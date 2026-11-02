"""Withdrawn annotations remain auditable but cannot drive new comparisons or scores."""
import json

import pytest

from mint.runtime import atomic_npz, file_identity
from mint.comparison.quality import QualityService
from mint.comparison.research import ResearchRegistry
from mint.comparison.diagnostics import run_breakdown
from tests.test_comparison import arrays, published
from tests.test_platform_lifecycle import manager, comparison


def _with_reference(manager, tmp_path):
    comp = comparison(manager, tmp_path)
    native = tmp_path / 'official-reference.npz'
    atomic_npz(native, **arrays())
    request = comp['sample_request']
    imported = manager.create(request['source']['path'], [{'algorithm': 'reference'}],
        **{key: request[key] for key in ('view', 'target_fps', 'max_frames', 'start_frame', 'camera')},
        import_entry={'path': str(native), 'format': 'normalized_npz', 'frame_map': list(range(5)),
                      'declared_source': 'Official annotation; OpenCV camera coordinates in metres'})
    run_id = imported['run_ids'][0]
    published(manager.root / 'runs' / run_id / 'result', sample_id=comp['sample_id'], run_id=run_id,
              reference_role='user_declared_ground_truth', backend_version='reference-v1')
    manager._set(run_id, 'completed')
    return manager.attach(comp['id'], run_id), run_id


def _withdraw(manager, run_id):
    payload = json.loads(manager.row(run_id)['payload'])
    payload['annotation_withdrawal'] = {'reason': 'ARCTIC MANO mean pose was omitted; use corrected reference',
                                      'replacement_run': 'a' * 64}
    with manager.db() as db:
        db.execute('UPDATE runs SET payload=? WHERE id=?', (json.dumps(payload), run_id))


def test_dataset_withdrawal_blocks_training_without_rewriting_frozen_registry_payload(manager):
    registry = ResearchRegistry(manager)
    saved = registry.put('dataset', {'name': 'Legacy ARCTIC export', 'training_ready': True, 'root': '/owned/legacy'})
    with manager.db() as db:
        frozen = db.execute('SELECT payload FROM research_records WHERE id=?', (saved['id'],)).fetchone()['payload']
        db.execute('INSERT INTO research_withdrawals VALUES(?,?)',
                   (saved['id'], json.dumps({'reason': 'ARCTIC MANO convention mismatch'})))
    current = registry.get(saved['id'], 'dataset')
    assert current['training_ready'] is False and current['availability'] == 'annotation_withdrawn'
    listed = registry.page('dataset')['records'][0]
    assert listed['training_ready'] is False and listed['annotation_withdrawal'] == current['annotation_withdrawal']
    with manager.db() as db:
        assert db.execute('SELECT payload FROM research_records WHERE id=?', (saved['id'],)).fetchone()['payload'] == frozen


@pytest.mark.parametrize('summary', [False, True])
def test_withdrawn_reference_hides_geometry_and_preserves_immutable_files(manager, tmp_path, summary):
    comp, ref = _with_reference(manager, tmp_path)
    directory = manager.root / 'runs' / ref / 'result'
    identities = {path: file_identity(path) for path in directory.iterdir()}
    _withdraw(manager, ref)
    value = manager.run(ref, summary=summary)
    assert value['status'] == 'failed' and value['result'] is None
    assert 'mean pose' in value['error']
    assert value['annotation_withdrawal']['replacement_run'] == 'a' * 64
    assert {path: file_identity(path) for path in directory.iterdir()} == identities
    retained = manager.comparison(comp['id'])
    assert [r['id'] for r in retained['runs']] == comp['run_ids']
    assert all(r['result'] for r in retained['runs'] if r['id'] != ref)


def test_withdrawn_reference_cannot_be_attached_or_resumed(manager, tmp_path):
    comp, ref = _with_reference(manager, tmp_path)
    _withdraw(manager, ref)
    with pytest.raises(ValueError, match='withdrawn'):
        manager.attach(comp['id'], ref)
    with pytest.raises(ValueError, match='withdrawn'):
        manager.resume(ref)
    assert manager.row(ref)['status'] == 'completed'  # no new execution was queued


def test_old_frozen_protocol_cannot_keep_scoring_a_withdrawn_reference(manager, tmp_path):
    comp, ref = _with_reference(manager, tmp_path)
    registry = ResearchRegistry(manager)
    protocol = registry.protocol(name='Before correction', comparisons=[comp['id']])
    _withdraw(manager, ref)
    with pytest.raises(ValueError, match='unavailable or withdrawn'):
        registry.evaluate(protocol_id=protocol['id'], run_ids=[comp['run_ids'][1]])
    with pytest.raises(ValueError, match='exactly one explicit GT'):
        registry.protocol(name='Cannot freeze withdrawn GT', comparisons=[comp['id']])


def test_quality_and_breakdown_reject_withdrawn_reference(manager, tmp_path):
    comp, ref = _with_reference(manager, tmp_path)
    _withdraw(manager, ref)
    with pytest.raises(ValueError, match='published results'):
        QualityService(manager).create(comp['run_ids'][0], reference_run=ref)
    with pytest.raises(ValueError, match='published results'):
        run_breakdown(manager, comp['run_ids'][0], ref)


def test_saved_quality_curves_are_invalidated_when_the_reference_is_withdrawn(manager, tmp_path):
    comp, ref = _with_reference(manager, tmp_path)
    service = QualityService(manager)
    saved = service.create(comp['run_ids'][0], reference_run=ref)
    assert saved['paired_frames'] == 5
    _withdraw(manager, ref)
    with pytest.raises(ValueError, match='Diagnostic source changed'):
        service.get(saved['id'])


def test_nonreference_results_are_not_withdrawn_by_annotation_metadata(manager, tmp_path):
    comp = comparison(manager, tmp_path)
    run_id = comp['run_ids'][0]
    _withdraw(manager, run_id)
    for summary in (False, True):
        value = manager.run(run_id, summary=summary)
        assert value['status'] == 'completed' and value['result']
        assert 'annotation_withdrawal' not in value
    assert manager.resume(run_id)['result']
    assert manager.attach(comp['id'], run_id)['run_ids'] == comp['run_ids']


def _evaluated_model(manager, tmp_path, monkeypatch):
    comp, ref = _with_reference(manager, tmp_path)
    checkpoint = tmp_path / 'checkpoints/model.safetensors'
    checkpoint.parent.mkdir()
    checkpoint.write_bytes(b'registered checkpoint fixture')
    configuration = tmp_path / 'configs/training/mint_step2.yaml'
    configuration.parent.mkdir(parents=True)
    configuration.write_text('model: {}\ndata: {clip_len: 32, size_hw: [378,518]}\n')
    monkeypatch.setattr('mint.comparison.research.PROJECT', tmp_path)
    registry = ResearchRegistry(manager)
    protocol = registry.protocol(name='Before GT correction', comparisons=[comp['id']])
    evaluation = registry.evaluate(protocol_id=protocol['id'], run_ids=[comp['run_ids'][0]])
    model = registry.register_model(path=checkpoint)
    assert evaluation['rows'][0]['camera_absolute_joint_mm'] == 0
    return registry, model, evaluation, ref


def test_historic_evaluation_cannot_promote_a_model_after_gt_withdrawal(manager, tmp_path, monkeypatch):
    registry, model, evaluation, ref = _evaluated_model(manager, tmp_path, monkeypatch)
    _withdraw(manager, ref)
    with pytest.raises(ValueError, match='unavailable or withdrawn'):
        registry.promote(model_id=model['id'], evaluation_id=evaluation['id'], max_joint_mm=.01, min_coverage=1.)
    assert registry.aliases() == []


def test_existing_model_alias_becomes_unavailable_with_a_specific_withdrawal_reason(manager, tmp_path, monkeypatch):
    registry, model, evaluation, ref = _evaluated_model(manager, tmp_path, monkeypatch)
    registry.promote(model_id=model['id'], evaluation_id=evaluation['id'], max_joint_mm=.01, min_coverage=1.)
    assert registry.aliases()[0]['ready']
    _withdraw(manager, ref)
    alias = registry.aliases()[0]
    assert not alias['ready'] and 'mean pose' in alias['reason']
    assert alias['model_id'] == model['id'] and alias['evaluation_id'] == evaluation['id']
