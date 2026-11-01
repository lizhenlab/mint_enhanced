"""Batch admission, immutable inputs, cancellation and terminal result accounting."""
from pathlib import Path

import numpy as np
import pytest

from mint.comparison.collections import CollectionManager, collection_request
from mint.comparison.preflight import inspect_collection
from mint.comparison.jobs import ComparisonManager
from mint.inference.cache_budget import file_lock
from tests.test_comparison import arrays, fake_backend, published


@pytest.fixture
def manager(tmp_path, monkeypatch):
    for module in ('jobs', 'collections', 'preflight'):
        monkeypatch.setattr('mint.comparison.' + module + '.backend_spec', lambda name, **_: fake_backend(name))
    monkeypatch.setattr('mint.comparison.preflight.probe_video', lambda _: {'frames': 100, 'fps': 30., 'width': 640, 'height': 480})
    monkeypatch.setenv('MINT_CACHE_MIN_FREE_GIB', '0')
    value = ComparisonManager(tmp_path / 'store', autostart=False)
    yield value
    value.close()


def video(tmp_path):
    path = tmp_path / 'input.mp4'; path.write_bytes(b'fixture'); return str(path)


def reference(tmp_path, mapping=None):
    path = tmp_path / 'reference.npz'; np.savez(path, **arrays())
    return {'path': str(path), 'format': 'normalized_npz', 'declared_source': 'Explicit metric camera annotation',
            'frame_map': list(range(5)) if mapping is None else mapping}


def test_evaluation_preflight_and_creation_use_the_same_required_dataset_reference(manager, tmp_path, monkeypatch):
    source = video(tmp_path); dataset = {'root': '/fixture', 'episode': 0, 'attach_gt': False}
    seen = []
    def inspect(*_, dataset=None, **__):
        seen.append(dataset)
        return {'ready': True, 'errors': [], 'warnings': [], 'frames': 100, 'gt': bool(dataset.get('attach_gt')), 'calibration': False}
    monkeypatch.setattr('mint.comparison.preflight.inspect_request', inspect)
    check = inspect_collection(manager, kind='evaluation', items=[{'source': source, 'dataset': dataset}], selections=[{'algorithm': 'mint'}])
    assert check['ready'] and check['reference_inputs'] == 1 and seen[0]['attach_gt'] is True
    def resolve(spec, **_):
        assert spec['attach_gt'] is True
        return {'source': source}, {'identity': {'fixture': 'frozen'}}
    monkeypatch.setattr('mint.comparison.datasets.resolve_dataset_input', resolve)
    collections = CollectionManager(manager, autostart=False)
    value = collections.create(kind='evaluation', items=[{'source': source, 'dataset': dataset}], selections=[{'algorithm': 'mint'}])
    assert value['inputs'][0]['dataset']['attach_gt'] is True
    assert dataset['attach_gt'] is False  # Normalization never edits the caller's form.
    collections.close()


@pytest.mark.parametrize('flag,value', [('attach_gt', 'true'), ('attach_gt', 1), ('use_calibration', 'false'), ('use_calibration', 0)])
def test_dataset_protocol_flags_are_explicit_booleans(flag, value):
    with pytest.raises(ValueError, match='boolean'):
        collection_request('batch', [{'dataset': {'root': '/data', 'episode': 0, flag: value}}], [{'algorithm': 'mint'}])


@pytest.mark.parametrize('selections', [[{'algorithm': 'mint'}] * 8, [{'algorithm': 'reference'}]])
def test_preflight_and_create_reserve_the_reference_slot_and_reject_reference_as_inference(manager, tmp_path, selections):
    collections = CollectionManager(manager, autostart=False); items = [{'source': video(tmp_path)}]
    with pytest.raises(ValueError): inspect_collection(manager, items=items, selections=selections)
    with pytest.raises(ValueError): collections.create(items=items, selections=selections)
    collections.close()


def test_dataset_and_external_reference_cannot_create_two_gt_or_different_episode_timeline(tmp_path):
    with pytest.raises(ValueError, match='not both'):
        collection_request('evaluation', [{'dataset': {'root': '/data', 'episode': 0}, 'reference': reference(tmp_path)}], [{'algorithm': 'mint'}])


@pytest.mark.parametrize('mapping', [[0, 1, 2], [0, 1, 2, 3, 100]])
def test_reference_shape_and_sample_mapping_fail_preflight_before_any_run(manager, tmp_path, mapping):
    check = inspect_collection(manager, items=[{'source': video(tmp_path), 'reference': reference(tmp_path, mapping)}], selections=[{'algorithm': 'mint'}])
    assert not check['ready'] and 'frame_map' in ' '.join(check['errors'])
    with manager.db() as db: assert db.execute('SELECT count(*) FROM runs').fetchone()[0] == 0


def test_valid_external_reference_is_visible_in_review_totals(manager, tmp_path):
    check = inspect_collection(manager, items=[{'source': video(tmp_path), 'reference': reference(tmp_path)}], selections=[{'algorithm': 'mint'}])
    assert check['ready'] and check['inputs'][0]['gt'] is True and check['reference_inputs'] == 1
    assert check['calibrated_inputs'] == 0 and isinstance(check['warnings'], list)


def test_changed_reference_is_rejected_before_creating_inference_children(manager, tmp_path, monkeypatch):
    collections = CollectionManager(manager, autostart=False); entry = reference(tmp_path)
    value = collections.create(items=[{'source': video(tmp_path), 'reference': entry}], selections=[{'algorithm': 'mint'}])
    Path(entry['path']).write_bytes(b'changed')
    monkeypatch.setattr(manager, 'create', lambda *_args, **_kwargs: pytest.fail('A changed reference cannot start inference'))
    collections._execute(value['id']); final = collections.get(value['id'])
    assert final['status'] == 'failed' and final['done'] == 1 and final['counts']['failed'] == 1
    assert 'Reference annotations changed' in final['items'][0]['error'] and final['report_available']
    collections.close()


def test_all_failed_child_runs_do_not_turn_into_a_partial_success(manager, tmp_path, monkeypatch):
    collections = CollectionManager(manager, autostart=False)
    value = collections.create(items=[{'source': video(tmp_path)}], selections=[{'algorithm': 'mint'}])
    original = manager.create
    def fail_child(*args, **kwargs):
        child = original(*args, **kwargs)
        for run_id in child['run_ids']: manager._set(run_id, 'failed', error='fixture failure')
        return manager.comparison(child['id'])
    monkeypatch.setattr(manager, 'create', fail_child)
    collections._execute(value['id']); final = collections.get(value['id'])
    assert final['status'] == 'failed' and final['counts']['failed'] == 1 and final['completed'] == 0
    assert final['items'][0]['errors'] == ['fixture failure']
    assert collections.report(value['id'])['collection']['status'] == 'failed'
    collections.close()


def test_batch_with_some_published_geometry_remains_partial(manager, tmp_path, monkeypatch):
    collections = CollectionManager(manager, autostart=False)
    value = collections.create(items=[{'source': video(tmp_path)}], selections=[{'algorithm': 'mint'}, {'algorithm': 'hawor_camera'}])
    original = manager.create
    def partial_child(*args, **kwargs):
        child = original(*args, **kwargs); first, second = child['run_ids']
        published(manager.root / 'runs' / first / 'result', sample_id=child['sample_id'], run_id=first)
        manager._set(first, 'completed'); manager._set(second, 'failed', error='fixture failure')
        return manager.comparison(child['id'])
    monkeypatch.setattr(manager, 'create', partial_child)
    collections._execute(value['id']); final = collections.get(value['id'])
    assert final['status'] == 'partial' and final['counts']['partial'] == 1
    collections.close()


def test_cancel_before_start_marks_every_unfinished_input_cancelled_and_publishes_index(manager, tmp_path):
    collections = CollectionManager(manager, autostart=False)
    value = collections.create(items=[{'source': video(tmp_path)}] * 2, selections=[{'algorithm': 'mint'}])
    collections.cancel(value['id']); collections._execute(value['id']); final = collections.get(value['id'])
    assert final['status'] == 'cancelled' and final['done'] == 2 and final['counts']['cancelled'] == 2
    assert final['counts']['queued'] == 0 and final['report_available']
    assert collections.report(value['id'])['collection']['counts']['cancelled'] == 2
    collections.close()


def test_cancel_between_read_and_execution_claim_is_not_overwritten(manager, tmp_path, monkeypatch):
    collections = CollectionManager(manager, autostart=False)
    value = collections.create(items=[{'source': video(tmp_path)}], selections=[{'algorithm': 'mint'}])
    original = collections.get; first = True
    def race(collection_id):
        nonlocal first
        snapshot = original(collection_id)
        if first:
            first = False; collections.cancel(collection_id)
        return snapshot
    monkeypatch.setattr(collections, 'get', race)
    monkeypatch.setattr(manager, 'create', lambda *_args, **_kwargs: pytest.fail('Cancelled input cannot be submitted'))
    collections._execute(value['id'])
    assert original(value['id'])['status'] == 'cancelled'
    collections.close()


def test_suspension_preserves_a_cancel_request_for_recovery(manager, tmp_path):
    collections = CollectionManager(manager, autostart=False)
    value = collections.create(items=[{'source': video(tmp_path)}], selections=[{'algorithm': 'mint'}])
    collections.cancel(value['id']); collections._suspend(value['id'], [{'status': 'running'}])
    assert collections.get(value['id'])['status'] == 'cancelling'
    collections._execute(value['id']); assert collections.get(value['id'])['status'] == 'cancelled'
    collections.close()


def test_terminal_publication_keeps_concurrent_cancel_and_completed_inputs(manager, tmp_path):
    collections = CollectionManager(manager, autostart=False)
    value = collections.create(items=[{'source': video(tmp_path)}] * 2, selections=[{'algorithm': 'mint'}])
    directory = collections.root / value['id']; directory.mkdir()
    collections.cancel(value['id']); collections._finish(value['id'], 'partial', [{'status': 'completed'}, {'status': 'queued'}])
    final = collections.get(value['id'])
    assert final['status'] == 'cancelled' and final['completed'] == 1 and final['counts']['cancelled'] == 1 and final['done'] == 2
    collections.close()


def test_resume_does_not_race_an_executor_and_clears_previous_attempt_errors(manager, tmp_path):
    collections = CollectionManager(manager, autostart=False)
    value = collections.create(items=[{'source': video(tmp_path)}], selections=[{'algorithm': 'mint'}])
    collections._set(value['id'], status='failed', items=[{'status': 'failed', 'error': 'old', 'errors': ['old run error']}])
    directory = collections.root / value['id']; directory.mkdir()
    with file_lock(directory / '.execution.lock'):
        assert collections.resume(value['id'])['status'] == 'failed'
    resumed = collections.resume(value['id'])
    assert resumed['status'] == 'queued' and resumed['items'][0] == {'status': 'queued', 'retry': True}
    collections.close()


def test_reference_change_after_child_creation_cancels_only_new_exclusive_work(manager, tmp_path, monkeypatch):
    collections = CollectionManager(manager, autostart=False); entry = reference(tmp_path)
    value = collections.create(items=[{'source': video(tmp_path), 'reference': entry}], selections=[{'algorithm': 'mint'}])
    original = manager.create
    def changed_after_create(*args, **kwargs):
        child = original(*args, **kwargs); Path(entry['path']).write_bytes(b'changed after creation'); return child
    monkeypatch.setattr(manager, 'create', changed_after_create)
    collections._execute(value['id']); final = collections.get(value['id'])
    assert final['status'] == 'failed' and final['items'][0]['comparison_id']
    child = manager.comparison(final['items'][0]['comparison_id'])
    assert all(run['status'] == 'cancelled' for run in child['runs'])
    collections.close()


@pytest.mark.parametrize('payload', ['not an archive', 'npy'])
def test_malformed_normalized_reference_is_a_validation_error_before_queueing(manager, tmp_path, payload):
    entry = reference(tmp_path)
    if payload == 'npy':
        with Path(entry['path']).open('wb') as stream: np.save(stream, np.ones(3))
    else: Path(entry['path']).write_bytes(payload.encode())
    collections = CollectionManager(manager, autostart=False)
    with pytest.raises(ValueError, match='Invalid normalized reference'):
        collections.create(items=[{'source': video(tmp_path), 'reference': entry}], selections=[{'algorithm': 'mint'}])
    assert not collections.history()
    collections.close()


def test_preflight_receipt_is_stable_and_changes_with_source_or_backend(manager, tmp_path, monkeypatch):
    source = video(tmp_path); kwargs = {'items': [{'source': source}], 'selections': [{'algorithm': 'mint'}]}
    first = inspect_collection(manager, **kwargs)['preflight_id']
    assert len(first) == 64 and first == inspect_collection(manager, **kwargs)['preflight_id']
    Path(source).write_bytes(b'replacement source file')
    second = inspect_collection(manager, **kwargs)['preflight_id']; assert second != first
    monkeypatch.setattr('mint.comparison.preflight.backend_spec', lambda name, **_: {**fake_backend(name), 'version': 'new-backend'})
    assert inspect_collection(manager, **kwargs)['preflight_id'] != second


def test_preflight_receipt_freezes_external_gt_contents(manager, tmp_path):
    entry = reference(tmp_path); kwargs = {'items': [{'source': video(tmp_path), 'reference': entry}], 'selections': [{'algorithm': 'mint'}]}
    first = inspect_collection(manager, **kwargs)['preflight_id']
    replacement = arrays(); replacement['joints_camera_m'] += .01
    np.savez(entry['path'], **replacement)
    assert inspect_collection(manager, **kwargs)['preflight_id'] != first


def test_final_submission_rejects_a_stale_review_before_creating_any_collection(monkeypatch):
    from flask import Flask
    from mint.comparison.api import comparison_blueprint
    monkeypatch.setattr('mint.comparison.api.ComparisonManager', lambda: object())
    monkeypatch.setattr('mint.comparison.preflight.inspect_collection', lambda *_args, **_kwargs: {'ready': True, 'preflight_id': 'b' * 64})
    monkeypatch.setattr('mint.comparison.collections.CollectionManager', lambda _: pytest.fail('Stale review cannot create background work'))
    app = Flask(__name__); app.register_blueprint(comparison_blueprint())
    response = app.test_client().post('/api/comparison/collections', json={'items': [], 'selections': [], 'preflight_id': 'a' * 64})
    assert response.status_code == 400 and '重新预检' in response.json['error']


@pytest.mark.parametrize('reviewed', [False, True])
def test_matching_review_and_legacy_api_omit_receipt_from_durable_request(monkeypatch, reviewed):
    from types import SimpleNamespace
    from flask import Flask
    from mint.comparison.api import comparison_blueprint
    captured = []
    monkeypatch.setattr('mint.comparison.api.ComparisonManager', lambda: object())
    monkeypatch.setattr('mint.comparison.preflight.inspect_collection', lambda *_args, **_kwargs: {'ready': True, 'preflight_id': 'a' * 64})
    def create(**kwargs): captured.append(kwargs); return {'id': 'created'}
    monkeypatch.setattr('mint.comparison.collections.CollectionManager', lambda _: SimpleNamespace(create=create))
    app = Flask(__name__); app.register_blueprint(comparison_blueprint())
    body = {'items': [], 'selections': []}
    if reviewed: body['preflight_id'] = 'a' * 64
    response = app.test_client().post('/api/comparison/collections', json=body)
    assert response.status_code == 201 and 'preflight_id' not in captured[0]


def test_durable_creation_rechecks_the_last_preflight_before_queueing(manager, tmp_path, monkeypatch):
    source = video(tmp_path); kwargs = {'items': [{'source': source}], 'selections': [{'algorithm': 'mint'}]}
    check = inspect_collection(manager, **kwargs)
    collections = CollectionManager(manager, autostart=False)
    Path(source).write_bytes(b'changed after final admission')
    with pytest.raises(ValueError, match='重新预检'): collections.create(**kwargs, _preflight=check)
    assert not collections.history()
    collections.close()


def test_durable_creation_freezes_exactly_the_admitted_protocol(manager, tmp_path):
    kwargs = {'items': [{'source': video(tmp_path), 'reference': reference(tmp_path)}], 'selections': [{'algorithm': 'mint'}]}
    check = inspect_collection(manager, **kwargs)
    collections = CollectionManager(manager, autostart=False)
    value = collections.create(**kwargs, _preflight=check)
    assert value['status'] == 'queued' and value['inputs'][0]['snapshot'] == check['inputs'][0]['input_identity']
    assert value['inputs'][0]['reference']['identity'] == check['inputs'][0]['reference_identity']
    collections.close()


def test_gt_success_does_not_hide_failed_inference_and_external_reference_batches_can_resume(manager, tmp_path, monkeypatch):
    collections = CollectionManager(manager, autostart=False)
    value = collections.create(items=[{'source': video(tmp_path), 'reference': reference(tmp_path)}], selections=[{'algorithm': 'mint'}])
    original = manager.create; prediction_creations = []
    def create(*args, **kwargs):
        child = original(*args, **kwargs)
        for run_id in child['run_ids']:
            if manager.run(run_id)['algorithm'] == 'reference' and not manager.run(run_id)['result']:
                published(manager.root / 'runs' / run_id / 'result', sample_id=child['sample_id'], run_id=run_id,
                          reference_role='user_declared_ground_truth')
                manager._set(run_id, 'completed')
            elif manager.run(run_id)['algorithm'] != 'reference':
                prediction_creations.append(run_id); manager._set(run_id, 'failed', error='inference failed')
        return manager.comparison(child['id'])
    monkeypatch.setattr(manager, 'create', create)
    collections._execute(value['id']); first = collections.get(value['id'])
    assert first['status'] == 'failed' and first['counts']['failed'] == 1
    child_id = first['items'][0]['comparison_id']
    def successful_retry(run_id):
        child = manager.comparison(child_id)
        published(manager.root / 'runs' / run_id / 'result', sample_id=child['sample_id'], run_id=run_id)
        manager._set(run_id, 'completed'); return manager.run(run_id)
    monkeypatch.setattr(manager, 'resume', successful_retry)
    collections.resume(value['id']); collections._execute(value['id']); final = collections.get(value['id'])
    assert final['status'] == 'completed' and final['items'][0]['comparison_id'] == child_id
    assert len(prediction_creations) == 1  # Reuse the exact child; its reference was attached after initial creation.
    collections.close()


def test_resuming_a_batch_does_not_wait_for_or_resume_unrelated_attached_runs(manager, tmp_path, monkeypatch):
    collections = CollectionManager(manager, autostart=False); source = video(tmp_path)
    value = collections.create(items=[{'source': source}], selections=[{'algorithm': 'mint'}])
    child = manager.create(source, value['selections'], comparison_key=value['id'] + ':0')
    prediction = child['run_ids'][0]; manager._set(prediction, 'failed', error='old failure')
    other = manager.create(source, [{'algorithm': 'hawor_camera'}]); manager.attach(child['id'], other['run_ids'][0])
    collections._set(value['id'], status='failed', items=[{'status': 'failed', 'comparison_id': child['id'], 'run_ids': [prediction]}])
    def resume(run_id):
        assert run_id == prediction
        published(manager.root / 'runs' / prediction / 'result', sample_id=child['sample_id'], run_id=prediction)
        manager._set(prediction, 'completed'); return manager.run(prediction)
    monkeypatch.setattr(manager, 'resume', resume)
    collections.resume(value['id']); collections._execute(value['id'])
    assert collections.get(value['id'])['status'] == 'completed'
    assert manager.run(other['run_ids'][0])['status'] == 'queued'
    collections.close()


def test_external_reference_checksum_detects_changes_when_filesystem_metadata_is_identical(manager, tmp_path, monkeypatch):
    import mint.comparison.collections as module
    entry = reference(tmp_path); source = video(tmp_path)
    original = module.file_identity; frozen = original(entry['path'])
    monkeypatch.setattr(module, 'file_identity', lambda path: frozen if str(Path(path).resolve()) == entry['path'] else original(path))
    kwargs = {'items': [{'source': source, 'reference': entry}], 'selections': [{'algorithm': 'mint'}]}
    first = inspect_collection(manager, **kwargs)
    identity = first['inputs'][0]['reference_identity']; assert identity['sha256']
    replacement = arrays(); replacement['joints_camera_m'] += .01; np.savez(entry['path'], **replacement)
    second = inspect_collection(manager, **kwargs)
    assert second['preflight_id'] != first['preflight_id']
    assert not module._reference_matches(entry['path'], identity)
    assert module._reference_matches(entry['path'], frozen)  # Legacy identity contract remains unchanged.


def test_evaluation_report_computation_keeps_database_writable_and_reconciles_cancellation(manager, tmp_path, monkeypatch):
    collections = CollectionManager(manager, autostart=False)
    value = collections.create(kind='evaluation', items=[{'source': video(tmp_path), 'reference': reference(tmp_path)}], selections=[{'algorithm': 'mint'}])
    (collections.root / value['id']).mkdir()
    def report(_manager, snapshot):
        # Report calculation must permit concurrent task updates/cancellation.
        assert collections.cancel(value['id'])['status'] == 'cancelling'
        return {'status': snapshot['status'], 'sequences': [{'index': 0, 'status': 'completed'}]}
    monkeypatch.setattr('mint.comparison.evaluation.collection_report', report)
    collections._finish(value['id'], 'completed', [{'status': 'completed'}])
    assert collections.get(value['id'])['status'] == 'cancelled'
    assert collections.report(value['id'])['status'] == 'cancelled'
    collections.close()
