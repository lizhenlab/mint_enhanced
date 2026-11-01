"""Reject repeated frozen inputs instead of silently reducing evaluation counts."""
import json
from unittest.mock import patch

from mint.runtime import file_identity
from tests.test_study_recovery_roles import _platform, _protocol, _publish


def _duplicate_inputs(manager, registry, tmp_path):
    original = _protocol(manager, registry, tmp_path)
    entry = original['entries'][0]
    second = manager.create(entry['sample_request']['source']['path'],
                            [{'algorithm': 'mint', 'options': {'hand_mode': 'hard'}}])
    _publish(manager, second['run_ids'][0])
    manager.attach(second['id'], entry['reference_run'])
    return original, second, [entry, {**entry, 'comparison_id': second['id']}]


def _rejected(action):
    try:
        action()
    except ValueError as error:
        assert '同一输入' in str(error) and '一份比较' in str(error)
    else:
        raise AssertionError('Repeated frozen sample IDs must be rejected explicitly')


def test_new_protocol_rejects_distinct_histories_with_the_same_sample(tmp_path):
    with _platform(tmp_path) as (manager, registry, studies):
        original, second, entries = _duplicate_inputs(manager, registry, tmp_path)
        identities = [file_identity(manager.root / 'runs' / key / 'result/result.json')
                      for key in (entries[0]['reference_run'], second['run_ids'][0])]
        _rejected(lambda: registry.protocol(name='duplicate input histories',
                  comparisons=[entries[0]['comparison_id'], second['id']]))
        assert registry.page('protocol')['total'] == 1
        assert registry.get(original['id'], 'protocol') == original
        assert [file_identity(identity['path']) for identity in identities] == identities


def test_legacy_duplicate_protocol_stays_readable_but_cannot_silently_shrink_evaluation(tmp_path):
    with _platform(tmp_path) as (manager, registry, studies):
        _, second, entries = _duplicate_inputs(manager, registry, tmp_path)
        legacy = registry.put('protocol', {'name': 'legacy duplicate protocol', 'entries': entries})
        with patch.object(manager, 'run', side_effect=AssertionError('Reject before looking up predictions')):
            _rejected(lambda: registry.evaluate(protocol_id=legacy['id'], run_ids=second['run_ids']))
        assert registry.get(legacy['id'], 'protocol') == legacy
        assert len(registry.get(legacy['id'])['entries']) == 2
        assert registry.page('evaluation')['total'] == 0


def test_regression_submission_rejects_legacy_duplicate_protocol_before_child_jobs(tmp_path):
    with _platform(tmp_path) as (manager, registry, studies):
        _, _, entries = _duplicate_inputs(manager, registry, tmp_path)
        legacy = registry.put('protocol', {'name': 'legacy duplicate protocol', 'entries': entries})
        with patch.object(manager, 'create', side_effect=AssertionError('Do not submit duplicate input jobs')):
            _rejected(lambda: studies.create(kind='regression', protocol_id=legacy['id'],
                                             selections=[{'algorithm': 'mint'}]))
        with manager.db() as db:
            assert db.execute('SELECT COUNT(*) FROM studies').fetchone()[0] == 0
        assert registry.get(legacy['id'], 'protocol') == legacy


def test_saved_duplicate_study_is_rejected_before_execution_or_resume(tmp_path):
    with _platform(tmp_path) as (manager, registry, studies):
        original, _, entries = _duplicate_inputs(manager, registry, tmp_path)
        job = studies.create(kind='regression', protocol_id=original['id'], selections=[{'algorithm': 'mint'}])
        request = job['request']
        request['cases'] = entries
        with manager.db() as db:
            db.execute('UPDATE studies SET payload=? WHERE id=?', (json.dumps(request), job['id']))
        with patch.object(manager, 'create', side_effect=AssertionError('Do not execute duplicate input jobs')):
            _rejected(lambda: studies._execute(job['id']))
        studies._set(job['id'], status='interrupted')
        with patch.object(manager, 'resume', side_effect=AssertionError('Reject before resuming child jobs')):
            _rejected(lambda: studies.resume(job['id']))
        assert studies.get(job['id'])['request']['cases'] == entries
        assert registry.get(original['id'], 'protocol') == original
