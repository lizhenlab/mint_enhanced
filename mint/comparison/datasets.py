"""LeRobot episode selection and reference geometry without a second RGB decode."""
from __future__ import annotations

import json
from pathlib import Path

import numpy as np

from mint.runtime import file_identity, digest
from .contracts import transform_points


def dataset_info(root):
    root = Path(root).expanduser().resolve()
    path = root / 'meta/info.json'
    if not path.is_file():
        raise ValueError('选择包含 meta/info.json 的 LeRobot 数据集根目录')
    info = json.loads(path.read_text())
    cameras = [key for key, value in info.get('features', {}).items()
               if value.get('dtype') == 'video' or (key.startswith('observation.images.') and len(value.get('shape', [])) >= 2)]
    if not cameras:
        raise ValueError('LeRobot metadata has no video camera features')
    return root, info, cameras


def episodes(root, video_key=None):
    from eval.model_effect.visualization.reproj_core.lerobot_io import discover_episodes
    root, info, cameras = dataset_info(root)
    video_key = video_key or ('observation.images.ego' if 'observation.images.ego' in cameras else cameras[0])
    if video_key not in cameras:
        raise ValueError('Unknown dataset camera stream')
    records = discover_episodes(root, video_key=video_key)
    return root, info, cameras, video_key, records


def annotation_capability(episode):
    import pyarrow.parquet as pq
    names = set(pq.read_schema(episode['parquet']).names)
    camera = {'cam_trans', 'cam_quat', 'cam_fov'} <= names or {'extrinsics_w2c', 'fov'} <= names
    mano = all(f'{side}_mano_{key}' in names for side in ('left', 'right')
               for key in ('transl_cam', 'orient6d', 'pose6d', 'betas'))
    keypoints = {'left_kpt21', 'right_kpt21', 'hand_kept'} <= names
    if keypoints and not mano:
        root = episode.get('dataset_root')
        info = json.loads((Path(root) / 'meta/info.json').read_text()) if root else {}
        keypoints = info.get('hand_frame') in {'world', 'camera'}
    raw = all(f'{side}_{key}' in names for side in ('left', 'right')
              for key in ('transl_world', 'orient_world', 'hand_pose', 'kept')) and 'observation.state' in names
    ready = camera and ((mano and 'hand_kept' in names) or keypoints or raw)
    return {'ready': ready, 'camera': camera, 'mesh': mano or raw,
            'reason': '' if ready else '需要相机标注、手有效掩码及完整 MANO/21 点字段；缺失手形不使用零值伪造 GT'}


def catalog(root, video_key=None, offset=0, limit=100, episode=None):
    root, info, cameras, video_key, records = episodes(root, video_key)
    offset, limit = max(0, int(offset)), max(1, min(500, int(limit)))
    if episode is not None:
        index = next((i for i, row in enumerate(records) if row['episode_index'] == int(episode)), None)
        if index is None:
            raise ValueError('Episode is unavailable for the selected camera')
        offset = index // limit * limit
    rows = []
    capabilities = {}
    for record in records[offset:offset + limit]:
        key = record['parquet']
        if key not in capabilities:
            capabilities[key] = annotation_capability(record)
        rows.append({'episode_index': record['episode_index'], 'length': record['length'],
                     'video': record['video'], 'video_start': record.get('video_start', record.get('start', 0)),
                     'annotation': capabilities[key]})
    return {'root': str(root), 'camera': video_key, 'cameras': cameras, 'fps': info.get('fps'),
            'total': len(records), 'offset': offset, 'episodes': rows,
            'joint_contract': 'MINT LeRobot/OpenPose21; MANO-decoded geometry preferred when available'}


def select_episode(spec):
    if not isinstance(spec, dict) or set(spec) - {'root', 'episode', 'video_key', 'attach_gt', 'use_calibration'}:
        raise ValueError('Invalid dataset selection')
    for flag in ('attach_gt', 'use_calibration'):
        if flag in spec and not isinstance(spec[flag], bool):
            raise ValueError(flag + ' must be a boolean')
    if isinstance(spec.get('episode'), bool) or float(spec['episode']) != int(spec['episode']):
        raise ValueError('Episode index must be an integer')
    root, info, cameras, key, records = episodes(spec['root'], spec.get('video_key'))
    number = int(spec['episode'])
    episode = next((row for row in records if row['episode_index'] == number), None)
    if episode is None:
        raise ValueError('Episode is unavailable for the selected camera')
    capability = annotation_capability(episode)
    if spec.get('attach_gt') and not capability['ready']:
        raise ValueError(capability['reason'])
    return root, info, key, episode, capability


def annotation_identity(root, episode, video_key):
    return {'root': str(root), 'episode': int(episode['episode_index']), 'video_key': video_key,
            'metadata': file_identity(Path(root) / 'meta/info.json'),
            'parquet': file_identity(episode['parquet']), 'video': file_identity(episode['video']),
            'video_start': int(episode.get('video_start', episode.get('start', 0))),
            'length': int(episode['length']), 'schema': 1}


def annotated_intrinsics(episode, positions, fallback):
    """Prefer actual pixel intrinsics over the legacy centred-FoV approximation."""
    import pyarrow.parquet as pq
    if 'intrinsics' not in pq.read_schema(episode['parquet']).names:
        return np.broadcast_to(fallback, (len(positions), 3, 3)).copy(), 'LeRobot FoV-derived centred pinhole intrinsics'
    kwargs = {} if 'start' in episode else {'filters': [('episode_index', '=', int(episode['episode_index']))]}
    table = pq.read_table(episode['parquet'], columns=['intrinsics'], **kwargs)
    values = np.asarray(table.column('intrinsics').combine_chunks().to_pylist(), np.float64).reshape(-1, 3, 3)
    rows = np.asarray(positions, np.int64) + int(episode.get('start', 0))
    K = values[rows].copy()
    if not np.isfinite(K).all() or np.any(K[:, (0, 1), (0, 1)] <= 0) or not np.allclose(K[:, 2], [0, 0, 1]):
        raise ValueError('Invalid annotated pixel intrinsics')
    if not np.allclose(K[:, (0, 1), (1, 0)], 0):
        raise ValueError('Annotated camera must use the zero-skew pinhole result contract')
    return K, 'LeRobot annotated pixel intrinsics'


def resolve_dataset_input(spec, *, source=None, view='full', target_fps=0, start_frame=0, max_frames=None, camera=None):
    from decord import VideoReader
    from mint.inference.frame_store import sample_indices
    root, info, key, episode, capability = select_episode(spec)
    if view != 'full':
        raise ValueError('LeRobot 相机流已定义视角，请选择完整画面而非再次裁剪双目')
    path = str(Path(episode['video']).resolve())
    if source and str(Path(source).expanduser().resolve()) != path:
        raise ValueError('Dataset episode and selected source video differ')
    if isinstance(start_frame, bool) or int(start_frame) != float(start_frame):
        raise ValueError('Episode start must be an integer')
    relative_start = int(start_frame)
    if not 0 <= relative_start < int(episode['length']):
        raise ValueError('起始帧超出当前 Episode')
    reader = VideoReader(path, num_threads=1)
    begin = int(episode.get('video_start', episode.get('start', 0)))
    end = begin + int(episode['length'])
    if end > len(reader):
        raise ValueError('Episode metadata extends beyond the source video')
    indices, _ = sample_indices(len(reader), float(reader.get_avg_fps()), float(target_fps))
    selected = indices[(indices >= begin + relative_start) & (indices < end)]
    if max_frames not in (None, ''):
        if isinstance(max_frames, bool) or int(max_frames) != float(max_frames) or int(max_frames) < 1:
            raise ValueError('Maximum frames must be a positive integer')
        selected = selected[:int(max_frames)]
    if not len(selected):
        raise ValueError('Episode selection has no source frames at this FPS')
    identity = annotation_identity(root, episode, key)
    if spec.get('use_calibration'):
        from eval.model_effect.visualization.reproj_core.lerobot_io import load_episode_raw
        raw = load_episode_raw(episode, max_frames=relative_start + 1, decode_video=False, video_key=key)
        h, w = raw['hw']
        calibration, _ = annotated_intrinsics(episode, [int(selected[0]) - begin], raw['K'])
        derived = {'K': calibration[0].tolist(), 'width': w, 'height': h,
                   'source': f'LeRobot {root.name} episode {episode["episode_index"]}; explicit K-given protocol'}
        if camera and camera != derived:
            raise ValueError('Choose dataset calibration or manual calibration, not both')
        camera = derived
    context = {'selection': {**spec, 'root': str(root), 'video_key': key, 'episode': int(episode['episode_index'])},
               'identity': identity, 'relative_start': relative_start,
               'requested_max_frames': None if max_frames in (None, '') else int(max_frames),
               'annotation': capability, 'reference_only': not bool(spec.get('use_calibration'))}
    return {'source': path, 'view': 'full', 'target_fps': target_fps,
            'start_frame': int(selected[0]), 'max_frames': len(selected), 'camera': camera}, context


def reference_arrays(sample, context):
    from eval.model_effect.visualization.reproj_core.lerobot_io import load_episode_raw
    from mint.visualization import mano
    root, info, key, episode, capability = select_episode(context['selection'])
    expected = context['identity']
    if annotation_identity(root, episode, key) != expected:
        raise ValueError('Dataset annotations or video changed after task creation')
    if not capability['ready']:
        raise ValueError(capability['reason'])
    ids = np.asarray(sample['source_frame_indices'], np.int64)
    positions = ids - expected['video_start']
    if np.any(positions < 0) or np.any(positions >= episode['length']):
        raise ValueError('Prepared sample escaped its dataset Episode')
    raw = load_episode_raw(episode, max_frames=int(positions.max()) + 1, decode_video=False, video_key=key)
    if tuple(raw['hw']) != (sample['height'], sample['width']):
        raise ValueError('Dataset camera metadata and decoded video pixel grids differ')
    cameras = np.asarray(raw['cam_c2w'])[positions]
    kept = np.asarray(raw['kept'], bool)[positions]
    joints, vertices = None, None
    if raw['hands']:
        all_joints, all_vertices = [], []
        for side in ('left', 'right'):
            value = raw['hands'][side]
            decoded = mano.decode_hand_6d(*(np.asarray(value[k])[positions] for k in ('transl_cam', 'orient6d', 'pose6d', 'betas')), side == 'right')
            verts, points = mano.run_mano(decoded['trans'], decoded['rot'], decoded['hand_pose'], decoded['betas'], side == 'right')
            all_joints.append(points[:, :21]);all_vertices.append(verts)
        joints, vertices = np.stack(all_joints, axis=1), np.stack(all_vertices, axis=1)
        # Budget MANO fields explicitly end in _cam. Legacy export stores world MANO.
        import pyarrow.parquet as pq
        camera_hands = 'left_mano_transl_cam' in pq.read_schema(episode['parquet']).names
        if not camera_hands:
            joints = transform_points(np.linalg.inv(cameras), joints)
            vertices = transform_points(np.linalg.inv(cameras), vertices)
    elif raw['kpt21_gt'] is not None:
        joints = np.asarray(raw['kpt21_gt'])[positions]
        if raw['hand_frame'] == 'world':
            joints = transform_points(np.linalg.inv(cameras), joints)
        elif raw['hand_frame'] != 'camera':
            raise ValueError('Declare camera/world for dataset keypoint coordinates')
    if joints is None:
        raise ValueError('Dataset has no supported reference hand geometry')
    valid = kept & np.isfinite(joints).all(axis=(-1, -2))
    if vertices is not None:
        valid &= np.isfinite(vertices).all(axis=(-1, -2))
    joints = np.asarray(joints, np.float32).copy();joints[~valid] = np.nan
    K, K_source = annotated_intrinsics(episode, positions, raw['K'])
    arrays = {'joints_camera_m': joints, 'geometry_valid': valid, 'presence': kept,
              'K': K,
              'T_world_camera': cameras.copy(), 'pose_valid': np.isfinite(cameras).all(axis=(1, 2)),
              'source_frame_indices': ids, 'timestamps_s': np.asarray(sample['timestamps_s'])}
    arrays['T_world_camera'][~arrays['pose_valid']] = np.nan
    if vertices is not None:
        vertices = np.asarray(vertices, np.float32).copy();vertices[~valid] = np.nan
        arrays['vertices_camera_m'] = vertices
    metadata = {'reference_role': 'user_declared_ground_truth', 'dataset_reference': context,
                'camera_source': 'LeRobot annotated camera; source provenance retained',
                'K_source': K_source + '; distortion must already be removed in the source video',
                'joint_source': 'MANO decoded annotation' if vertices is not None else 'Dataset OpenPose21 annotation',
                'performance_scope': 'Annotation import only; no neural inference'}
    if annotation_identity(root, episode, key) != expected:
        raise ValueError('Dataset changed while loading annotations')
    return arrays, metadata
