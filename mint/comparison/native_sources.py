"""Read-only native datasets, immutable MINT-owned preparation and provenance.

Heavy readers and MANO are imported in an owned CPU workflow, not by the web
capability endpoint. Published versions are never evicted as disposable cache.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import subprocess
import time
from pathlib import Path

import numpy as np

from mint.runtime import atomic_json, atomic_npz, digest, file_identity
from mint.inference.cache_budget import file_lock
from .calibration import confidence_mask, pinhole_matrix, resize_intrinsics, rectification_maps, validate_pose_matrices
from .contracts import validate_geometry
from .normalizers import finish_geometry

ADAPTERS = ('egodex', 'dexycb', 'arctic')
SPLITS = {'egodex': ['train', 'test'], 'dexycb': ['train', 'val', 'test'], 'arctic': ['train', 'val', 'test']}
EGO_JOINTS = ['Hand'] + ['Thumb' + v for v in ('Knuckle', 'IntermediateBase', 'IntermediateTip', 'Tip')]
EGO_JOINTS += [finger + 'Finger' + part for finger in ('Index', 'Middle', 'Ring', 'Little')
               for part in ('Knuckle', 'IntermediateBase', 'IntermediateTip', 'Tip')]


def native_source_key(plan):
    return digest({k: plan[k] for k in ('dataset', 'root', 'sequence')})


def source_provenance(manager, source):
    """A converted/sliced copy keeps the identity of its original sequence."""
    with manager.db() as db:
        row = db.execute("SELECT id,payload FROM research_records WHERE kind='dataset' AND json_extract(payload,'$.native.source')=? LIMIT 1",
                         (source['path'],)).fetchone()
    if row:
        native = json.loads(row['payload'])['native']
        return {'source_key': native_source_key(native['plan']), 'official_split': native['plan']['split'],
                'native_dataset_id': row['id']}
    return {'source_key': digest(source), 'official_split': None, 'native_dataset_id': None}


def sha256(path):
    h = hashlib.sha256()
    with Path(path).open('rb') as stream:
        for block in iter(lambda: stream.read(8 * 2**20), b''):
            h.update(block)
    return h.hexdigest()


def inside(root, relative):
    root = Path(root).expanduser().resolve()
    relative = str(relative)
    if Path(relative).is_absolute() or '..' in Path(relative).parts or '\\' in relative:
        raise ValueError('Choose a sequence relative to the selected dataset root')
    path = (root / relative).resolve()
    if not path.is_relative_to(root):
        raise ValueError('Dataset sequence escapes its root')
    return path


def arctic_root(root):
    root = Path(root).expanduser().resolve()
    for candidate in (root, root / 'unpack/arctic_data'):
        if (candidate / 'data/splits_json/protocol_p1.json').is_file():
            return candidate
    raise ValueError('ARCTIC root requires data/splits_json/protocol_p1.json')


def dataset_catalog(root='/dataset'):
    root = Path(root).expanduser().resolve()
    source = root / 'DATASET_CATALOG_20261016.json'
    records = json.loads(source.read_text()).get('entries', []) if source.is_file() else [
        {'dataset': p.name, 'path': str(p)} for p in sorted(root.iterdir()) if p.is_dir()]
    result = []
    for row in records:
        name = row['dataset'].lower()
        adapter = next((a for a in ADAPTERS if name == a), None)
        path = Path(row['path'])
        working = 'archive' not in row.get('role', '') or 'working_tree' in row.get('role', '')
        supported = bool(adapter and working and path.is_dir() and row.get('status') != 'duplicates_removed')
        result.append({**row, 'adapter': adapter, 'available': path.is_dir(), 'native_import': supported,
                       'splits': SPLITS.get(adapter, []), 'training_export': adapter in {'egodex', 'arctic'},
                       'status_detail': 'Native adapter available' if supported else
                       'Metadata only' if 'metadata_only' in row.get('status', '') else 'Requires a dataset-specific adapter or working-tree path'})
    return {'root': str(root), 'catalog_identity': file_identity(source), 'datasets': result,
            'adapters': list(ADAPTERS), 'scope': 'Presence/format inventory, not recursive content verification'}


def sequences(dataset, root, *, group='', split='test', offset=0, limit=50):
    if dataset not in ADAPTERS:
        raise ValueError('Choose a supported native dataset')
    if split not in SPLITS[dataset]:
        raise ValueError('Choose an official native partition: ' + ', '.join(SPLITS[dataset]))
    root = Path(root).expanduser().resolve()
    offset, limit = max(0, int(offset)), min(100, max(1, int(limit)))
    groups, rows = [], []
    if dataset == 'egodex':
        partitions = [p.name for p in root.iterdir() if p.is_dir() and
                      (bool(re.fullmatch(r'part[1-5]', p.name)) if split == 'train' else p.name == 'test')]
        if not group:
            groups = [str(p.relative_to(root)) for part in sorted(partitions)
                      for p in sorted((root / part).iterdir()) if p.is_dir()]
        else:
            folder = inside(root, group)
            if Path(group).parts[0] not in partitions:
                raise ValueError('Choose a group in the selected official partition')
            rows = [{'sequence': str(p.relative_to(root).with_suffix('')), 'name': p.stem}
                    for p in sorted(folder.iterdir()) if p.suffix == '.hdf5']
    elif dataset == 'arctic':
        root = arctic_root(root)
        if split not in {'train', 'val', 'test'}:
            raise ValueError('ARCTIC split is train, val or test')
        values = json.loads((root / 'data/splits_json/protocol_p1.json').read_text()).get(split, [])
        rows = [{'sequence': name, 'name': name, 'has_mano': (root / 'data/raw_seqs' / (name + '.mano.npy')).is_file()}
                for name in sorted(values)]
    else:
        if not group:
            groups = [p.name for p in sorted(root.iterdir()) if p.is_dir() and 'subject-' in p.name and
                      ('test' if int(p.name.rsplit('-', 1)[1]) in (8, 9) else 'val' if int(p.name.rsplit('-', 1)[1]) == 7 else 'train') == split]
        else:
            folder = inside(root, group)
            rows = [{'sequence': str(cam.relative_to(root)), 'name': f'{seq.name}/{cam.name}'}
                    for seq in sorted(folder.iterdir()) if seq.is_dir()
                    for cam in sorted(seq.iterdir()) if cam.is_dir() and any(cam.glob('color_*.jpg'))]
    return {'dataset': dataset, 'root': str(root), 'groups': groups, 'sequences': rows[offset:offset + limit],
            'total': len(rows), 'offset': offset, 'split': split}


def source_plan(dataset, root, sequence, *, split='test', start_frame=0, max_frames=None, training=False):
    if dataset not in ADAPTERS:
        raise ValueError('Supported native datasets: ' + ', '.join(ADAPTERS))
    if split not in {'train', 'val', 'test'}:
        raise ValueError('Declare train, val or test')
    if split not in SPLITS[dataset]:
        raise ValueError('No official native partition named ' + split + ' for ' + dataset)
    if training and split != 'train':
        raise ValueError('Training conversion requires the official train partition')
    if training and dataset == 'dexycb':
        raise ValueError('This DexYCB adapter provides camera-frame joint GT, not a world trajectory/MANO training dataset')
    if isinstance(start_frame, bool) or int(start_frame) != start_frame or isinstance(max_frames, bool):
        raise ValueError('Frame counts must be integers')
    if max_frames not in (None, '') and int(max_frames) != max_frames:
        raise ValueError('Frame counts must be integers')
    start_frame = int(start_frame)
    max_frames = None if max_frames in (None, '') else int(max_frames)
    if start_frame < 0 or max_frames is not None and max_frames <= 0:
        raise ValueError('Choose a nonnegative start and positive or omitted frame count')
    root = arctic_root(root) if dataset == 'arctic' else Path(root).expanduser().resolve()
    seq = inside(root, sequence)
    files = []
    if dataset == 'egodex':
        partition = Path(sequence).parts[0]
        if (split == 'train' and not re.fullmatch(r'part[1-5]', partition)) or (split != 'train' and partition != 'test'):
            raise ValueError('EgoDex split and native partition do not match')
        files = [seq.with_suffix('.hdf5'), seq.with_suffix('.mp4')]
    elif dataset == 'dexycb':
        parts = Path(sequence).parts
        if len(parts) != 3 or 'subject-' not in parts[0]:
            raise ValueError('DexYCB sequence is subject/recording/camera')
        subject = int(parts[0].rsplit('-', 1)[1])
        expected = 'test' if subject in (8, 9) else 'val' if subject == 7 else 'train'
        if split != expected:
            raise ValueError('DexYCB s1 subject partition mismatch; expected ' + expected)
        files = [seq.parent / 'meta.yml', root / f'calibration/intrinsics/{parts[-1]}_640x480.yml']
        images = sorted(seq.glob('color_*.jpg'))
        if not images:
            raise ValueError('DexYCB selected camera has no RGB images')
        selected = images[start_frame:None if max_frames is None else start_frame + max_frames]
        files += [p for image in selected for p in (image, image.with_name('labels_' + image.stem[6:] + '.npz'))]
    else:
        values = json.loads((root / 'data/splits_json/protocol_p1.json').read_text()).get(split, [])
        if sequence not in values:
            raise ValueError('ARCTIC sequence is not in the selected official P1 split')
        files = [root / 'data/splits_json/protocol_p1.json', root / 'data/meta/misc.json',
                 root / ('data/raw_seqs/' + sequence + '.mano.npy'),
                 root / ('data/raw_seqs/' + sequence + '.egocam.dist.npy')]
        folder = root / 'data/cropped_images' / sequence / '0'
        images = sorted(folder.glob('*.jpg'))
        offset = int(json.loads((root / 'data/meta/misc.json').read_text())[sequence.split('/')[0]]['ioi_offset'])
        files += [p for p in images if int(p.stem) >= start_frame + offset and
                  (max_frames is None or int(p.stem) < start_frame + offset + max_frames)]
    implementation = [Path(__file__), Path(__file__).with_name('calibration.py')]
    if training:
        implementation.append(Path(__file__).with_name('training_data.py'))
    if dataset == 'arctic':
        from mint.visualization import mano
        implementation += [Path(mano.__file__), Path(mano.__file__).with_name('geometry.py')]
        files += [mano._MANO_RIGHT_DIR / 'MANO_RIGHT.pkl', mano._MANO_LEFT_DIR / 'MANO_LEFT.pkl']
    missing = [str(p) for p in files if not p.is_file()]
    if missing:
        raise ValueError('Native source files missing: ' + ', '.join(missing[:3]))
    return {'dataset': dataset, 'root': str(root), 'sequence': str(sequence), 'split': split,
            'start_frame': start_frame, 'max_frames': max_frames, 'training': bool(training),
            'files': [file_identity(p) for p in files], 'adapter_revision': sha256(__file__),
            'calibration_revision': sha256(Path(__file__).with_name('calibration.py')),
            'implementation': {str(p): sha256(p) for p in implementation}}


def _selection(n, plan):
    start = plan['start_frame']; stop = n if plan['max_frames'] is None else min(n, start + plan['max_frames'])
    if not 0 <= start < stop:
        raise ValueError('Native frame selection is empty')
    return np.arange(start, stop, dtype=np.int64)


def _timeline(n, h, w, fps):
    return {'frames': n, 'height': h, 'width': w, 'source_frame_indices': np.arange(n), 'timestamps_s': np.arange(n) / fps}


def _encode(images, target, fps, cancel, progress):
    """Stream exact RGB into a lossless RGB video; never filter/pad timestamps."""
    from decord import VideoReader
    iterator = iter(images); first = np.asarray(next(iterator), np.uint8)
    h, w = first.shape[:2]
    partial = target.with_name('.' + target.stem + '.partial.mp4')
    command = ['ffmpeg', '-nostdin', '-v', 'error', '-f', 'rawvideo', '-pix_fmt', 'rgb24', '-s', f'{w}x{h}',
               '-r', str(fps), '-i', 'pipe:0', '-an', '-c:v', 'libx264rgb', '-crf', '0', '-preset', 'fast', '-threads', '2', '-y', str(partial)]
    process = subprocess.Popen(command, stdin=subprocess.PIPE, stderr=subprocess.PIPE)
    from itertools import chain
    n = 0
    try:
        for image in chain([first], iterator):
            if cancel():
                raise InterruptedError('Native dataset conversion cancelled')
            image = np.asarray(image, np.uint8)
            if image.shape != first.shape:
                raise ValueError('Native sequence pixel grid changed')
            process.stdin.write(image.tobytes()); n += 1; last = image
            if n % 16 == 0:
                progress(stage='native_rgb', done=n)
        process.stdin.close()
        if process.wait() != 0:
            raise RuntimeError(process.stderr.read().decode()[-1500:])
        reader = VideoReader(str(partial), num_threads=1)
        if len(reader) != n:
            raise ValueError('Prepared video frame count changed')
        np.testing.assert_array_equal(reader[0].asnumpy(), first)
        np.testing.assert_array_equal(reader[n - 1].asnumpy(), last)
        os.replace(partial, target)
    finally:
        if process.poll() is None:
            process.terminate()
            try:
                process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                process.kill(); process.wait()
        if process.stderr:
            process.stderr.close()


def _ego(plan, folder, cancel, progress):
    import h5py
    from decord import VideoReader
    seq = inside(plan['root'], plan['sequence'])
    original = seq.with_suffix('.mp4'); labels = seq.with_suffix('.hdf5')
    reader = VideoReader(str(original), num_threads=1); fps = float(reader.get_avg_fps())
    h, w = reader[0].shape[:2]
    with h5py.File(labels, 'r') as data:
        if len(data['transforms/camera']) != len(reader):
            raise ValueError('EgoDex HDF5 and MP4 frame counts differ')
        ids = _selection(len(reader), plan)
        poses = validate_pose_matrices(np.asarray(data['transforms/camera'][ids], float))
        K = pinhole_matrix(data['camera/intrinsic'][()]); w2c = np.linalg.inv(poses)
        joints = np.empty((len(ids), 2, 21, 3), np.float32); conf = np.ones((len(ids), 2, 21), np.float32)
        for slot, side in enumerate(('left', 'right')):
            world = np.stack([data['transforms/' + side + key][ids, :3, 3] for key in EGO_JOINTS], axis=1)
            joints[:, slot] = np.einsum('tij,tkj->tki', w2c[:, :3, :3], world) + w2c[:, None, :3, 3]
            if 'confidences' in data:
                conf[:, slot] = np.stack([data['confidences/' + side + key][ids] for key in EGO_JOINTS], axis=1)
        valid = confidence_mask(joints, conf); joints[~valid] = np.nan
        arrays = finish_geometry(_timeline(len(ids), h, w, fps), joints, K, poses=poses,
                                 presence=valid, confidence=conf.min(axis=-1))
    if len(ids) == len(reader) and ids[0] == 0:
        shutil.copyfile(original, folder / 'video.mp4')
    else:
        _encode((reader[int(i)].asnumpy() for i in ids), folder / 'video.mp4', fps, cancel, progress)
    return arrays, K, ids, fps, (h, w), {'label_role': 'ARKit_tracking_reference', 'joint_mapping': EGO_JOINTS,
        'perspective_note': 'Vision Pro synthesized RGB and ARKit skeletal reprojection may differ; not MANO GT'}, None


def _dex(plan, folder, cancel, progress):
    import cv2
    import yaml
    seq = inside(plan['root'], plan['sequence']); root = Path(plan['root'])
    meta = yaml.safe_load((seq.parent / 'meta.yml').read_text())
    sides = meta.get('mano_sides', [])
    if len(sides) != 1 or sides[0] not in {'left', 'right'}:
        raise ValueError('DexYCB requires its explicit single-hand metadata')
    slot = ('left', 'right').index(sides[0]); paths = sorted(seq.glob('color_*.jpg'))
    ids = _selection(len(paths), plan); paths = [paths[i] for i in ids]
    native_ids = np.array([int(p.stem[6:]) for p in paths])
    if np.any(np.diff(native_ids) != 1):
        raise ValueError('Selected DexYCB images are not contiguous; choose a contiguous block to preserve timing')
    calibration = root / ('calibration/intrinsics/' + seq.name + '_640x480.yml')
    text = calibration.read_text().split('\nextrinsics:')[0]
    c = yaml.safe_load(text)['color']; K = pinhole_matrix([[c['fx'], 0, c['ppx']], [0, c['fy'], c['ppy']], [0, 0, 1]])
    n = len(paths); joints = np.full((n, 2, 21, 3), np.nan, np.float32)
    for i, image in enumerate(paths):
        if cancel():
            raise InterruptedError('Native label reading cancelled')
        with np.load(image.with_name('labels_' + image.stem[6:] + '.npz'), allow_pickle=False) as labels:
            j = np.asarray(labels['joint_3d'])[0]
        if np.isfinite(j).all() and (j[:, 2] > 0).all():
            joints[i, slot] = j
    valid = confidence_mask(joints); h, w = cv2.imread(str(paths[0])).shape[:2]
    _encode((cv2.imread(str(p))[:, :, ::-1] for p in paths), folder / 'video.mp4', 30., cancel, progress)
    return finish_geometry(_timeline(n, h, w, 30.), joints, K, presence=valid), K, native_ids, 30., (h, w), {
        'label_role': 'official_camera_joint_annotation', 'hand_side': sides[0], 'official_setup': 's1',
        'invalid_label_frames': int((~valid.any(axis=1)).sum())}, None


def _arctic(plan, folder, cancel, progress):
    import cv2
    from mint.visualization import mano, geometry
    from scipy.spatial.transform import Rotation
    root = Path(plan['root']); sequence = plan['sequence']; subject = sequence.split('/')[0]
    raw = root / 'data/raw_seqs'
    data = np.load(raw / (sequence + '.mano.npy'), allow_pickle=True).item()
    ego = np.load(raw / (sequence + '.egocam.dist.npy'), allow_pickle=True).item()
    n_all = len(data['right']['rot']); ids = _selection(n_all, plan); n = len(ids)
    R = np.asarray(ego['R_k_cam_np'])[ids]; t = np.asarray(ego['T_k_cam_np'])[ids].reshape(n, 3)
    w2c = np.broadcast_to(np.eye(4), (n, 4, 4)).copy(); w2c[:, :3, :3] = R; w2c[:, :3, 3] = t
    poses = np.linalg.inv(validate_pose_matrices(w2c))
    misc = json.loads((root / 'data/meta/misc.json').read_text())[subject]
    offset = int(misc['ioi_offset']); image_dir = root / 'data/cropped_images' / sequence / '0'
    by_number = {int(p.stem): p for p in image_dir.glob('*.jpg')}
    numbers = ids + offset
    if any(int(i) not in by_number for i in numbers):
        raise ValueError('ARCTIC image/pose synchronization has missing frames')
    paths = [by_number[int(i)] for i in numbers]
    first = cv2.imread(str(paths[0])); source_hw = first.shape[:2]; target_hw = (480, 672)
    raw_hw = tuple(reversed(misc['image_size'][0]))
    K_source = resize_intrinsics(ego['intrinsics'], raw_hw, source_hw)
    K, maps = rectification_maps(K_source, ego['dist8'], source_hw, target_hw)
    joints = np.empty((n, 2, 21, 3), np.float32); vertices = np.empty((n, 2, 778, 3), np.float32)
    hands = np.empty((n, 2, 109), np.float32)
    for slot, side in enumerate(('left', 'right')):
        value = data[side]; beta = np.broadcast_to(np.asarray(value['shape']).reshape(1, 10), (n, 10)).copy()
        world_rot = np.asarray(value['rot'])[ids]; local_pose = np.asarray(value['pose'])[ids]
        v_world, j_world = mano.run_mano(np.asarray(value['trans'])[ids], world_rot, local_pose, beta, slot == 1)
        joints[:, slot] = np.einsum('tij,tkj->tki', R, j_world[:, :21]) + t[:, None]
        vertices[:, slot] = np.einsum('tij,tkj->tki', R, v_world) + t[:, None]
        orient = R @ Rotation.from_rotvec(world_rot).as_matrix()
        pose_encoded = local_pose.copy()
        if slot == 0:
            pose_encoded[:, 1::3] *= -1; pose_encoded[:, 2::3] *= -1
        local_rot = Rotation.from_rotvec(pose_encoded.reshape(-1, 3)).as_matrix().reshape(n, 15, 3, 3)
        hands[:, slot] = np.concatenate([joints[:, slot, 0], geometry.mat_to_6d(orient),
                                         geometry.mat_to_6d(local_rot).reshape(n, 90), beta], axis=-1)
    valid = confidence_mask(joints); joints[~valid] = np.nan; vertices[~valid] = np.nan
    def pixels():
        for p in paths:
            image = cv2.imread(str(p))
            if image is None or image.shape[:2] != source_hw:
                raise ValueError('ARCTIC native image grid changed')
            yield cv2.remap(image, *maps, interpolation=cv2.INTER_LINEAR)[:, :, ::-1]
    _encode(pixels(), folder / 'video.mp4', 30., cancel, progress)
    arrays = finish_geometry(_timeline(n, *target_hw, 30.), joints, K, vertices=vertices, poses=poses, presence=valid)
    return arrays, K, numbers, 30., target_hw, {'label_role': 'official_MANO_annotation', 'ioi_offset': offset,
        'pose_frame_indices': ids.tolist(), 'distortion_policy': 'OpenCV rational undistortion into centred virtual pinhole',
        'original_dist8': np.asarray(ego['dist8']).tolist(), 'source_intrinsics': K_source.tolist(),
        'source_hw': list(source_hw), 'camera_frame_rotation': 'identity'}, hands


def _prepare_native(plan, directory, *, cancelled=lambda: False, progress=lambda **_: None):
    """Publish only after pixels, labels, calibration and source checks pass."""
    directory = Path(directory); directory.mkdir(parents=True, exist_ok=True)
    for record in plan['files']:
        if file_identity(record['path']) != record:
            raise ValueError('Native input changed before conversion')
    if any(sha256(p) != value for p, value in plan['implementation'].items()):
        raise ValueError('Native adapter implementation changed')
    arrays, K, ids, fps, (h, w), details, hands = {'egodex': _ego, 'dexycb': _dex, 'arctic': _arctic}[plan['dataset']](
        plan, directory, cancelled, progress)
    validate_geometry(arrays)
    atomic_npz(directory / 'reference.npz', **arrays)
    atomic_npz(directory / 'native-frame-map.npz', native_frame_indices=ids,
               original_timestamps_s=ids / fps, sample_timestamps_s=arrays['timestamps_s'])
    training = None
    if plan['training']:
        from scipy.spatial.transform import Rotation
        from .training_data import _write_dataset
        if 'T_world_camera' not in arrays or not arrays['pose_valid'].all():
            raise ValueError('Camera training requires actual labelled world poses')
        w2c = np.linalg.inv(arrays['T_world_camera']); n = len(ids)
        columns = {'cam_trans': w2c[:, :3, 3], 'cam_quat': Rotation.from_matrix(w2c[:, :3, :3]).as_quat(),
                   'cam_fov': np.tile([2 * np.arctan(h / (2 * K[1, 1])), 2 * np.arctan(w / (2 * K[0, 0]))], (n, 1)),
                   'state_mask': np.zeros((n, 2), bool), 'hand_kept': np.zeros((n, 2), bool)}
        if hands is not None:
            from .calibration import project_points
            xy, front = project_points(arrays['joints_camera_m'], K)
            in_frame = (front & (xy[..., 0] >= 0) & (xy[..., 0] < w) & (xy[..., 1] >= 0) & (xy[..., 1] < h)).any(axis=-1)
            columns.update(hand_kept=arrays['geometry_valid'] & in_frame, state_mask=arrays['geometry_valid'] & in_frame)
            for slot, side in enumerate(('left', 'right')):
                for key, values in zip(('transl_cam', 'orient6d', 'pose6d', 'betas'),
                                       (hands[:, slot, :3], hands[:, slot, 3:9], hands[:, slot, 9:99], hands[:, slot, 99:109])):
                    columns[side + '_mano_' + key] = values
                columns[side + '_kpt21'] = np.nan_to_num(arrays['joints_camera_m'][:, slot]).reshape(n, 63)
        training = _write_dataset(directory / 'lerobot', [{'video': str(directory / 'video.mp4'), 'columns': columns}],
            origin={'type': details['label_role'], 'native_dataset': plan['dataset'], 'split': plan['split'],
                    'source_files': plan['files'], 'frame_map': ids.tolist()},
            hand_supervision='mano' if hands is not None else 'none')
    if cancelled():
        raise InterruptedError('Native dataset conversion cancelled before publication')
    for record in plan['files']:
        if file_identity(record['path']) != record:
            raise ValueError('Native source changed during conversion')
    content = [{'source': row, 'sha256': sha256(row['path'])} for row in plan['files']]
    value = {'id': digest(plan), 'plan': plan, 'details': details, 'frames': len(ids), 'fps': fps, 'width': w, 'height': h,
             'source': str(directory / 'video.mp4'), 'reference': str(directory / 'reference.npz'),
             'camera': {'K': K.tolist(), 'width': w, 'height': h, 'source': plan['dataset'] + ' native calibrated/rectified camera'},
             'native_frame_indices': ids.tolist(), 'valid_gt_hand_frames': arrays['geometry_valid'].sum(axis=0).tolist(),
             'training': training, 'source_content': content, 'supervision': ['camera', 'mano'] if hands is not None else
             ['camera', 'arkit_joints'] if plan['dataset'] == 'egodex' else ['camera_joints'],
             'pixel_contract': 'lossless RGB encoding or exact source MP4; explicit original frame map',
             'reference_identity': file_identity(directory / 'reference.npz')}
    value['video_identity'] = file_identity(directory / 'video.mp4')
    atomic_json(directory / 'native.json', value)
    return value


def prepare_native(plan, directory, *, cancelled=lambda: False, progress=lambda **_: None):
    from .storage import DurableRunBudget
    directory = Path(directory)
    budget = DurableRunBudget(directory.parent)
    budget.maximum = int(float(os.environ.get('MINT_NATIVE_DATA_MAX_GIB', '128')) * 2**30)
    if budget.maximum <= 0:
        raise ValueError('Native data quota must be positive')
    estimate = sum(v['size'] for v in plan['files']) * 3 + 64 * 2**20
    with file_lock(directory.with_suffix('.lock')):
        marker = directory / 'native.json'
        if marker.is_file():
            value = json.loads(marker.read_text())
            if value['plan'] != plan or any(file_identity(r['path']) != r for r in plan['files']):
                raise ValueError('Native dataset version changed')
            for key in ('video_identity', 'reference_identity'):
                if file_identity(value[key]['path']) != value[key]:
                    raise ValueError('Published native artifact changed')
            if value.get('training'):
                trained = value['training']
                records = [trained['identity'], trained['provenance']['data'], trained['provenance']['episodes'],
                           *trained['provenance']['videos']]
                if any(file_identity(r['path']) != r for r in records):
                    raise ValueError('Published native training artifact changed')
            return value
        if directory.exists() and any(directory.iterdir()):
            directory.rename(directory.with_name('.interrupted-' + directory.name + '-' + str(time.time_ns())))
        directory.mkdir(parents=True, exist_ok=True)
        with budget.reserve(marker, estimate):
            return _prepare_native(plan, directory, cancelled=cancelled, progress=progress)
