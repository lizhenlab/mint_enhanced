"""Auditable native-MINT pseudo labels, with bounded Ray CPU publication.

This is a working native-backend production route. It does not impersonate the
separate MegaSAM/MoGe/GeoCalib reference reconstruction pipeline.
"""
from __future__ import annotations

import json
import os
import time
from pathlib import Path

import numpy as np

from mint.runtime import atomic_json, digest, file_identity
from .contracts import load_result


def production_plan(manager, run_ids):
    if not isinstance(run_ids, list) or not 1 <= len(run_ids) <= 1000 or len(set(run_ids)) != len(run_ids):
        raise ValueError('Select 1..1000 distinct completed native MINT results')
    records = []
    for key in run_ids:
        run = manager.run(key, summary=True)
        if run['algorithm'] != 'mint' or run['imported'] or run['status'] != 'completed' or not run['result']:
            raise ValueError('Pseudo-label production requires complete native MINT camera+MANO results')
        folder = manager.root / 'runs' / key
        raw = folder / 'native/mint_native.npz'
        sample_file = manager.root / 'inputs' / run['sample_id'] / 'sample.json'
        sample = json.loads(sample_file.read_text())
        if not raw.is_file() or file_identity(sample['source']['path']) != sample['source']:
            raise ValueError('Raw MANO prediction or original video is unavailable/changed')
        timestamps = np.asarray(sample['timestamps_s'], float)
        if len(timestamps) > 1 and not np.allclose(np.diff(timestamps), 1 / sample['fps'], rtol=.05, atol=.001):
            raise ValueError('Variable-frame-rate input requires explicit target FPS before pseudo-label production')
        from .native_sources import source_provenance
        provenance = source_provenance(manager, sample['source'])
        records.append({'run_id': key, 'sample_id': run['sample_id'], 'raw': file_identity(raw),
                        **provenance,
                        'result': file_identity(folder / 'result/result.json'), 'sample': sample,
                        'model': run.get('checkpoint'), 'options': run['options']})
    return records


def _episode(record, directory, acceleration_limit):
    from scipy.spatial.transform import Rotation
    from decord import VideoReader
    from .native_sources import _encode
    from .prepare import select_view
    directory = Path(directory); directory.mkdir(parents=True, exist_ok=True)
    for key in ('raw', 'result'):
        if file_identity(record[key]['path']) != record[key]:
            raise ValueError('Frozen production result changed')
    metadata, arrays = load_result(Path(record['result']['path']).parent)
    sample = record['sample']; n = sample['frames']
    if len(arrays['source_frame_indices']) != n or not np.array_equal(arrays['source_frame_indices'], sample['source_frame_indices']):
        raise ValueError('Pseudo labels must cover the exact full input timeline')
    if 'T_world_camera' not in arrays or not arrays['pose_valid'].all():
        raise ValueError('Camera pseudo supervision requires an actual predicted complete trajectory')
    with np.load(record['raw']['path'], allow_pickle=False) as raw:
        hands = np.asarray(raw['hand']).reshape(n, 2, 109)
    if not np.isfinite(hands).all():
        raise ValueError('Nonfinite MANO parameters cannot become training labels')
    from .quality import frame_diagnostics
    curves = frame_diagnostics(arrays)
    outlier = np.nan_to_num(curves['wrist_acceleration_m_s2'], nan=0) > acceleration_limit
    keep = arrays['geometry_valid'] & arrays.get('presence', arrays['geometry_valid']) & ~outlier[:, None]
    from .calibration import project_points
    xy = np.empty((n, 2, 21, 2)); positive = np.zeros((n, 2, 21), bool)
    for index in range(n):
        xy[index], positive[index] = project_points(arrays['joints_camera_m'][index], arrays['K'][index])
    inside = positive & (xy[..., 0] >= 0) & (xy[..., 0] < sample['width']) & (xy[..., 1] >= 0) & (xy[..., 1] < sample['height'])
    keep &= positive.all(axis=-1) & inside.any(axis=-1)
    w2c = np.linalg.inv(arrays['T_world_camera'])
    K = np.asarray(arrays['K'])
    if not np.allclose(K[:, [0, 1], [2, 2]], [sample['width'] / 2, sample['height'] / 2], atol=1.):
        raise ValueError('FoV-only training needs centred intrinsics; rectify the input first')
    columns = {'cam_trans': w2c[:, :3, 3], 'cam_quat': Rotation.from_matrix(w2c[:, :3, :3]).as_quat(),
               'cam_fov': np.stack([2 * np.arctan(sample['height'] / (2 * K[:, 1, 1])),
                                    2 * np.arctan(sample['width'] / (2 * K[:, 0, 0]))], axis=-1),
               'hand_kept': keep, 'state_mask': keep}
    for slot, side in enumerate(('left', 'right')):
        for key, start, stop in (('transl_cam', 0, 3), ('orient6d', 3, 9), ('pose6d', 9, 99), ('betas', 99, 109)):
            columns[side + '_mano_' + key] = hands[:, slot, start:stop]
        columns[side + '_kpt21'] = np.nan_to_num(arrays['joints_camera_m'][:, slot]).reshape(n, 63)
    source = sample['source']
    if file_identity(source['path']) != source:
        raise ValueError('Production source video changed')
    reader = VideoReader(source['path'], num_threads=1)
    video = directory / 'video.mp4'
    _encode((select_view(reader[int(i)].asnumpy()[None], sample['request']['view'])[0] for i in sample['source_frame_indices']),
            video, sample['fps'], lambda: False, lambda **_: None)
    label_path = directory / 'labels.npz'
    from mint.runtime import atomic_npz
    atomic_npz(label_path, **columns)
    if file_identity(source['path']) != source:
        raise ValueError('Production source changed during encoding')
    return {'video': str(video), 'labels': str(label_path), 'run_id': record['run_id'],
            'source': source, 'frames': n, 'kept_hand_frames': keep.sum(axis=0).tolist(),
            'masked_motion_outlier_frames': int(outlier.sum()), 'original_timestamps_s': sample['timestamps_s'],
            'source_frame_indices': sample['source_frame_indices'], 'model_provenance': metadata.get('provenance'),
            'video_identity': file_identity(video), 'label_identity': file_identity(label_path)}


def produce(plan, directory, *, acceleration_limit=30., cancelled=lambda: False, progress=lambda **_: None):
    import ray
    from .training_data import _write_dataset
    from .storage import DurableRunBudget
    directory = Path(directory); marker = directory / 'production.json'
    if marker.is_file():
        saved = json.loads(marker.read_text())
        if saved['plan'] != plan or saved['acceleration_limit'] != acceleration_limit:
            raise ValueError('Published production contract changed')
        for episode in saved['episodes']:
            for field in ('video_identity', 'label_identity'):
                if file_identity(episode[field]['path']) != episode[field]:
                    raise ValueError('Published production artifact changed')
        data = saved['dataset']
        identities = [data['identity'], data['provenance']['data'], data['provenance']['episodes'], *data['provenance']['videos']]
        if any(file_identity(r['path']) != r for r in identities):
            raise ValueError('Published production dataset changed')
        return saved
    directory.mkdir(parents=True, exist_ok=True)
    budget = DurableRunBudget(directory.parent)
    budget.maximum = int(float(os.environ.get('MINT_PRODUCTION_MAX_GIB', '128')) * 2**30)
    estimate = sum(r['sample']['frames'] * r['sample']['width'] * r['sample']['height'] * 3 for r in plan) + 128 * 2**20
    # A short alias avoids Ray's UNIX socket path limit while data stays owned.
    alias = Path('/tmp') / f'mint-ray-{os.getuid()}-{digest(str(directory))[:10]}'
    ray_root = directory / 'ray'; ray_root.mkdir(exist_ok=True)
    if alias.is_symlink():
        if alias.resolve() != ray_root.resolve():
            raise ValueError('Ray alias belongs to another workflow')
    elif alias.exists():
        raise ValueError('Ray alias is already occupied')
    else:
        alias.symlink_to(ray_root.resolve(), target_is_directory=True)
    pending = []
    try:
        with budget.reserve(marker, estimate):
            os.environ['RAY_USAGE_STATS_ENABLED'] = '0'
            ray.init(num_cpus=min(8, os.cpu_count() or 2), num_gpus=0, include_dashboard=False, _node_ip_address='127.0.0.1',
                     object_store_memory=128 * 2**20, _temp_dir=str(alias), logging_level='ERROR')
            remote = ray.remote(num_cpus=2, max_retries=1)(_episode)
            # Bound outstanding work and result objects; video/arrays remain on disk.
            iterator = iter(enumerate(plan)); episodes = []
            def submit():
                try:
                    index, record = next(iterator)
                except StopIteration:
                    return False
                pending.append(remote.remote(record, str(directory / f'episodes/{index:05d}'), acceleration_limit)); return True
            for _ in range(min(4, len(plan))):
                submit()
            while pending:
                if cancelled():
                    raise InterruptedError('Pseudo-label production cancelled')
                ready, pending = ray.wait(pending, num_returns=1, timeout=.5)
                for result in ready:
                    episodes.append(ray.get(result)); submit()
                    progress(stage='ray_publish', done=len(episodes), total=len(plan))
            episodes.sort(key=lambda v: next(i for i, r in enumerate(plan) if r['run_id'] == v['run_id']))
            converted = []
            for episode in episodes:
                with np.load(episode['labels'], allow_pickle=False) as values:
                    converted.append({'video': episode['video'], 'columns': dict(values)})
            target = directory / 'lerobot'
            if target.exists():
                target.rename(directory / ('interrupted-lerobot-' + str(time.time_ns())))
            dataset = _write_dataset(target, converted, origin={'type': 'MINT_native_pseudo_labels', 'plan': plan,
                'cleaning': {'wrist_acceleration_m_s2': acceleration_limit, 'action': 'mask only; no label interpolation'}},
                hand_supervision='pseudo_mano')
            output = {'plan': plan, 'acceleration_limit': acceleration_limit, 'episodes': episodes, 'dataset': dataset,
                'engine': 'Ray CPU publication after shared native GPU inference', 'label_role': 'model_generated_pseudo_labels',
                'reference_pipeline': 'MegaSAM/MoGe/GeoCalib reconstruction is a separate dependency-gated route'}
            atomic_json(marker, output); return output
    finally:
        for ref in pending:
            ray.cancel(ref, force=True)
        ray.shutdown()
        if alias.is_symlink() and alias.resolve() == ray_root.resolve():
            alias.unlink()
