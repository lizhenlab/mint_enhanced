"""Native MANO decoders stay inside their own environment and repository."""
from __future__ import annotations

import json
from pathlib import Path

import numpy as np

from .contracts import transform_points


def timeline(sample, positions=None):
    positions = np.arange(sample['frames']) if positions is None else np.asarray(positions, dtype=np.int64)
    if positions.ndim != 1 or not len(positions) or np.any(positions < 0) or np.any(positions >= sample['frames']) or np.any(np.diff(positions) <= 0):
        raise ValueError('Result positions must select an ordered subset of the declared sample')
    return {'source_frame_indices': np.asarray(sample['source_frame_indices'], np.int64)[positions],
            'timestamps_s': np.asarray(sample['timestamps_s'], np.float64)[positions]}


def finish_geometry(sample, joints, K, *, positions=None, vertices=None, poses=None, pose_valid=None, **masks):
    joints = np.asarray(joints, np.float32).copy()
    valid = np.isfinite(joints).all(axis=(-1, -2))
    joints[~valid] = np.nan
    n = len(joints)
    K = np.asarray(K, np.float64)
    arrays = {**timeline(sample, positions), 'joints_camera_m': joints, 'geometry_valid': valid,
              'K': np.broadcast_to(K, (n, 3, 3)).copy(), **masks}
    if vertices is not None:
        vertices = np.asarray(vertices, np.float32).copy()
        vertices[~valid] = np.nan
        arrays['vertices_camera_m'] = vertices
    if poses is not None:
        poses = np.asarray(poses, np.float64).copy()
        good = np.isfinite(poses).all(axis=(1, 2)) if pose_valid is None else np.asarray(pose_valid, bool)
        poses[~good] = np.nan
        arrays.update(T_world_camera=poses, pose_valid=good)
    return arrays


def mint_geometry(prediction, sample, positions=None):
    from mint.visualization import mano, geometry
    from mint.visualization.render import prediction_to_hands
    n = len(prediction['hand'])
    joints = np.empty((n, 2, 21, 3), np.float32)
    vertices = np.empty((n, 2, 778, 3), np.float32)
    hands = prediction_to_hands(prediction['hand'])
    for slot, side in enumerate(('left', 'right')):
        value = hands[side]
        decoded = mano.decode_hand_6d(*(value[k] for k in ('transl_cam', 'orient6d', 'pose6d', 'betas')), side == 'right')
        vertices[:, slot], all_joints = mano.run_mano(decoded['trans'], decoded['rot'], decoded['hand_pose'], decoded['betas'], side == 'right')
        joints[:, slot] = all_joints[:, :21]
        raw_valid = np.logical_and.reduce([np.isfinite(value[k]).reshape(n, -1).all(axis=1)
                                         for k in ('transl_cam', 'orient6d', 'pose6d', 'betas')])
        joints[~raw_valid, slot] = np.nan
        vertices[~raw_valid, slot] = np.nan
    poses, K = geometry.decode_camera_pose_enc(prediction['pose_enc'], sample['height'], sample['width'], fov_mean=False)
    masks = {}
    if prediction.get('hand_presence_logits') is not None:
        logits = np.asarray(prediction['hand_presence_logits'])
        masks.update(presence=logits >= 0, confidence=(1 / (1 + np.exp(-np.clip(logits, -80, 80)))).astype(np.float32))
    return finish_geometry(sample, joints, K, positions=positions, vertices=vertices, poses=poses, **masks)


def ace_geometry(prediction, sample, K, positions=None):
    from ace_ego_hand.world_export import camera_geometry
    joints, vertices, decoder = camera_geometry(prediction, device='cpu', vertices=True, chunk_size=128)
    masks = {key: np.asarray(prediction[key], np.float32) for key in ('exists_2d', 'exists_3d') if key in prediction}
    if 'exists_3d' not in masks:
        raise ValueError('ACE results require native exists_3d; no synthetic all-present mask')
    masks.update(presence=masks['exists_3d'] > .5, confidence=masks['exists_3d'])
    return finish_geometry(sample, joints, K, positions=positions, vertices=vertices, **masks)


def hawor_camera_geometry(seq, chunks, sample, K):
    import torch
    from hawor.utils.process import run_mano, run_mano_left
    from hawor.utils.rotation import rotation_matrix_to_angle_axis
    n = sample['frames']
    joints = np.full((n, 2, 21, 3), np.nan, np.float32)
    vertices = np.full((n, 2, 778, 3), np.nan, np.float32)
    covered = np.zeros((n, 2), bool)
    for slot in (0, 1):
        for frames in chunks.get(slot, []):
            frames = np.asarray(frames, np.int64)
            if np.any(frames < 0) or np.any(frames >= n):
                raise ValueError('HaWoR chunk indices escape the declared sample')
            path = Path(seq) / 'cam_space' / str(slot) / f'{frames[0]}_{frames[-1]}.json'
            values = {key: torch.tensor(value, dtype=torch.float32) for key, value in json.loads(path.read_text()).items()}
            go = rotation_matrix_to_angle_axis(values['init_root_orient'])
            hp = rotation_matrix_to_angle_axis(values['init_hand_pose'])
            native = (run_mano_left if slot == 0 else run_mano)(values['init_trans'], go, hp,
                betas=values['init_betas'], use_cuda=False)
            joints[frames, slot] = native['joints'][0, :, :21].detach().cpu().numpy()
            vertices[frames, slot] = native['vertices'][0].detach().cpu().numpy()
            covered[frames, slot] = True
    return finish_geometry(sample, joints, K, vertices=vertices, presence=covered, tracked=covered,
                           filled=np.zeros_like(covered))


def hawor_world_geometry(prediction, sample, K, positions=None, observed=None):
    import torch
    from hawor.utils.process import run_mano, run_mano_left
    trans = np.asarray(prediction['translation'])
    n = trans.shape[1]
    if trans.shape != (2, n, 3) or list(np.asarray(prediction.get('hand_order', ['left', 'right']))) != ['left', 'right']:
        raise ValueError('Unexpected HaWoR world layout / hand order')
    valid = np.asarray(prediction['valid'], bool).T
    poses = np.broadcast_to(np.eye(4), (n, 4, 4)).copy()
    poses[:, :3, :3] = prediction['camera_rotation_c2w']
    poses[:, :3, 3] = prediction['camera_translation_c2w']
    world_joints = np.full((n, 2, 21, 3), np.nan, np.float32)
    world_vertices = np.full((n, 2, 778, 3), np.nan, np.float32)
    for slot in (0, 1):
        for start in range(0, n, 128):
            end = min(n, start + 128)
            arrays = [torch.tensor(np.asarray(prediction[k])[slot:slot+1, start:end], dtype=torch.float32)
                      for k in ('translation', 'root_rotation', 'hand_pose', 'shape')]
            native = (run_mano_left if slot == 0 else run_mano)(*arrays[:3], betas=arrays[3], use_cuda=False)
            world_joints[start:end, slot] = native['joints'][0, :, :21].detach().cpu().numpy()
            world_vertices[start:end, slot] = native['vertices'][0].detach().cpu().numpy()
    world_joints[~valid] = np.nan
    world_vertices[~valid] = np.nan
    camera_joints = transform_points(np.linalg.inv(poses), world_joints)
    camera_vertices = transform_points(np.linalg.inv(poses), world_vertices)
    masks = {'presence': valid}
    if observed is not None:
        observed = np.asarray(observed, bool)
        masks.update(tracked=observed, filled=valid & ~observed)
    return finish_geometry(sample, camera_joints, K, positions=positions, vertices=camera_vertices, poses=poses, **masks)
