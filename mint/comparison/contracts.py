"""The interchange format contains geometry, never framework/model objects."""
from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path

import numpy as np

from mint.runtime import atomic_json, digest, file_identity

SCHEMA = 1
HANDS = ['left', 'right']
CONNECTIONS = [(0, 1), (1, 2), (2, 3), (3, 4), (0, 5), (5, 6), (6, 7), (7, 8),
               (0, 9), (9, 10), (10, 11), (11, 12), (0, 13), (13, 14), (14, 15), (15, 16),
               (0, 17), (17, 18), (18, 19), (19, 20)]


def positive_number(value, name, *, zero=False):
    if isinstance(value, (bool, np.bool_)):
        raise ValueError(f'{name} must be a number, not a boolean')
    value = float(value)
    if not np.isfinite(value) or value < 0 or (value == 0 and not zero):
        raise ValueError(f'{name} must be finite and {"nonnegative" if zero else "positive"}')
    return value


def integer_number(value, name, *, minimum=0, maximum=None):
    """Validate form/API integers before conversion; never truncate a protocol."""
    if isinstance(value, (bool, np.bool_)):
        raise ValueError(f'{name} must be an integer, not a boolean')
    try:
        number = float(value)
        integer = int(value)
    except (TypeError, ValueError, OverflowError):
        raise ValueError(f'{name} must be a finite integer') from None
    if not np.isfinite(number) or integer != number or integer < minimum or (maximum is not None and integer > maximum):
        raise ValueError(f'{name} must be an integer from {minimum}' + (f' to {maximum}' if maximum is not None else ''))
    return integer


def source_request(source, *, view='full', target_fps=0, max_frames=None, start_frame=0, camera=None):
    path = Path(source).expanduser().resolve()
    if not path.is_file() or path.suffix.lower() not in {'.mp4', '.mov', '.avi', '.mkv', '.webm', '.m4v'}:
        raise ValueError('Select a readable video file')
    if view not in {'full', 'left', 'right'}:
        raise ValueError('view must be full, left or right')
    start_frame = integer_number(start_frame, 'start_frame')
    if max_frames not in (None, ''):
        max_frames = integer_number(max_frames, 'max_frames', minimum=1)
    else:
        max_frames = None
    calibration = None
    if camera is not None:
        if not isinstance(camera, dict):
            raise ValueError('camera must describe K in the original source pixel grid')
        K = np.asarray(camera.get('K'), dtype=float)
        width = integer_number(camera.get('width', 0), 'camera width', minimum=1)
        height = integer_number(camera.get('height', 0), 'camera height', minimum=1)
        if K.shape != (3, 3) or not np.isfinite(K).all() or min(K[0, 0], K[1, 1], width, height) <= 0:
            raise ValueError('camera requires finite 3x3 K and positive width/height')
        if not np.allclose(K[2], [0, 0, 1]) or not np.allclose([K[0, 1], K[1, 0]], 0):
            raise ValueError('Only an undistorted pinhole K with zero skew is supported')
        if not str(camera.get('source', '')).strip():
            raise ValueError('Declare the calibration source')
        calibration = {'K': K.tolist(), 'width': width, 'height': height, 'source': str(camera['source'])}
    result = {'schema': SCHEMA, 'source': file_identity(path), 'view': view,
              'target_fps': positive_number(target_fps, 'target_fps', zero=True),
              'max_frames': max_frames, 'start_frame': int(start_frame), 'camera': calibration}
    return {**result, 'id': digest(result)}


def transform_points(transforms, points):
    transforms, points = np.asarray(transforms), np.asarray(points)
    rotated = np.einsum('tij,t...j->t...i', transforms[:, :3, :3], points)
    return rotated + transforms[:, :3, 3].reshape((len(points),) + (1,) * (points.ndim - 2) + (3,))


def validate_geometry(arrays):
    """Do not promote a missing hand, pose, confidence or observation to a success."""
    required = {'joints_camera_m', 'geometry_valid', 'timestamps_s', 'source_frame_indices', 'K'}
    if not required <= arrays.keys():
        raise ValueError('Missing result fields: ' + ', '.join(sorted(required - arrays.keys())))
    joints = np.asarray(arrays['joints_camera_m'])
    n = len(joints)
    if n < 1 or joints.shape != (n, 2, 21, 3):
        raise ValueError('Expected OpenPose21 geometry [T,2,21,3] in metres')
    for key, value in arrays.items():
        if np.asarray(value).dtype.kind not in 'bifu':
            raise ValueError(f'{key} must be a numeric non-object array')
    valid = np.asarray(arrays['geometry_valid'])
    if valid.dtype.kind != 'b' or valid.shape != (n, 2):
        raise ValueError('geometry_valid must be boolean [T,2]')
    if not np.isfinite(joints[valid]).all() or not np.isnan(joints[~valid]).all():
        raise ValueError('Valid geometry must be finite; missing geometry must be NaN')
    indices, times, K = (np.asarray(arrays[k]) for k in ('source_frame_indices', 'timestamps_s', 'K'))
    if indices.dtype.kind not in 'iu' or indices.shape != (n,) or np.any(indices < 0) or np.any(indices[1:] <= indices[:-1]):
        raise ValueError('Source indices must be strictly increasing integers')
    if times.shape != (n,) or not np.isfinite(times).all() or np.any(np.diff(times) <= 0) or times[0] < 0:
        raise ValueError('timestamps_s must be finite, nonnegative, strictly increasing source times')
    if K.shape != (n, 3, 3) or not np.isfinite(K).all() or np.any(K[:, [0, 1], [0, 1]] <= 0):
        raise ValueError('Each frame requires finite positive pinhole intrinsics')
    if not np.allclose(K[:, 2], [0, 0, 1]) or not np.allclose(K[:, [0, 1], [1, 0]], 0):
        raise ValueError('K must use a zero-skew pinhole pixel grid')
    for key in ('presence', 'detected', 'tracked', 'filled'):
        if key in arrays and (np.asarray(arrays[key]).shape != (n, 2) or np.asarray(arrays[key]).dtype.kind != 'b'):
            raise ValueError(f'{key} must be boolean [T,2] when available')
    for key in ('confidence', 'exists_2d', 'exists_3d'):
        if key in arrays:
            value = np.asarray(arrays[key])
            if value.shape != (n, 2) or not np.isfinite(value).all() or np.any((value < 0) | (value > 1)):
                raise ValueError(f'{key} must contain finite probabilities [T,2]')
    if 'T_world_camera' in arrays:
        T, pose_valid = np.asarray(arrays['T_world_camera']), np.asarray(arrays.get('pose_valid'))
        if T.shape != (n, 4, 4) or pose_valid.shape != (n,) or pose_valid.dtype.kind != 'b':
            raise ValueError('World poses require [T,4,4] and boolean pose_valid [T]')
        good = T[pose_valid]
        if not np.isfinite(good).all() or not np.isnan(T[~pose_valid]).all():
            raise ValueError('Invalid camera poses must remain NaN')
        if len(good) and (not np.allclose(good[:, 3], [0, 0, 0, 1], atol=1e-6)
                or not np.allclose(good[:, :3, :3].transpose(0, 2, 1) @ good[:, :3, :3], np.eye(3), atol=2e-3)
                or not np.allclose(np.linalg.det(good[:, :3, :3]), 1, atol=2e-3)):
            raise ValueError('World camera matrices must be proper rigid transforms')
    elif 'pose_valid' in arrays:
        raise ValueError('pose_valid cannot manufacture a camera trajectory')
    if 'vertices_camera_m' in arrays:
        vertices = np.asarray(arrays['vertices_camera_m'])
        if vertices.shape != (n, 2, 778, 3) or not np.isfinite(vertices[valid]).all() or not np.isnan(vertices[~valid]).all():
            raise ValueError('Vertices must match hand validity and MANO topology')
    return n


def publish_result(directory, arrays, metadata):
    """An immutable completion marker is published only after every array is durable."""
    directory = Path(directory)
    directory.mkdir(parents=True, exist_ok=True, mode=0o700)
    n = validate_geometry(arrays)
    if (directory / 'result.json').exists():
        raise FileExistsError('A completed result is immutable; create a new run identity')
    records = {}
    for name, value in arrays.items():
        if not name.replace('_', '').isalnum():
            raise ValueError('Invalid array field name')
        path = directory / (name + '.npy')
        fd, temporary = tempfile.mkstemp(prefix='.' + name, dir=directory)
        try:
            with os.fdopen(fd, 'wb') as stream:
                np.save(stream, np.asarray(value), allow_pickle=False)
                stream.flush(); os.fsync(stream.fileno())
            os.replace(temporary, path)
        finally:
            Path(temporary).unlink(missing_ok=True)
        records[name] = {'shape': list(np.asarray(value).shape), 'dtype': str(np.asarray(value).dtype), 'file': file_identity(path)}
    record = {**metadata, 'schema': SCHEMA, 'frames': n, 'hands': HANDS, 'joint_order': 'openpose21',
              'units': 'm', 'camera_convention': 'opencv_xright_ydown_zforward', 'arrays': records}
    atomic_json(directory / 'result.json', record)
    return record


def result_metadata(directory):
    """Validate immutable file identities without opening long numerical arrays."""
    directory = Path(directory)
    metadata = json.loads((directory / 'result.json').read_text())
    if metadata.get('schema') != SCHEMA or metadata.get('joint_order') != 'openpose21' or metadata.get('units') != 'm':
        raise ValueError('Unsupported comparison result schema')
    for key, record in metadata['arrays'].items():
        if not key.replace('_', '').isalnum() or file_identity(directory / (key + '.npy')) != record['file']:
            raise ValueError('Published array changed: ' + key)
    return metadata


def load_result(directory, *, verify=True):
    directory = Path(directory)
    metadata = json.loads((directory / 'result.json').read_text())
    if metadata.get('schema') != SCHEMA or metadata.get('joint_order') != 'openpose21' or metadata.get('units') != 'm':
        raise ValueError('Unsupported comparison result schema')
    arrays = {}
    for key, record in metadata['arrays'].items():
        if not key.replace('_', '').isalnum():
            raise ValueError('Unsafe array field')
        path = directory / (key + '.npy')
        if verify and file_identity(path) != record['file']:
            raise ValueError(f'Cached array changed: {key}')
        values = np.load(path, mmap_mode='r', allow_pickle=False)
        if list(values.shape) != record['shape'] or str(values.dtype) != record['dtype']:
            raise ValueError('Array layout changed')
        arrays[key] = values
    return metadata, arrays


def json_values(value):
    """Strict JSON; absent geometry becomes null, not JavaScript's invalid NaN."""
    if isinstance(value, dict):
        return {k: json_values(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [json_values(v) for v in value]
    if isinstance(value, np.ndarray):
        return json_values(value.tolist())
    if isinstance(value, (float, np.floating)):
        return float(value) if np.isfinite(value) else None
    if isinstance(value, np.generic):
        return value.item()
    return value
