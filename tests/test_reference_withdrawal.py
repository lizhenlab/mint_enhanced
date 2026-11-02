"""Withdrawn annotations remain auditable but cannot drive new comparisons or scores."""
import json
import shutil

import pytest

from mint.runtime import atomic_npz, file_identity
from mint.comparison.quality import QualityService
from mint.comparison.research import ResearchRegistry
from mint.comparison.diagnostics import run_breakdown
from tests.test_comparison import arrays, published
from tests.test_platform_lifecycle import manager, comparison
from tests.test_comparison_platform import annotated_episode


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


def _reimport_request(manager, ref):
    request = json.loads(manager.row(ref)['payload'])
    sample = request['sample_request']
    return {'source': sample['source']['path'], 'selections': [{'algorithm': 'reference'}],
            **{key: sample[key] for key in ('view', 'target_fps', 'max_frames', 'start_frame', 'camera')},
            'import_entry': {key: request['import'][key] for key in ('path', 'format', 'frame_map', 'declared_source')}}


@pytest.mark.parametrize('variant', ['provenance', 'fresh', 'copied_file'])
def test_withdrawn_reference_cannot_be_laundered_into_a_new_run(manager, tmp_path, variant):
    _, ref = _with_reference(manager, tmp_path)
    request = _reimport_request(manager, ref)
    _withdraw(manager, ref)
    if variant == 'provenance':
        request['import_entry']['declared_source'] = 'Renamed source declaration'
    elif variant == 'fresh':
        request['execution'] = {'cache': 'fresh'}
    else:
        copied = tmp_path / 'renamed-reference.npz'
        shutil.copyfile(request['import_entry']['path'], copied)
        request['import_entry']['path'] = str(copied)
    with manager.db() as db:
        count = db.execute('SELECT count(*) FROM runs').fetchone()[0]
    with pytest.raises(ValueError, match='withdrawn'):
        manager.create(**request)
    with manager.db() as db:
        assert db.execute('SELECT count(*) FROM runs').fetchone()[0] == count


@pytest.mark.parametrize('boundary', ['execute', 'resume', 'finish'])
def test_previously_admitted_reference_is_rechecked_before_execution_or_publication(manager, tmp_path, monkeypatch, boundary):
    _, ref = _with_reference(manager, tmp_path)
    request = _reimport_request(manager, ref)
    request['import_entry']['declared_source'] = 'Admitted before source withdrawal'
    pending = manager.create(**request)['run_ids'][0]
    _withdraw(manager, ref)
    if boundary == 'execute':
        monkeypatch.setattr('mint.comparison.prepare.prepare_sample', lambda *a, **k: pytest.fail('Withdrawn GT started RGB preparation'))
        manager._execute(pending)
    elif boundary == 'resume':
        manager._set(pending, 'interrupted')
        with pytest.raises(ValueError, match='withdrawn'):
            manager.resume(pending)
    else:
        from mint.runtime import atomic_json
        source = manager.run(ref)
        result = manager.root / 'runs' / pending / 'result'
        published(result, sample_id=source['sample_id'], run_id=pending, reference_role='user_declared_ground_truth')
        before = {p: file_identity(p) for p in result.iterdir()}
        atomic_json(result.parent / 'exit.json', {'status': 'completed'})
        manager._finish_from_disk(pending)
        assert {p: file_identity(p) for p in result.iterdir()} == before
    run = manager.run(pending)
    assert run['status'] == 'failed' and run['result'] is None
    assert 'mean pose' in run['annotation_withdrawal']['reason']


def test_withdrawn_content_remains_blocked_after_original_file_is_archived(manager, tmp_path):
    from mint.comparison.reference_validity import register_reference_withdrawal
    _, ref = _with_reference(manager, tmp_path)
    request = _reimport_request(manager, ref)
    prior = request['import_entry']['path']
    copied = tmp_path / 'old-copy.npz'; shutil.copyfile(prior, copied)
    register_reference_withdrawal(manager, prior, reason='Known wrong reference', source_run=ref)
    from pathlib import Path
    Path(prior).unlink()
    request['import_entry']['path'] = str(copied)
    with pytest.raises(ValueError, match='withdrawn'):
        manager.create(**request)
    corrected = tmp_path / 'corrected.npz'
    values = arrays(); values['joints_camera_m'] += .01
    atomic_npz(corrected, **values)
    request['import_entry']['path'] = str(corrected)
    assert manager.create(**request)['runs'][0]['status'] == 'queued'


def test_explicit_backfill_quarantines_existing_completed_copies_without_touching_outputs(manager, tmp_path):
    from mint.comparison.reference_validity import quarantine_withdrawn_imports
    comp, ref = _with_reference(manager, tmp_path)
    request = _reimport_request(manager, ref)
    copied = tmp_path / 'prior-copy.npz'; shutil.copyfile(request['import_entry']['path'], copied)
    request['import_entry']['path'] = str(copied)
    alias = manager.create(**request)['run_ids'][0]
    output = manager.root / 'runs' / alias / 'result'
    published(output, sample_id=comp['sample_id'], run_id=alias, reference_role='user_declared_ground_truth')
    manager._set(alias, 'completed')
    before = {p: file_identity(p) for p in output.iterdir()}
    _withdraw(manager, ref)
    assert quarantine_withdrawn_imports(manager) == [alias]
    assert manager.run(alias)['result'] is None
    assert {p: file_identity(p) for p in output.iterdir()} == before
    assert quarantine_withdrawn_imports(manager) == []


@pytest.mark.parametrize('attach', [False, True])
def test_legacy_arctic_mano_is_not_available_as_lerobot_gt_but_video_stays_usable(annotated_episode, attach):
    from mint.comparison import datasets
    root, _, _ = annotated_episode
    info_path = root / 'meta/info.json'
    info = json.loads(info_path.read_text()); info['hand_supervision'] = 'mano'
    info_path.write_text(json.dumps(info))
    (root / 'source_manifest.json').write_text(json.dumps({'label_origin': {'native_dataset': 'arctic'}}))
    assert not datasets.catalog(root)['episodes'][0]['annotation']['ready']
    spec = {'root': str(root), 'episode': 2, 'attach_gt': attach}
    if attach:
        with pytest.raises(ValueError, match='重新准备官方 GT'):
            datasets.select_episode(spec)
    else:
        assert datasets.select_episode(spec)[3]['episode_index'] == 2


def test_true_arctic_joint_reference_without_incompatible_mano_remains_available(annotated_episode):
    from mint.comparison import datasets
    root, _, _ = annotated_episode
    (root / 'source_manifest.json').write_text(json.dumps({'label_origin': {'native_dataset': 'arctic'}}))
    assert datasets.select_episode({'root': str(root), 'episode': 2, 'attach_gt': True})[-1]['ready']


def test_withdrawn_parquet_copy_without_manifest_is_rejected_before_any_prediction(manager, annotated_episode, monkeypatch):
    from mint.comparison.preflight import inspect_request
    from mint.comparison.reference_validity import register_reference_withdrawal
    root, episode, _ = annotated_episode
    register_reference_withdrawal(manager, episode['parquet'], reason='Withdrawn legacy ARCTIC MANO export')
    copied = root / 'copied-labels.parquet'
    shutil.copyfile(episode['parquet'], copied)
    episode['parquet'] = str(copied)
    assert not (root / 'source_manifest.json').exists()
    spec = {'root': str(root), 'episode': 2, 'attach_gt': True}
    monkeypatch.setattr('mint.comparison.preflight.probe_video', lambda *_: pytest.fail('Withdrawn reference reached RGB preflight'))
    with pytest.raises(ValueError, match='withdrawn'):
        inspect_request(manager, '', [{'algorithm': 'mint'}], dataset=spec)
    with manager.db() as db:
        before = db.execute('SELECT count(*) FROM runs').fetchone()[0]
    with pytest.raises(ValueError, match='withdrawn'):
        manager.create('', [{'algorithm': 'mint'}], dataset=spec)
    with manager.db() as db:
        assert db.execute('SELECT count(*) FROM runs').fetchone()[0] == before


def test_collection_rejects_withdrawn_reference_before_creating_predictions(manager, tmp_path, monkeypatch):
    from mint.comparison.collections import CollectionManager
    from mint.comparison.preflight import inspect_collection
    from tests.test_comparison import fake_backend
    _, ref = _with_reference(manager, tmp_path)
    request = _reimport_request(manager, ref)
    item = {'source': request['source'], 'reference': request['import_entry']}
    monkeypatch.setattr('mint.comparison.collections.backend_spec', fake_backend)
    monkeypatch.setattr('mint.comparison.preflight.backend_spec', fake_backend)
    monkeypatch.setattr('mint.comparison.preflight.probe_video', lambda _: {'frames': 20, 'fps': 30., 'width': 640, 'height': 480})
    _withdraw(manager, ref)
    service = CollectionManager(manager, autostart=False)
    with pytest.raises(ValueError, match='withdrawn'):
        service.create(kind='evaluation', items=[item], selections=[{'algorithm': 'mint'}])
    preflight = inspect_collection(manager, kind='evaluation', items=[item], selections=[{'algorithm': 'mint'}])
    assert preflight['ready'] is False and any('withdrawn' in error for error in preflight['errors'])
    service.close()


def test_already_queued_collection_checks_gt_before_admitting_any_child(manager, tmp_path, monkeypatch):
    from mint.comparison.collections import CollectionManager
    from tests.test_comparison import fake_backend
    _, ref = _with_reference(manager, tmp_path)
    request = _reimport_request(manager, ref)
    monkeypatch.setattr('mint.comparison.collections.backend_spec', fake_backend)
    service = CollectionManager(manager, autostart=False)
    collection = service.create(kind='evaluation', items=[{'source': request['source'], 'reference': request['import_entry']}],
                                selections=[{'algorithm': 'mint'}])
    _withdraw(manager, ref)
    monkeypatch.setattr(manager, 'create', lambda **_: pytest.fail('Withdrawn GT admitted a prediction child'))
    service._execute(collection['id'])
    saved = service.get(collection['id'])
    assert saved['status'] == 'failed' and 'withdrawn' in saved['items'][0]['error']
    assert not saved['items'][0].get('comparison_id')
    service.close()


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


def _completed_reference_alias(manager, comp, ref, *, copied=None):
    request = _reimport_request(manager, ref)
    request['import_entry']['declared_source'] = 'Previously admitted reference alias'
    if copied:
        shutil.copyfile(request['import_entry']['path'], copied)
        request['import_entry']['path'] = str(copied)
    alias = manager.create(**request)['run_ids'][0]
    published(manager.root / 'runs' / alias / 'result', sample_id=comp['sample_id'],
              run_id=alias, reference_role='user_declared_ground_truth')
    manager._set(alias, 'completed')
    return alias


@pytest.mark.parametrize('copied', [False, True])
def test_registering_withdrawal_immediately_hides_completed_aliases_across_managers(manager, tmp_path, copied):
    from mint.comparison.jobs import ComparisonManager
    from mint.comparison.reference_validity import register_reference_withdrawal
    comp, ref = _with_reference(manager, tmp_path)
    alias = _completed_reference_alias(manager, comp, ref,
        copied=tmp_path / 'copied-reference.npz' if copied else None)
    output = manager.root / 'runs' / alias / 'result'
    before = {path: file_identity(path) for path in output.iterdir()}
    reader = ComparisonManager(manager.root, autostart=False)
    try:
        entry = _reimport_request(manager, ref)['import_entry']
        register_reference_withdrawal(manager, entry['path'], reason='Known incorrect GT', source_run=ref)
        for run_id in (ref, alias):
            assert reader.run(run_id)['result'] is None
            assert reader.run(run_id)['status'] == 'failed'
            with pytest.raises(ValueError, match='withdrawn'):
                reader.attach(comp['id'], run_id)
        assert {path: file_identity(path) for path in output.iterdir()} == before
        assert all(reader.run(run_id)['result'] for run_id in comp['run_ids'] if run_id != ref)
    finally:
        reader.close()


@pytest.mark.parametrize('recover_exit', [False, True])
def test_startup_backfill_hides_old_completed_and_recovered_reference_aliases(manager, tmp_path, recover_exit):
    from mint.runtime import atomic_json
    from mint.comparison.jobs import ComparisonManager
    comp, ref = _with_reference(manager, tmp_path)
    alias = _completed_reference_alias(manager, comp, ref, copied=tmp_path / 'old-reference-copy.npz')
    _withdraw(manager, ref)
    if recover_exit:
        manager._set(alias, 'launching')
        atomic_json(manager.root / 'runs' / alias / 'exit.json', {'status': 'completed'})
    recovered = ComparisonManager(manager.root, autostart=False)
    try:
        assert recovered.row(alias)['status'] == 'failed'
        assert recovered.run(alias)['result'] is None
        assert 'mean pose' in recovered.run(alias)['annotation_withdrawal']['reason']
        assert recovered.run(comp['run_ids'][0])['result']
    finally:
        recovered.close()


def test_runtime_legacy_backfill_quarantines_existing_aliases_without_registration_recursion(manager, tmp_path, monkeypatch):
    from mint.comparison import reference_validity
    comp, ref = _with_reference(manager, tmp_path)
    alias = _completed_reference_alias(manager, comp, ref)
    _withdraw(manager, ref)
    monkeypatch.setattr(reference_validity, 'register_reference_withdrawal',
        lambda *a, **kw: pytest.fail('Backfill must not recurse into public registration'))
    reference_validity.refresh_reference_withdrawals(manager)
    assert manager.run(alias)['result'] is None


def test_registration_hashes_outside_write_transactions_and_viewing_never_rehashes(manager, tmp_path, monkeypatch):
    from mint.comparison import reference_validity
    from mint.comparison.results import frame_payload
    from tests.test_comparison import sample
    comp, ref = _with_reference(manager, tmp_path)
    _completed_reference_alias(manager, comp, ref, copied=tmp_path / 'copy-to-validate.npz')
    path = _reimport_request(manager, ref)['import_entry']['path']
    original = reference_validity.reference_digest
    reads = []
    def checked_digest(*args, **kwargs):
        # Another connection must be able to claim the write lock while a
        # reference is being read, even during startup/backfill admission.
        with manager.db() as db:
            db.execute('PRAGMA busy_timeout=30')
            db.execute('BEGIN IMMEDIATE')
        reads.append(str(args[0]))
        return original(*args, **kwargs)
    monkeypatch.setattr(reference_validity, 'reference_digest', checked_digest)
    reference_validity.register_reference_withdrawal(manager, path, reason='Incorrect source', source_run=ref)
    assert len(reads) == 2  # Original registration and one copied source.
    monkeypatch.setattr(reference_validity, 'reference_digest',
        lambda *a, **kw: pytest.fail('Viewing or attaching must not hash annotation files'))
    assert manager.run(ref)['result'] is None
    assert manager.run(ref, summary=True)['result'] is None
    with pytest.raises(ValueError, match='withdrawn'):
        manager.attach(comp['id'], ref)
    prediction = comp['run_ids'][0]
    assert manager.run(prediction)['result']
    assert len(frame_payload(manager.root / 'runs' / prediction / 'result', sample(), count=2)['sample_positions']) == 2
