"""MINT-owned camera-training datasets with explicit label origin and frame maps."""
from __future__ import annotations

import json
from itertools import chain
from pathlib import Path

import numpy as np

from mint.runtime import atomic_json, file_identity


def camera_normalization_schema_errors(root):
    """Check one data file before admitting a normalized training job to GPUs."""
    import pyarrow.parquet as pq
    root = Path(root)
    path = next((root / 'data').rglob('*.parquet'), None)
    if path is None:
        return ['相机归一化训练缺少 data Parquet：' + str(root)]
    schema = pq.read_schema(path)
    errors = []
    for name, width in (('cam_trans', 3), ('cam_quat', 4)):
        if name not in schema.names or getattr(schema.field(name).type, 'list_size', None) != width:
            errors.append(f'相机归一化要求 {name} 为 fixed_size_list[{width}]；请重新转换数据版本：{path}')
    return errors


def _write_dataset(destination, episodes, *, origin):
    import pyarrow as pa
    import pyarrow.parquet as pq
    from decord import VideoReader
    destination = Path(destination)
    if (destination / 'meta/info.json').exists():
        raise FileExistsError('Training dataset versions are immutable')
    destination.mkdir(parents=True, exist_ok=True)
    meta, data, videos = [], [], []
    start = 0
    fps = None
    for number, episode in enumerate(episodes):
        values, video, offset = episode['columns'], Path(episode['video']), episode.get('video_offset_s', 0.)
        n = len(values['cam_trans'])
        if not n or any(len(value) != n for value in values.values()):
            raise ValueError('Training columns must share a nonempty frame timeline')
        matrices = np.asarray(values['cam_quat'])
        if matrices.shape != (n, 4) or not np.isfinite(matrices).all() or not np.allclose(np.linalg.norm(matrices, axis=1), 1., atol=1e-4):
            raise ValueError('Training camera rotation requires finite unit xyzw quaternions')
        fov_values = np.asarray(values['cam_fov'])
        if np.asarray(values['cam_trans']).shape != (n, 3) or fov_values.shape != (n, 2) or np.any(fov_values <= 0) or np.any(fov_values >= np.pi) or not np.isfinite(values['cam_trans']).all() or not np.isfinite(fov_values).all():
            raise ValueError('Training trajectories and FoV must be finite')
        reader = VideoReader(str(video), num_threads=1)
        local_fps = float(reader.get_avg_fps())
        if fps is not None and abs(local_fps - fps) > .01:
            raise ValueError('A training dataset version must use one frame rate')
        fps = local_fps
        shape = list(reader[0].shape)
        if round(offset * fps) + n > len(reader):
            raise ValueError('Training labels escape the video timeline')
        values = {**values, 'episode_index': [number] * n, 'frame_index': list(range(n)),
                  'timestamp': (np.arange(n) / fps).tolist(), 'index': list(range(start, start + n))}
        data.append(values)
        key = 'observation.images.ego'
        target = destination / f'videos/{key}/chunk-000/file-{number:03d}.mp4'
        target.parent.mkdir(parents=True, exist_ok=True)
        target.symlink_to(video.resolve())
        videos.append(file_identity(video))
        meta.append({'episode_index': number, 'length': n, 'data/chunk_index': 0, 'data/file_index': 0,
            f'videos/{key}/chunk_index': 0, f'videos/{key}/file_index': number, f'videos/{key}/from_timestamp': offset,
            f'videos/{key}/to_timestamp': offset + n / fps, 'dataset_from_index': start, 'dataset_to_index': start + n})
        start += n
    if not meta:
        raise ValueError('Select nonempty episodes')
    keys = set(data[0])
    if any(set(v) != keys for v in data):
        raise ValueError('All episodes must use the same supervision schema')
    combined = {key: list(chain.from_iterable(row[key] for row in data)) for key in keys}
    parquet = destination / 'data/chunk-000/file-000.parquet'
    parquet.parent.mkdir(parents=True, exist_ok=True)
    # The production camera-normalization reader requires fixed-size vectors.
    # Arrow's inferred variable lists also widen Python floats to float64.
    vector_types = {'cam_trans': (3, pa.float32()), 'cam_quat': (4, pa.float32()),
                    'cam_fov': (2, pa.float32()), 'state_mask': (2, pa.bool_()),
                    'hand_kept': (2, pa.bool_())}
    columns = {}
    for key, values in combined.items():
        if key in vector_types:
            width, dtype = vector_types[key]
            columns[key] = pa.FixedSizeListArray.from_arrays(
                pa.array(np.asarray(values).reshape(-1), type=dtype), width)
        else:
            columns[key] = pa.array(values)
    pq.write_table(pa.table(columns), parquet)
    ep = destination / 'meta/episodes/chunk-000/file-000.parquet'
    ep.parent.mkdir(parents=True, exist_ok=True)
    pq.write_table(pa.Table.from_pylist(meta), ep)
    record = {'codebase_version': 'v3.0', 'robot_type': 'camera_trajectory', 'fps': fps,
        'total_episodes': len(meta), 'total_frames': start, 'total_tasks': 0,
        'data_path': 'data/chunk-{chunk_index:03d}/file-{file_index:03d}.parquet',
        'video_path': 'videos/{video_key}/chunk-{chunk_index:03d}/file-{file_index:03d}.mp4',
        'features': {'observation.images.ego': {'dtype': 'video', 'shape': shape, 'names': ['height', 'width', 'channels']}}}
    provenance = {'schema': 1, 'label_origin': origin, 'videos': videos,
                  'data': file_identity(parquet), 'episodes': file_identity(ep),
                  'supervision': 'camera pose and FoV only; no synthesized MANO ground truth'}
    if any(file_identity(v['path']) != v for v in videos):
        raise ValueError('Source video changed during data conversion')
    atomic_json(destination / 'source_manifest.json', provenance)
    # Completion marker is last, so a partial directory never looks train-ready.
    atomic_json(destination / 'meta/info.json', record)
    return {'root': str(destination), 'episodes': len(meta), 'frames': start,
            'provenance': provenance, 'identity': file_identity(destination / 'meta/info.json')}


def prepare_existing_camera_dataset(source, destination, *, episode_ids=None, max_frames=None, cancelled=lambda: False):
    """Convert an explicit raw LeRobot annotation source, preserving its origin."""
    import pyarrow.parquet as pq
    from scipy.spatial.transform import Rotation
    from .datasets import select_episode
    # Use the authoritative v3 episode/video resolver; no guessed video filenames.
    from .datasets import catalog
    inventory = catalog(str(source), limit=100)
    ids = episode_ids if episode_ids is not None else [row['episode_index'] for row in inventory['episodes']]
    episodes, sources = [], []
    for index in ids:
        if cancelled():
            raise InterruptedError('Data conversion cancelled at an episode boundary')
        root, info, key, episode, capability = select_episode({'root': str(source), 'episode': int(index), 'video_key': inventory['camera']})
        from .datasets import annotation_identity
        identity = annotation_identity(root, episode, key)
        table = pq.read_table(identity['parquet']['path'])
        if 'episode_index' in table.column_names:
            import pyarrow.compute as pc
            table = table.filter(pc.equal(table['episode_index'], int(index)))
        elif 'start' in episode:
            table = table.slice(int(episode['start']), int(episode['length']))
        elif len(table) != int(episode['length']):
            raise ValueError('Camera labels need explicit episode_index or an authoritative legacy row offset')
        table = table.slice(0, int(episode['length']))
        if max_frames is not None:
            table = table.slice(0, int(max_frames))
        n = len(table)
        def values(name):
            return np.asarray(table[name].to_pylist())
        if {'cam_trans', 'cam_quat', 'cam_fov'} <= set(table.column_names):
            cols = {name: values(name) for name in ('cam_trans', 'cam_quat', 'cam_fov')}
        else:
            if not {'extrinsics_w2c', 'fov'} <= set(table.column_names):
                raise ValueError('Source lacks explicit camera trajectory/FoV annotations')
            transforms = values('extrinsics_w2c').reshape(n, 4, 4)
            if not np.allclose(transforms[:, 3], [0, 0, 0, 1], atol=1e-5) or not np.allclose(np.linalg.det(transforms[:, :3, :3]), 1., atol=1e-4) or not np.allclose(
                transforms[:, :3, :3].transpose(0, 2, 1) @ transforms[:, :3, :3], np.eye(3), atol=1e-4):
                raise ValueError('Source camera annotations are not proper rigid world-to-camera poses')
            cols = {'cam_trans': transforms[:, :3, 3], 'cam_quat': Rotation.from_matrix(transforms[:, :3, :3]).as_quat(), 'cam_fov': values('fov')}
        cols['state_mask'] = np.zeros((n, 2), bool)
        cols['hand_kept'] = np.zeros((n, 2), bool)
        video = episode['video']
        episodes.append({'columns': {k: v.tolist() for k, v in cols.items()}, 'video': video,
                         'video_offset_s': episode.get('video_start', episode.get('start', 0)) / float(info['fps'])})
        sources.append(identity)
    return _write_dataset(destination, episodes, origin={'type': 'existing_declared_camera_annotations',
        'source': str(Path(source).resolve()), 'identities': sources, 'episodes': list(ids),
        'max_frames': max_frames, 'purpose': 'Schema conversion; no new camera labels were inferred'})


def export_reviewed_camera_dataset(manager, dataset, destination, *, cancelled=lambda: False):
    """Export reviewed camera predictions as explicitly declared pseudo labels."""
    from scipy.spatial.transform import Rotation
    from .contracts import load_result
    from .prepare import prepare_sample
    from mint.visualization.encoding import atomic_video_file
    from mint.visualization.draw import H264PipeWriter
    episodes = []
    for number, entry in enumerate(dataset['entries']):
        if cancelled():
            raise InterruptedError('Reviewed export cancelled at an episode boundary')
        request = json.loads(manager.row(entry['run_id'])['payload'])
        result = manager.root / 'runs' / entry['run_id'] / 'result'
        if file_identity(result / 'result.json') != entry['result_identity']:
            raise ValueError('Reviewed result changed before data export')
        metadata, arrays = load_result(result)
        if 'T_world_camera' not in arrays:
            raise ValueError('Camera training requires a valid world camera trajectory')
        sample = prepare_sample(request['sample_request'], manager.root / 'inputs' / request['sample_id'], cancel=cancelled)
        positions = np.arange(entry['start'], entry['end'])
        ids = np.asarray(sample['source_frame_indices'])[positions]
        mapping = np.searchsorted(arrays['source_frame_indices'], ids)
        if np.any(mapping >= len(arrays['source_frame_indices'])) or not np.array_equal(arrays['source_frame_indices'][mapping], ids):
            raise ValueError('Reviewed interval has missing camera frames')
        if not arrays['pose_valid'][mapping].all():
            raise ValueError('Reviewed interval has invalid world camera poses')
        T = np.linalg.inv(arrays['T_world_camera'][mapping])
        K = arrays['K'][mapping]
        if not np.allclose(K[:, 0, 2], sample['width'] / 2, atol=.01) or not np.allclose(K[:, 1, 2], sample['height'] / 2, atol=.01):
            raise ValueError('FoV-only camera supervision requires centered pinhole intrinsics; rectify this calibration explicitly first')
        fov = np.stack([2 * np.arctan(sample['width'] / (2 * K[:, 0, 0])),
                        2 * np.arctan(sample['height'] / (2 * K[:, 1, 1]))], axis=1)
        video = Path(destination).parent / ('reviewed-video-' + dataset['id'][:12] + f'-{number}.mp4')
        frames = np.load(sample['frames_path'], mmap_mode='r', allow_pickle=False)
        def render(path):
            writer = H264PipeWriter(path, sample['fps'], (sample['width'], sample['height']), preset='veryfast')
            try:
                for position in positions:
                    if cancelled():
                        raise InterruptedError('Reviewed export cancelled at a frame boundary')
                    writer.write(np.asarray(frames[position])[:, :, ::-1])
            finally:
                writer.close()
        atomic_video_file(video, render, len(positions))
        frames._mmap.close()
        episodes.append({'video': video, 'columns': {'cam_trans': T[:, :3, 3].tolist(),
            'cam_quat': Rotation.from_matrix(T[:, :3, :3]).as_quat().tolist(), 'cam_fov': fov.tolist(),
            'state_mask': np.zeros((len(T), 2), bool).tolist(), 'hand_kept': np.zeros((len(T), 2), bool).tolist()}})
    return _write_dataset(destination, episodes, origin={'type': 'human_reviewed_pseudo_camera_labels',
        'curation_version': dataset['id'], 'entries': dataset['entries'],
        'warning': 'Predicted trajectories retain model bias; they are not independent ground truth'})
