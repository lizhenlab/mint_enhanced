"""Recovery and scientific-protocol boundaries for durable platform services."""
import json
import os
import time
from pathlib import Path
from types import SimpleNamespace

import numpy as np
import pytest

from mint.runtime import atomic_json, file_identity
from mint.comparison.jobs import ComparisonManager
from mint.comparison.exports import ComparisonExports
from mint.comparison.quality import QualityService, frame_diagnostics
from mint.comparison.research import ResearchRegistry
from mint.comparison.task_index import TaskIndex
from tests.test_comparison import fake_backend, published, arrays
from tests.test_comparison_platform import annotated_episode


@pytest.fixture
def manager(tmp_path, monkeypatch):
    monkeypatch.setattr('mint.comparison.jobs.backend_spec', fake_backend)
    value = ComparisonManager(tmp_path / 'store', autostart=False)
    yield value
    value.close()


def comparison(manager, tmp_path, *, reference=False, execution=None, options=None, start=0):
    source = tmp_path / 'source.mp4'
    if not source.exists():
        source.write_bytes(b'video fixture')
    comp = manager.create(str(source), [{'algorithm': 'mint', 'options': options or {}}, {'algorithm': 'hawor_camera'}],
                          execution=execution, start_frame=start)
    for index, key in enumerate(comp['run_ids']):
        directory = manager.root / 'runs' / key / 'result'
        if not (directory / 'result.json').exists():
            published(directory, sample_id=comp['sample_id'], run_id=key, backend_version='mint-v1' if index == 0 else 'hawor_camera-v1',
                      **({'reference_role': 'user_declared_ground_truth'} if reference and index == 1 else {}))
        manager._set(key, 'completed')
    return manager.comparison(comp['id'])


def test_export_request_survives_dead_owner_and_cancel_is_not_replayed(manager, tmp_path):
    comp = comparison(manager, tmp_path)
    # Real sample is unnecessary for testing the admission/recovery boundary.
    sample = {'request': comp['sample_request'], 'frames_path': str(tmp_path / 'frames.npy')}
    atomic_json(manager.root / 'inputs' / comp['sample_id'] / 'sample.json', sample)
    exports = ComparisonExports(manager, autostart=False)
    job = exports.start(comp['id'])
    request = json.loads((exports.directory / (job['id'] + '.request.json')).read_text())
    assert request['identity']['runs'] == comp['run_ids']
    exports._write(job['id'], status='rendering', owner_pid=999999999, owner_token='dead')
    recovered = ComparisonExports(manager, autostart=False)
    assert recovered.status(job['id'])['status'] == 'queued'
    recovered.cancel(job['id'])
    assert ComparisonExports(manager, autostart=False).status(job['id'])['status'] == 'cancelled'
    assert recovered.resume(job['id'])['status'] == 'queued'
    exports.close(); recovered.close()


def test_legacy_export_has_explicit_manual_recovery_and_completed_video_survives(manager):
    folder = manager.root / 'exports'; folder.mkdir()
    key = 'a' * 64
    atomic_json(folder / (key + '.json'), {'id': key, 'status': 'rendering'})
    exports = ComparisonExports(manager, autostart=False)
    assert exports.status(key)['status'] == 'interrupted'
    with pytest.raises(ValueError, match='Legacy'):
        exports.resume(key)
    atomic_json(folder / (key + '.json'), {'id': key, 'status': 'completed'})
    (folder / (key + '.mp4')).write_bytes(b'complete fixture')
    assert ComparisonExports(manager, autostart=False).status(key)['status'] == 'completed'


def test_index_retains_events_and_sql_pages_without_rewriting_executor_state(manager):
    index = TaskIndex(manager)
    row = {'id': 'one', 'kind': 'workflow', 'label': 'test', 'status': 'queued', 'updated': 1, 'progress': {}}
    index.sync([row])
    index.sync([{**row, 'status': 'running', 'updated': 2, 'progress': {'stage': 'training', 'done': 1, 'total': 2}}])
    assert index.page(status='active', limit=1)['tasks'][0]['attempt'] == 1
    index.sync([{**row, 'status': 'interrupted', 'updated': 3}])
    # Reopening the index preserves history even if a legacy executor forgets old snapshots.
    restored = TaskIndex(manager)
    assert len(restored.detail('workflow', 'one')['events']) == 3
    assert restored.page(status='attention')['total'] == 1
    assert restored.page(query='%')['total'] == 0


def test_compatible_cache_references_original_run_and_fresh_recomputes(manager, tmp_path):
    first = comparison(manager, tmp_path, options={'max_gpus': 1})
    source = first['sample_request']['source']['path']
    requested = manager.create(source, [{'algorithm': 'mint', 'options': {'max_gpus': 8}}], execution={'cache': 'compatible'})
    assert requested['run_ids'] == [first['run_ids'][0]] and not requested['created_run_ids']
    assert requested['reuse'][first['run_ids'][0]]['timing_scope'].startswith('cached_result')
    changed = manager.create(source, [{'algorithm': 'mint', 'options': {'hand_mode': 'hard'}}], execution={'cache': 'compatible'})
    assert changed['run_ids'][0] != first['run_ids'][0]
    fresh = manager.create(source, [{'algorithm': 'mint', 'options': {'max_gpus': 1}}], execution={'cache': 'fresh'})
    assert fresh['run_ids'][0] != first['run_ids'][0]


def test_preflight_catches_short_ace_empty_ranges_and_keeps_full_frame_default(manager, tmp_path, monkeypatch):
    from mint.comparison.preflight import inspect_request
    monkeypatch.setattr('mint.comparison.preflight.backend_spec', fake_backend)
    monkeypatch.setattr('mint.comparison.preflight.probe_video', lambda _: {'frames': 10428, 'fps': 36., 'width': 3248, 'height': 1232})
    monkeypatch.setenv('MINT_CACHE_MIN_FREE_GIB', '0')
    source = tmp_path / 'v.mp4'; source.write_bytes(b'fixture')
    all_frames = inspect_request(manager, str(source), [{'algorithm': 'mint'}], view='left')
    assert all_frames['frames'] == 10428 and all_frames['width'] == 1624
    short = inspect_request(manager, str(source), [{'algorithm': 'ace_kfree'}], max_frames=17)
    assert not short['ready'] and '81' in ' '.join(short['errors'])
    empty = inspect_request(manager, str(source), [{'algorithm': 'mint'}], start_frame=20000)
    assert not empty['ready'] and empty['frames'] == 0


def test_collection_preflight_checks_all_inputs_and_reuses_capability_inventory(manager, tmp_path, monkeypatch):
    from mint.comparison.preflight import inspect_collection
    calls = []
    def backend(name):
        calls.append(name); return fake_backend(name)
    monkeypatch.setattr('mint.comparison.preflight.backend_spec', backend)
    monkeypatch.setattr('mint.comparison.preflight.probe_video', lambda _: {'frames': 17, 'fps': 30., 'width': 128, 'height': 64})
    source = tmp_path / 'v.mp4'; source.write_bytes(b'fixture')
    check = inspect_collection(manager, items=[{'source': str(source)}] * 3, selections=[{'algorithm': 'ace_kfree'}])
    assert not check['ready'] and len(check['errors']) == 3 and check['total_frames'] == 51
    assert calls == ['ace_kfree']
    with manager.db() as db:
        assert db.execute('SELECT count(*) FROM runs').fetchone()[0] == 0


def test_frame_quality_keeps_missing_data_and_does_not_call_discrepancy_accuracy(manager, tmp_path):
    reference = arrays(world=False)
    prediction = {key: value.copy() for key, value in reference.items()}
    prediction['joints_camera_m'] += .01
    prediction['geometry_valid'][2] = False; prediction['joints_camera_m'][2] = np.nan
    curves = frame_diagnostics(prediction, reference)
    assert curves['joint_difference_mm'][0] == pytest.approx(np.sqrt(3) * 10, rel=1e-5)
    assert np.isnan(curves['joint_difference_mm'][2]) and curves['paired_joints'][2] == 0
    comp = comparison(manager, tmp_path)
    service = QualityService(manager)
    quality = service.create(*comp['run_ids'])
    assert quality['comparison_role'] == 'discrepancy_and_motion_review_candidates'
    assert service.get(quality['id'])['id'] == quality['id']


def test_fixed_protocol_offline_evaluation_counts_missing_and_blocks_split_leakage(manager, tmp_path):
    a = comparison(manager, tmp_path, reference=True)
    b = comparison(manager, tmp_path, reference=True, start=1)
    registry = ResearchRegistry(manager)
    with pytest.raises(ValueError, match='multiple protocol splits'):
        registry.protocol(name='leak', comparisons=[a['id'], b['id']], splits={a['id']: 'train', b['id']: 'test'})
    protocol = registry.protocol(name='fixed', comparisons=[a['id'], b['id']])
    report = registry.evaluate(protocol_id=protocol['id'], run_ids=[a['run_ids'][0]])
    assert report['status'] == 'partial'
    assert report['rows'][0]['failed_sequences'] == 1
    assert report['rows'][0]['gt_geometry_coverage'] == .5
    assert report['rows'][0]['camera_absolute_joint_mm'] == 0


def test_review_versions_are_immutable_curation_not_automatic_gt(manager, tmp_path):
    comp = comparison(manager, tmp_path)
    quality = QualityService(manager).create(comp['run_ids'][0])
    registry = ResearchRegistry(manager)
    rejected = registry.review(quality_id=quality['id'], start=0, end=3, decision='rejected', reviewer='reviewer')
    with pytest.raises(ValueError, match='accepted'):
        registry.dataset_version(name='invalid', review_ids=[rejected['id']])
    accepted = registry.review(quality_id=quality['id'], start=0, end=3, decision='accepted', reviewer='reviewer')
    dataset = registry.dataset_version(name='curated', review_ids=[accepted['id']])
    assert dataset['training_ready'] is False and 'not automatic GT' in dataset['entries'][0]['label_role']
    assert ResearchRegistry(manager).get(dataset['id']) == dataset


def test_offline_prediction_registration_survives_restart_and_rejects_changed_file(manager, tmp_path):
    source = tmp_path / 'source.mp4'; source.write_bytes(b'video')
    path = tmp_path / 'prediction.npz'; np.savez(path, x=np.arange(4))
    registry = ResearchRegistry(manager)
    artifact = registry.register_raw(source=str(source), algorithm='mint', import_entry={'path': str(path), 'format': 'mint_npz', 'frame_map': [0]})
    assert ResearchRegistry(manager).page()['records'][0]['id'] == artifact['id']
    path.write_bytes(b'changed')
    with pytest.raises(ValueError, match='changed'):
        registry.import_artifact(artifact['id'])


def test_model_cannot_be_promoted_without_its_native_evaluation(manager, tmp_path):
    registry = ResearchRegistry(manager)
    weight = tmp_path / 'model.safetensors'; weight.write_bytes(b'checkpoint fixture')
    model = registry.register_model(path=str(weight))
    evaluation = registry.put('evaluation', {'rows': [], 'protocol_id': 'not-a-protocol'})
    with pytest.raises(ValueError, match='native evaluation'):
        registry.promote(model_id=model['id'], evaluation_id=evaluation['id'])
    assert registry.aliases() == []


def test_evaluated_model_alias_rolls_back_with_its_original_gate(manager, tmp_path, monkeypatch):
    comp = comparison(manager, tmp_path, reference=True)
    checkpoint = tmp_path / 'checkpoints/model.safetensors'
    checkpoint.parent.mkdir(); checkpoint.write_bytes(b'registered baseline')
    configuration = tmp_path / 'configs/training/mint_step2.yaml'
    configuration.parent.mkdir(parents=True); configuration.write_text('model: {}\ndata: {clip_len: 32, size_hw: [378,518]}\n')
    monkeypatch.setattr('mint.comparison.research.PROJECT', tmp_path)
    registry = ResearchRegistry(manager)
    protocol = registry.protocol(name='frozen', comparisons=[comp['id']])
    evaluation = registry.evaluate(protocol_id=protocol['id'], run_ids=[comp['run_ids'][0]])
    first = registry.register_model(path=checkpoint, name='first metadata version')
    second = registry.register_model(path=checkpoint, name='second metadata version')
    for model in (first, second):
        registry.promote(model_id=model['id'], evaluation_id=evaluation['id'], alias='validation', max_joint_mm=.01, min_coverage=1.)
    assert registry.rollback('validation')['model_id'] == first['id']
    assert registry.aliases()[0]['model_id'] == first['id']


def test_offline_scores_keep_frozen_weight_identity_when_a_checkpoint_is_overwritten(manager, tmp_path):
    comp = comparison(manager, tmp_path, reference=True)
    weight = tmp_path / 'weight.safetensors'; weight.write_bytes(b'original model')
    frozen = file_identity(weight)
    request = json.loads(manager.row(comp['run_ids'][0])['payload'])
    request['backend'].update(checkpoint=str(weight), identity={'assets': {str(weight): frozen}})
    with manager.db() as db:
        db.execute('UPDATE runs SET payload=? WHERE id=?', (json.dumps(request), comp['run_ids'][0]))
    weight.write_bytes(b'replaced model with different parameters')
    registry = ResearchRegistry(manager)
    protocol = registry.protocol(name='frozen input', comparisons=[comp['id']])
    report = registry.evaluate(protocol_id=protocol['id'], run_ids=[comp['run_ids'][0]])
    assert report['rows'][0]['model'] == frozen
    assert report['rows'][0]['model'] != file_identity(weight)
    current = registry.register_model(path=weight)
    with pytest.raises(ValueError, match='native evaluation'):
        registry.promote(model_id=current['id'], evaluation_id=report['id'])


def test_distribution_profile_validates_but_does_not_launch_resources(manager):
    registry = ResearchRegistry(manager)
    profile = registry.deployment(name='single node', nodes=[{'host': '127.0.0.1', 'gpus': 8}], shared_root='/data1/lizhen_algo/mint')
    assert profile['processes'] == 8 and profile['status'] == 'configuration_validated_network_unverified'
    with pytest.raises(ValueError, match='MINT-owned'):
        registry.deployment(name='bad', nodes=[{'host': '127.0.0.1'}], shared_root='/data1/lizhen_algo/hawor')


def test_gpu_priority_intents_expire_and_cannot_kill_or_steal_a_live_lease(tmp_path):
    from mint.inference.priority import AdmissionIntent
    batch = AdmissionIntent(tmp_path, 'GPU-test', 'batch')
    high = AdmissionIntent(tmp_path, 'GPU-test', 'interactive')
    assert batch.admitted()
    assert high.admitted()
    assert not batch.admitted() and batch.higher_waiter()
    high.clear()
    assert batch.admitted()
    stale = json.loads(batch.path.read_text()); stale['updated'] -= 10
    atomic_json(batch.path, stale)
    assert high.admitted()
    batch.clear(); high.clear()


def test_camera_data_conversion_preserves_authoritative_label_video_offsets(annotated_episode, tmp_path, monkeypatch):
    import pyarrow.parquet as pq
    from mint.comparison.training_data import prepare_existing_camera_dataset
    from eval.model_effect.visualization.reproj_core.lerobot_io import discover_episodes
    root, episode, _ = annotated_episode
    before = file_identity(episode['video'])
    output = prepare_existing_camera_dataset(root, tmp_path / 'converted', episode_ids=[2])
    assert output['frames'] == 4 and output['episodes'] == 1
    records = discover_episodes(Path(output['root']))
    assert records[0]['video_start'] == 3 and records[0]['length'] == 4
    labels = pq.read_table(records[0]['parquet'])
    assert len(labels) == 4 and labels['cam_quat'].to_pylist()[0] == [0., 0., 0., 1.]
    # Run the production reader: inferred list<double> silently passed the old
    # smoke recipe with normalization disabled, but fails the Stage 2 recipe.
    monkeypatch.syspath_prepend(str(Path(__file__).parents[1] / 'model_train'))
    from model_train.data.camera_normalization import resolve_global_camera_normalization
    normalization = resolve_global_camera_normalization({
        'root': output['root'], 'clip_len': 4, 'clip_stride': 4,
        'camera_translation_normalization': {'enabled': True, 'workers': 1,
                                             'cache_dir': str(tmp_path / 'normalization')}}, log=lambda _: None)
    assert normalization['selected_clip_count'] == 1
    from mint.comparison.training_data import camera_normalization_schema_errors
    assert camera_normalization_schema_errors(output['root']) == []
    # Earlier exported versions remain immutable, but are rejected before GPU
    # admission if a user selects them with production normalization enabled.
    legacy = tmp_path / 'legacy/data'
    legacy.mkdir(parents=True)
    import pyarrow as pa
    pq.write_table(pa.Table.from_pydict(labels.to_pydict()), legacy / 'old.parquet')
    assert len(camera_normalization_schema_errors(legacy.parent)) == 2
    assert file_identity(episode['video']) == before
    assert output['provenance']['label_origin']['type'] == 'existing_declared_camera_annotations'


def test_training_checkpoint_is_atomic_verified_and_disk_admitted(tmp_path, monkeypatch):
    import importlib.util
    import sys
    monkeypatch.setitem(sys.modules, 'utils.logging', SimpleNamespace(rank0_print=lambda *a: None))
    monkeypatch.setitem(sys.modules, 'accelerate.utils', SimpleNamespace(broadcast_object_list=lambda *a, **kw: None))
    path = Path(__file__).parents[1] / 'model_train/engine/checkpoint.py'
    spec = importlib.util.spec_from_file_location('mint_checkpoint_validation', path)
    module = importlib.util.module_from_spec(spec); spec.loader.exec_module(module)
    class Accelerator:
        is_main_process = True
        def unwrap_model(self, model): return model
        def wait_for_everyone(self): pass
        def save_state(self, directory):
            folder = Path(directory); folder.mkdir()
            for name in ('model.safetensors', 'optimizer.bin', 'scheduler.bin', 'random_states_0.pkl'):
                (folder / name).write_bytes(b'complete state fixture')
    parameter = SimpleNamespace(numel=lambda: 10, element_size=lambda: 4, requires_grad=True)
    model = SimpleNamespace(parameters=lambda: [parameter])
    monkeypatch.setenv('MINT_CACHE_MIN_FREE_GIB', '0')
    checkpoint = Path(module.save_checkpoint(Accelerator(), str(tmp_path), 1, model=model))
    assert (checkpoint / 'checkpoint.json').is_file()
    module.validate_checkpoint(checkpoint)
    assert not list(tmp_path.glob('.step_*.partial-*'))
    monkeypatch.setenv('MINT_TRAIN_CHECKPOINT_MAX_GIB', '0.000001')
    with pytest.raises(OSError, match='quota'):
        module.save_checkpoint(Accelerator(), str(tmp_path), 2, model=model)
    assert not (tmp_path / 'step_00000002').exists()
    (checkpoint / 'optimizer.bin').write_bytes(b'changed')
    with pytest.raises(ValueError, match='changed'):
        module.validate_checkpoint(checkpoint)
