"""Annotation withdrawal follows file content, not an import label or Run ID.

Checks happen at import/preflight/execution boundaries. Interactive frame reads
keep using immutable result identities and never hash whole reference files.
"""
from __future__ import annotations

import hashlib
import json
import time
from pathlib import Path

from mint.runtime import file_identity


class ReferenceWithdrawn(ValueError):
    def __init__(self, withdrawal):
        self.withdrawal = withdrawal
        super().__init__('This reference was withdrawn: ' + withdrawal['reason'])


def ensure_reference_withdrawals(manager):
    with manager.db() as db:
        db.execute('CREATE TABLE IF NOT EXISTS reference_artifact_withdrawals '
                   '(sha256 TEXT PRIMARY KEY, payload TEXT NOT NULL)')


def reference_digest(path, *, identity=None):
    path = Path(path).expanduser().resolve()
    before = file_identity(path)
    if before.get('missing') or identity is not None and before != identity:
        raise ValueError('Reference annotations are unavailable or changed')
    checksum = hashlib.sha256()
    with path.open('rb') as stream:
        for block in iter(lambda: stream.read(2 * 2**20), b''):
            checksum.update(block)
    if file_identity(path) != before:
        raise ValueError('Reference annotations changed during validation')
    return checksum.hexdigest(), before


def _store_reference_withdrawal(manager, checksum, frozen, *, reason, source_run=None):
    """Commit already-validated evidence without hashing inside a transaction."""
    ensure_reference_withdrawals(manager)
    value = {'reason': reason, 'source_run': source_run, 'identity': frozen,
             'sha256': checksum, 'withdrawn_at': time.time()}
    with manager.db() as db:
        created = db.execute('INSERT OR IGNORE INTO reference_artifact_withdrawals VALUES(?,?)',
                             (checksum, json.dumps(value))).rowcount
        saved = db.execute('SELECT payload FROM reference_artifact_withdrawals WHERE sha256=?',
                           (checksum,)).fetchone()
    return json.loads(saved['payload']), bool(created)


def register_reference_withdrawal(manager, path, *, reason, source_run=None, identity=None):
    """Withdraw the artifact and all admitted aliases before returning."""
    if not isinstance(reason, str) or not reason.strip():
        raise ValueError('A reference withdrawal needs a reason')
    checksum, frozen = reference_digest(path, identity=identity)
    saved, _ = _store_reference_withdrawal(manager, checksum, frozen,
                                          reason=reason, source_run=source_run)
    _quarantine_reference_imports(manager)
    return saved


def refresh_reference_withdrawals(manager):
    """Upgrade pre-existing Run-ID withdrawals without altering their files."""
    ensure_reference_withdrawals(manager)
    with manager.db() as db:
        rows = db.execute("SELECT id,payload FROM runs WHERE json_extract(payload,'$.algorithm')='reference' "
                          "AND json_type(payload,'$.annotation_withdrawal')='object'").fetchall()
        registered = [json.loads(row['payload']) for row in
                      db.execute('SELECT payload FROM reference_artifact_withdrawals')]
    known = [row['identity'] for row in registered]
    changed = False
    for row in rows:
        request = json.loads(row['payload'])
        entry = request.get('import', {})
        identity = entry.get('identity')
        if not identity or identity in known or entry.get('format') not in {'normalized_npz', 'lerobot_reference'}:
            continue
        # Do not attribute changed bytes to an older withdrawal. Exact identity
        # still fails when someone tries to execute that older frozen import.
        if file_identity(entry.get('path', '')) != identity:
            continue
        checksum, frozen = reference_digest(entry['path'], identity=identity)
        _, created = _store_reference_withdrawal(manager, checksum, frozen,
            reason=request['annotation_withdrawal']['reason'], source_run=row['id'])
        known.append(identity)
        changed |= created
    # This path also handles legacy Run-ID withdrawals discovered while the
    # service is running. The internal pass never calls register or refresh.
    return _quarantine_reference_imports(manager) if changed else []


def assert_reference_import_current(manager, entry, *, checksum=None):
    """Reject copies, renamed provenance and fresh-nonce imports of bad GT."""
    refresh_reference_withdrawals(manager)
    with manager.db() as db:
        if not db.execute('SELECT 1 FROM reference_artifact_withdrawals LIMIT 1').fetchone():
            return
    identity = entry.get('identity')
    # Collection identities include their content digest; file_identity does not.
    if identity and 'sha256' in identity:
        identity = {key: value for key, value in identity.items() if key != 'sha256'}
    if checksum is None:
        checksum, _ = reference_digest(entry['path'], identity=identity)
    elif identity is not None and file_identity(entry['path']) != identity:
        raise ValueError('Reference annotations changed during validation')
    with manager.db() as db:
        row = db.execute('SELECT payload FROM reference_artifact_withdrawals WHERE sha256=?',
                         (checksum,)).fetchone()
    if row:
        raise ReferenceWithdrawn(json.loads(row['payload']))


def withdraw_reference_run(manager, run_id, withdrawal):
    """Keep immutable numeric output, but stop exposing it as usable GT."""
    with manager.db() as db:
        row = db.execute('SELECT payload FROM runs WHERE id=?', (run_id,)).fetchone()
        request = json.loads(row['payload'])
        if request['algorithm'] != 'reference':
            raise ValueError('Annotation withdrawal applies only to references')
        request.setdefault('annotation_withdrawal', withdrawal)
        db.execute("UPDATE runs SET payload=?,status='failed',error=?,updated=? WHERE id=?",
                   (json.dumps(request), request['annotation_withdrawal']['reason'], time.time(), run_id))


def _quarantine_reference_imports(manager):
    """One admission-boundary pass; no recursive backfill and no read-path hash."""
    with manager.db() as db:
        withdrawals = {row['sha256']: json.loads(row['payload']) for row in
                       db.execute('SELECT sha256,payload FROM reference_artifact_withdrawals')}
        if not withdrawals:
            return []
        rows = db.execute("SELECT id,payload FROM runs WHERE json_extract(payload,'$.algorithm')='reference'").fetchall()
    # Several aliases/Episodes can share one source file. Hash it once per pass.
    checked = {json.dumps(value['identity'], sort_keys=True): checksum
               for checksum, value in withdrawals.items()}
    affected = []
    for row in rows:
        request = json.loads(row['payload'])
        if request.get('annotation_withdrawal') or not request.get('import'):
            continue
        entry = request['import']
        identity = entry.get('identity')
        if identity and 'sha256' in identity:
            identity = {key: value for key, value in identity.items() if key != 'sha256'}
        try:
            current = file_identity(entry['path'])
            if current.get('missing') or identity is not None and current != identity:
                continue
            key = json.dumps(current, sort_keys=True)
            if key not in checked:
                checked[key], _ = reference_digest(entry['path'], identity=current)
            withdrawal = withdrawals.get(checked[key])
            if withdrawal:
                withdraw_reference_run(manager, row['id'], withdrawal)
                affected.append(row['id'])
        except (OSError, ValueError):
            # Missing or changed sources have their normal immutable-input gate;
            # they are not evidence that new bytes equal a withdrawn reference.
            continue
    return affected


def quarantine_withdrawn_imports(manager):
    """One startup/explicit migration for older completed or recovered aliases."""
    affected = refresh_reference_withdrawals(manager)
    return affected + _quarantine_reference_imports(manager)


def assert_dataset_reference_current(root):
    """Legacy ARCTIC MANO exports are invalid as GT as well as for training."""
    from .training_data import mano_training_schema_errors
    errors = mano_training_schema_errors(root)
    if errors:
        raise ReferenceWithdrawn({'reason': '数据集参考标注不可用，请重新准备官方 GT：' + '；'.join(errors),
                                  'dataset_root': str(Path(root).expanduser().resolve())})


def assert_reference_request_current(manager, request):
    if request['algorithm'] != 'reference':
        return
    if request.get('annotation_withdrawal'):
        raise ReferenceWithdrawn(request['annotation_withdrawal'])
    entry = request.get('import')
    if not entry:
        return
    if entry.get('format') == 'lerobot_reference':
        assert_dataset_reference_current(entry['dataset_context']['selection']['root'])
    assert_reference_import_current(manager, entry)
