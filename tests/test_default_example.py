"""Default drafts must point at complete, unchanged prepared reference data."""
import copy
import json
import sqlite3
from contextlib import contextmanager
from pathlib import Path
from types import SimpleNamespace

import pytest

from mint.comparison.default_example import get_default_example
from mint.runtime import atomic_json, digest, file_identity


@pytest.fixture
def store(tmp_path):
    connection = sqlite3.connect(':memory:')
    connection.execute('CREATE TABLE research_records (id TEXT,kind TEXT,created REAL,payload TEXT)')
    records = {}

    @contextmanager
    def db():
        yield connection

    def get(key, kind):
        row = records[key]
        assert kind == 'dataset'
        return copy.deepcopy(row)

    def create(*, frames=677, maximum=None, sequence='s05/box_grab_01', revision=0, mutate=None):
        raw = tmp_path / 'raw' / 'sample.mano.npy'
        raw.parent.mkdir(exist_ok=True)
        if not raw.exists():
            raw.write_bytes(b'original annotation fixture')
        plan = {'dataset': 'arctic', 'split': 'val', 'sequence': sequence, 'root': str(raw.parent),
                'start_frame': 0, 'max_frames': maximum, 'training': False, 'adapter_revision': str(revision),
                'files': [file_identity(raw)]}
        native_id = digest(plan)
        folder = tmp_path / 'native-sources' / native_id
        folder.mkdir(parents=True)
        for name in ('video.mp4', 'reference.npz', 'native-frame-map.npz'):
            (folder / name).write_bytes(b'published-file-' + name.encode())
        camera = {'K': [[578., 0., 336.], [0., 578., 240.], [0., 0., 1.]],
                  'width': 672, 'height': 480, 'source': 'arctic native calibrated/rectified camera'}
        native = {'id': native_id, 'plan': plan, 'details': {'label_role': 'official_MANO_annotation',
                  'mano_convention': 'arctic_official_mean_pose_v1',
                  'ioi_offset': 1, 'pose_frame_indices': list(range(frames))},
                  'frames': frames, 'fps': 30., 'width': 672, 'height': 480, 'camera': camera,
                  'source': str(folder / 'video.mp4'), 'reference': str(folder / 'reference.npz'),
                  'training': None, 'supervision': ['camera', 'mano'],
                  'native_frame_indices': list(range(1, frames + 1)),
                  'valid_gt_hand_frames': [frames, frames],
                  'video_identity': file_identity(folder / 'video.mp4'),
                  'reference_identity': file_identity(folder / 'reference.npz'),
                  'frame_map_identity': file_identity(folder / 'native-frame-map.npz')}
        if mutate:
            mutate(native)
        atomic_json(folder / 'native.json', native)
        key = digest({'kind': 'dataset', 'native': native})
        record = {'id': key, 'kind': 'dataset', 'status': 'native_prepared', 'frames': frames,
                  'split': 'val', 'native': native, 'identity': file_identity(folder / 'native.json')}
        records[key] = record
        connection.execute('INSERT INTO research_records VALUES(?,?,?,?)',
                           (key, 'dataset', revision, json.dumps(record)))
        return record

    yield SimpleNamespace(manager=SimpleNamespace(root=tmp_path, db=db), registry=SimpleNamespace(get=get),
                          create=create, records=records, connection=connection)
    connection.close()


def inspect(store):
    return get_default_example(store.manager, store.registry)


@pytest.fixture
def native_client(store, monkeypatch):
    from flask import Flask
    from mint.comparison.api import comparison_blueprint

    store.connection.row_factory = sqlite3.Row
    store.connection.execute('CREATE TABLE runs (id TEXT PRIMARY KEY, payload TEXT NOT NULL)')
    created, attachments, preflights = [], [], []
    reference_version = 'current-reference-fixture'

    def create(**request):
        created.append(request)
        if request.get('import_entry'):
            return {'id': 'reference-comparison', 'runs': [{'id': 'official-gt'}]}
        return {'id': 'comparison', 'runs': [{'id': 'prediction'}]}

    def attach(comparison_id, run_id):
        attachments.append((comparison_id, run_id))
        return {'id': comparison_id, 'run_ids': ['prediction', run_id]}

    def preflight(_manager, **request):
        preflights.append(request)
        return {'ready': True}

    def forbidden_prepare(*_args, **_kwargs):
        pytest.fail('Reusing a published native sample must not prepare or rebuild it')

    store.manager.create, store.manager.attach = create, attach
    monkeypatch.setattr('mint.comparison.api.ComparisonManager', lambda: store.manager)
    monkeypatch.setattr('mint.comparison.api.backend_spec', lambda _algorithm: {
        'ready': True, 'version': reference_version,
    })
    monkeypatch.setattr('mint.comparison.research.ResearchRegistry', lambda _manager: store.registry)
    monkeypatch.setattr('mint.comparison.preflight.inspect_request', preflight)
    monkeypatch.setattr('mint.comparison.native_sources.prepare_native', forbidden_prepare)
    monkeypatch.setattr('mint.comparison.native_sources._prepare_native', forbidden_prepare)
    app = Flask(__name__)
    app.testing = True
    app.register_blueprint(comparison_blueprint())
    return SimpleNamespace(client=app.test_client(), created=created, attachments=attachments,
                           preflights=preflights, reference_version=reference_version)


def submit_example(client, example):
    return client.post(f"/api/comparison/native/{example['dataset_id']}/compare", json={
        'selections': example['selections'], 'calibration': example['calibration'],
        'execution': example['execution'], 'title': example['name'],
    })


def reuse_video_alias(native, target):
    alias = Path(native['source'])
    alias.unlink()
    alias.symlink_to(target)
    native['video_identity'] = file_identity(alias)


@pytest.mark.parametrize('corrected_video_alias', [False, True])
def test_default_get_and_native_submit_reuse_published_sample_after_raw_source_is_archived(store, native_client, corrected_video_alias):
    record = store.create()
    if corrected_video_alias:
        original = Path(record['native']['source'])
        record = store.create(revision=1, mutate=lambda native: reuse_video_alias(native, original))
    native = record['native']
    Path(native['plan']['files'][0]['path']).unlink()
    folder = Path(native['reference']).parent
    before = {file.name: file_identity(file) for file in folder.iterdir()}

    response = native_client.client.get('/api/comparison/default-example')
    assert response.status_code == 200
    example = response.json
    assert example['available'] and example['dataset_id'] == record['id']
    assert not native_client.created

    response = submit_example(native_client.client, example)
    assert response.status_code == 201, response.json
    assert response.json['run_ids'] == ['prediction', 'official-gt']
    prediction, reference = native_client.created
    assert prediction['selections'] == example['selections']
    assert prediction['execution'] == example['execution']
    assert all(request['source'] == native['source'] and request['camera'] == native['camera']
               for request in (prediction, reference))
    entry = reference['import_entry']
    assert reference['selections'] == [{'algorithm': 'reference'}]
    assert entry['path'] == native['reference'] and entry['frame_map'] == list(range(677))
    assert json.loads(entry['declared_source'])['version'] == native['id']
    assert native_client.reference_version in reference['comparison_key']
    assert native_client.attachments == [('comparison', 'official-gt')]
    assert {file.name: file_identity(file) for file in folder.iterdir()} == before


@pytest.mark.parametrize('filename', ['native.json', 'video.mp4', 'reference.npz', 'native-frame-map.npz'])
def test_native_submit_rechecks_published_artifact_changes_after_default_selection(store, native_client, filename):
    record = store.create()
    example = native_client.client.get('/api/comparison/default-example').json
    assert example['available']
    folder = Path(record['native']['reference']).parent
    (folder / filename).write_bytes(b'changed after selecting the example')

    response = submit_example(native_client.client, example)
    assert response.status_code == 400 and response.json['error']
    assert not native_client.preflights
    assert not native_client.created and not native_client.attachments


def test_native_submit_rejects_withdrawn_reference_before_creating_predictions(store, native_client):
    from mint.comparison.reference_validity import register_reference_withdrawal

    record = store.create()
    example = native_client.client.get('/api/comparison/default-example').json
    assert example['available']
    register_reference_withdrawal(store.manager, record['native']['reference'], reason='Known wrong annotation')

    response = submit_example(native_client.client, example)
    assert response.status_code == 400
    assert 'Known wrong annotation' in response.json['error']
    assert not native_client.created and not native_client.attachments


def test_complete_published_example_yields_explicit_draft_without_jobs(store):
    record = store.create()
    before = sorted(str(p) for p in Path(store.manager.root).rglob('*'))
    value = inspect(store)
    assert value['available'] and value['dataset_id'] == record['id']
    assert value['id'] == 'arctic-full-gt' and value['frames'] == 677 and value['fps'] == 30
    assert value['source'] == record['native']['source']
    assert value['view'] == 'full' and value['start_frame'] == 0 and value['target_fps'] == 0
    assert value['max_frames'] is None and value['calibration'] is True
    assert value['reference']['attach'] and value['reference']['role'] == 'official_MANO_annotation'
    assert value['execution'] == {'cache': 'exact', 'priority': 'interactive'}
    assert [r['algorithm'] for r in value['selections']] == [
        'mint', 'hawor_full', 'hawor_camera', 'ace_kfree', 'ace_kgiven']
    assert all(r['options']['max_gpus'] == 8 and r['options']['resource_policy'] == 'shared' for r in value['selections'])
    assert value['selections'][3]['options']['mode'] == 'independent81'
    assert not any('checkpoint' in row for row in value['selections'])
    assert sorted(str(p) for p in Path(store.manager.root).rglob('*')) == before
    # Returned presets must not share mutable options/calibration with the store.
    value['camera']['K'][0][0] = 0
    value['selections'][0]['options']['max_gpus'] = 1
    assert inspect(store)['camera']['K'][0][0] == 578.
    assert inspect(store)['selections'][0]['options']['max_gpus'] == 8


def test_no_prepared_sample_returns_unavailable_without_executable_fields(store):
    value = inspect(store)
    assert not value['available'] and value['reason']
    assert 'dataset_id' not in value and 'source' not in value and 'selections' not in value


@pytest.mark.parametrize('args', [
    {'frames': 81, 'maximum': 81},
    {'maximum': 677},
    {'sequence': 's05/box_use_01'},
    {'mutate': lambda n: n['plan'].pop('max_frames')},
    {'mutate': lambda n: n['plan'].update(start_frame=True)},
    {'mutate': lambda n: n['plan'].update(training=True)},
    {'mutate': lambda n: n['details'].update(label_role='model_generated_pseudo_labels')},
    {'mutate': lambda n: n['details'].pop('mano_convention')},
    {'mutate': lambda n: n['details'].update(mano_convention='matrix_layer_without_hand_mean')},
    {'mutate': lambda n: n.update(valid_gt_hand_frames=[676, 677])},
    {'mutate': lambda n: n.update(native_frame_indices=list(range(677)))},
    {'mutate': lambda n: n.update(native_frame_indices=[float(i) for i in range(1, 678)])},
    {'mutate': lambda n: n['details'].update(pose_frame_indices=list(range(1, 678)))},
    {'mutate': lambda n: n['details'].update(pose_frame_indices=[False, *range(1, 677)])},
    {'mutate': lambda n: n['details'].update(ioi_offset=-1)},
    {'mutate': lambda n: n.update(fps=float('inf'))},
    {'mutate': lambda n: n.update(fps=True)},
    {'mutate': lambda n: n['camera'].update(width=640)},
    {'mutate': lambda n: n['camera'].update(K=[[0, 0, 336], [0, 578, 240], [0, 0, 1]])},
    {'mutate': lambda n: n['camera'].update(K=[[578, 0, 0], [0, 578, 240], [0, 0, 1]])},
    {'mutate': lambda n: n['camera'].update(K=[[578, 0, 336], [0, float('nan'), 240], [0, 0, 1]])},
])
def test_short_mismatched_or_untrusted_metadata_never_becomes_the_default(store, args):
    store.create(**args)
    assert not inspect(store)['available']


@pytest.mark.parametrize('name', ['native.json', 'video.mp4', 'reference.npz', 'native-frame-map.npz'])
def test_changed_published_artifact_is_rejected(store, name):
    record = store.create()
    folder = Path(record['native']['source']).parent
    (folder / name).write_bytes(b'changed')
    value = inspect(store)
    assert not value['available'] and '校验' in value['reason']


@pytest.mark.parametrize('name', ['native.json', 'video.mp4', 'reference.npz', 'native-frame-map.npz'])
def test_missing_published_artifact_is_rejected(store, name):
    record = store.create()
    (Path(record['native']['source']).parent / name).unlink()
    assert not inspect(store)['available']


def test_registry_and_marker_must_match_even_if_record_was_relabelled(store):
    record = store.create()
    record['native']['camera']['source'] = 'modified metadata'
    assert not inspect(store)['available']


def test_newest_broken_version_does_not_hide_an_unchanged_full_version(store):
    older = store.create(revision=0)
    newer = store.create(revision=1)
    Path(newer['native']['reference']).unlink()
    assert inspect(store)['dataset_id'] == older['id']


def test_newest_valid_full_version_is_selected_and_old_short_sample_is_ignored(store):
    store.create(revision=0)
    newest = store.create(revision=1)
    store.create(frames=81, maximum=81, revision=2)
    assert inspect(store)['dataset_id'] == newest['id']


def test_uninitialized_registry_is_unavailable_instead_of_server_error(store):
    store.connection.execute('DROP TABLE research_records')
    assert not inspect(store)['available']


def test_native_publication_cannot_be_redirected_to_another_path(store, tmp_path):
    record = store.create()
    record['native']['source'] = str(tmp_path / 'unrelated.mp4')
    assert not inspect(store)['available']


def test_corrected_reference_can_reuse_immutable_native_video_without_changing_source_identity(store):
    from mint.comparison.contracts import source_request
    original = store.create(revision=0)
    target = Path(original['native']['source'])

    corrected = store.create(revision=1, mutate=lambda native: reuse_video_alias(native, target))
    value = inspect(store)
    assert value['available'] and value['dataset_id'] == corrected['id']
    assert source_request(value['source'], camera=value['camera'])['id'] == source_request(
        original['native']['source'], camera=original['native']['camera'])['id']
    assert corrected['native']['reference'] != original['native']['reference']
    target.write_bytes(b'changed RGB')
    assert not inspect(store)['available']


@pytest.mark.parametrize('filename', ['video.mp4', 'reference.npz', 'native-frame-map.npz'])
def test_external_aliases_never_become_the_default(store, tmp_path, filename):
    target = tmp_path / 'external' / filename
    target.parent.mkdir()
    target.write_bytes(b'external')

    def redirect(native):
        alias = Path(native['source']).parent / filename
        alias.unlink()
        alias.symlink_to(target)
        native[{'video.mp4': 'video_identity', 'reference.npz': 'reference_identity',
                'native-frame-map.npz': 'frame_map_identity'}[filename]] = file_identity(alias)

    store.create(mutate=redirect)
    assert not inspect(store)['available']


@pytest.mark.parametrize('filename', ['reference.npz', 'native-frame-map.npz'])
def test_reference_and_frame_map_aliases_are_rejected_even_inside_native_store(store, filename):
    original = store.create(revision=0)
    target = Path(original['native']['source']).parent / filename

    def redirect(native):
        alias = Path(native['source']).parent / filename
        alias.unlink()
        alias.symlink_to(target)
        native['reference_identity' if filename == 'reference.npz' else 'frame_map_identity'] = file_identity(alias)

    store.create(revision=1, mutate=redirect)
    assert inspect(store)['dataset_id'] == original['id']


@pytest.mark.parametrize('change', [lambda r: r.update(native=[]),
                                  lambda r: r['native'].update(plan=None),
                                  lambda r: r['native'].update(details=None)])
def test_malformed_registry_values_are_unavailable_not_server_errors(store, change):
    record = store.create()
    change(record)
    assert not inspect(store)['available']
