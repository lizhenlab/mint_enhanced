"""Frozen protocols and cached scores reflect reference validity without rewriting history."""
import json

import pytest

from mint.comparison.research import ResearchRegistry
from mint.runtime import file_identity
from tests.test_platform_lifecycle import manager
from tests.test_reference_withdrawal import _with_reference, _withdraw


def _records(manager, tmp_path):
    comp, reference = _with_reference(manager, tmp_path)
    registry = ResearchRegistry(manager)
    protocol = registry.protocol(name='Frozen reference fixture', comparisons=[comp['id']])
    evaluation = registry.evaluate(protocol_id=protocol['id'], run_ids=[comp['run_ids'][0]])
    return registry, protocol, evaluation, reference


@pytest.mark.parametrize('kind', ['protocol', 'evaluation'])
def test_cached_registry_record_reports_withdrawal_without_rewriting_or_reading_arrays(manager, tmp_path, monkeypatch, kind):
    registry, protocol, evaluation, reference = _records(manager, tmp_path)
    original = protocol if kind == 'protocol' else evaluation
    with manager.db() as db:
        frozen = db.execute('SELECT payload FROM research_records WHERE id=?', (original['id'],)).fetchone()['payload']
    _withdraw(manager, reference)
    monkeypatch.setattr('mint.comparison.reference_validity.reference_digest',
                        lambda *a, **k: pytest.fail('Registry reads must not hash annotation files'))
    monkeypatch.setattr('numpy.load', lambda *a, **k: pytest.fail('Registry reads must not load numerical arrays'))
    for current in (registry.get(original['id']), registry.page(kind)['records'][0]):
        assert current['availability'] == 'annotation_withdrawn'
        assert 'mean pose' in current['reason']
        assert current['annotation_withdrawal']['replacement_run'] == 'a' * 64
        assert all(current[key] == value for key, value in original.items())
    with manager.db() as db:
        assert db.execute('SELECT payload FROM research_records WHERE id=?', (original['id'],)).fetchone()['payload'] == frozen


@pytest.mark.parametrize('change', ['missing_manifest', 'changed_manifest', 'missing_array', 'failed_run'])
def test_cached_registry_records_report_missing_or_changed_references(manager, tmp_path, change):
    registry, protocol, evaluation, reference = _records(manager, tmp_path)
    directory = manager.root / 'runs' / reference / 'result'
    if change == 'missing_manifest':
        (directory / 'result.json').unlink()
    elif change == 'changed_manifest':
        with (directory / 'result.json').open('a') as stream:
            stream.write(' ')
    elif change == 'missing_array':
        (directory / 'joints_camera_m.npy').unlink()
    else:
        manager._set(reference, 'failed', error='Fixture result failed validation')
        assert (directory / 'result.json').is_file()
    for original in (protocol, evaluation):
        for current in (registry.get(original['id']), registry.page(original['kind'])['records'][0]):
            assert current['availability'] == 'missing_or_changed' and current['reason']
            assert 'annotation_withdrawal' not in current
            assert all(current[key] == value for key, value in original.items())


def test_evaluation_checks_only_its_own_protocol_split(manager, tmp_path):
    comp, reference = _with_reference(manager, tmp_path)
    second = tmp_path / 'validation'; second.mkdir()
    validation, validation_reference = _with_reference(manager, second)
    registry = ResearchRegistry(manager)
    protocol = registry.protocol(name='Independent splits', comparisons=[comp['id'], validation['id']],
                                 splits={comp['id']: 'test', validation['id']: 'validation'})
    evaluation = registry.evaluate(protocol_id=protocol['id'], run_ids=[comp['run_ids'][0]], split='test')
    _withdraw(manager, validation_reference)
    assert registry.get(protocol['id'])['availability'] == 'annotation_withdrawn'
    assert registry.get(evaluation['id']) == evaluation
    assert registry.page('evaluation')['records'][0] == evaluation
    _withdraw(manager, reference)
    assert registry.get(evaluation['id'])['availability'] == 'annotation_withdrawn'


def test_evaluation_with_missing_protocol_remains_auditable_but_unavailable(manager):
    registry = ResearchRegistry(manager)
    original = registry.put('evaluation', {'name': 'Historical scores', 'protocol_id': 'missing',
                                         'split': 'test', 'status': 'completed', 'rows': [{'camera_absolute_joint_mm': 1.}]})
    for current in (registry.get(original['id']), registry.page('evaluation')['records'][0]):
        assert current['availability'] == 'missing_or_changed'
        assert current['status'] == 'completed' and current['rows'] == original['rows']


def test_explicit_protocol_withdrawal_invalidates_cached_evaluation(manager, tmp_path):
    registry, protocol, evaluation, _ = _records(manager, tmp_path)
    with manager.db() as db:
        db.execute('INSERT INTO research_withdrawals VALUES(?,?)',
                   (protocol['id'], json.dumps({'reason': 'Entire frozen protocol was withdrawn'})))
    for current in (registry.get(evaluation['id']), registry.page('evaluation')['records'][0]):
        assert current['availability'] == 'annotation_withdrawn'
        assert current['reason'] == 'Entire frozen protocol was withdrawn'
        assert current['rows'] == evaluation['rows'] and current['status'] == evaluation['status']


@pytest.mark.parametrize('kind', ['artifact', 'model'])
def test_explicit_withdrawal_stays_unavailable_when_artifact_file_exists(manager, tmp_path, kind):
    registry = ResearchRegistry(manager)
    path = tmp_path / 'retained-artifact.bin'; path.write_bytes(b'Immutable original')
    original = registry.put(kind, {'name': 'Retained but withdrawn', 'identity': file_identity(path)})
    with manager.db() as db:
        frozen = db.execute('SELECT payload FROM research_records WHERE id=?', (original['id'],)).fetchone()['payload']
        db.execute('INSERT INTO research_withdrawals VALUES(?,?)',
                   (original['id'], json.dumps({'reason': 'Reference convention was corrected'})))
    for current in (registry.get(original['id']), registry.page(kind)['records'][0]):
        assert current['availability'] == 'annotation_withdrawn'
        assert current['reason'] == 'Reference convention was corrected'
        assert current['identity'] == file_identity(path)
    with manager.db() as db:
        assert db.execute('SELECT payload FROM research_records WHERE id=?', (original['id'],)).fetchone()['payload'] == frozen
