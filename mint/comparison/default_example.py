"""A read-only, checked preset for the prepared full ARCTIC comparison.

The preset identifies a dataset protocol, never a deployment path or old Run ID.
Only the regular native comparison endpoint may submit it and attach its GT.
"""
from __future__ import annotations

import copy
import json
import math
import re
import sqlite3
from pathlib import Path

from mint.runtime import digest, file_identity

PRESET_ID = 'arctic-full-gt'
FRAMES = 677
SEQUENCE = 's05/box_grab_01'
_MAX_MARKER_BYTES = 4 * 2**20
_MAX_CANDIDATES = 32


def _selections():
    shared = {'max_gpus': 8, 'resource_policy': 'shared'}
    return [
        {'algorithm': 'mint', 'options': {**shared, 'mode': 'max_chunked', 'hand_mode': 'smooth'}},
        *[{'algorithm': algorithm, 'options': {**shared, 'mode': 'native', 'analysis_width': 832, 'focal': None}}
          for algorithm in ('hawor_full', 'hawor_camera')],
        *[{'algorithm': algorithm, 'options': {**shared, 'mode': 'independent81', 'encode_width': 832,
                                             'stride': 80, 'merge': 'center'}}
          for algorithm in ('ace_kfree', 'ace_kgiven')],
    ]


def _integer(value):
    return isinstance(value, int) and not isinstance(value, bool)


def _frame_indices(value, start):
    return isinstance(value, list) and len(value) == FRAMES and all(_integer(v) for v in value) and value == list(range(start, start + FRAMES))


def _identity(record, path):
    if not isinstance(record, dict) or record.get('missing') or record.get('path') != str(path):
        raise ValueError('Published example identity is missing or inconsistent')
    if not path.is_file() or file_identity(path) != record:
        raise ValueError('Published example artifact is missing or changed')


def _checked_native(manager, record):
    if not isinstance(record, dict) or record.get('kind') != 'dataset' or record.get('status') != 'native_prepared':
        raise ValueError('Choose a prepared native dataset')
    if not isinstance(record.get('id'), str) or not re.fullmatch(r'[0-9a-f]{64}', record['id']):
        raise ValueError('Invalid dataset record identity')
    native = record['native']
    if not isinstance(native, dict):
        raise ValueError('Native sample metadata is missing')
    plan, details = native['plan'], native['details']
    if not isinstance(plan, dict) or not isinstance(details, dict):
        raise ValueError('Native plan or annotation metadata is missing')
    if any(plan.get(key) != value for key, value in {
            'dataset': 'arctic', 'split': 'val', 'sequence': SEQUENCE}.items()):
        raise ValueError('Different dataset protocol')
    if not _integer(plan.get('start_frame')) or plan['start_frame'] != 0 or 'max_frames' not in plan or plan['max_frames'] is not None:
        raise ValueError('The default example must cover the complete sequence')
    if plan.get('training') is not False or native.get('training') is not None:
        raise ValueError('The example is a validation input, not a training dataset')
    if any(not _integer(value) or value != FRAMES for value in (native.get('frames'), record.get('frames'))):
        raise ValueError('The full example must contain 677 frames')
    if record.get('split') != 'val' or details.get('label_role') != 'official_MANO_annotation':
        raise ValueError('Official validation annotations are required')
    if not isinstance(native.get('supervision'), list) or not {'camera', 'mano'}.issubset(native['supervision']):
        raise ValueError('Camera and hand annotations are required')
    if native.get('valid_gt_hand_frames') != [FRAMES, FRAMES] or any(
            not _integer(value) for value in native.get('valid_gt_hand_frames', [])):
        raise ValueError('The example must retain its full two-hand reference')
    offset = details.get('ioi_offset')
    if not _integer(offset) or offset < 0 or not _frame_indices(native.get('native_frame_indices'), offset) or not _frame_indices(details.get('pose_frame_indices'), 0):
        raise ValueError('The original-frame mapping is not complete')
    fps = native['fps']
    if isinstance(fps, bool) or not isinstance(fps, (int, float)) or not math.isfinite(fps) or fps != 30:
        raise ValueError('The ARCTIC example must retain its source FPS')
    native_id = native.get('id')
    if not isinstance(native_id, str) or native_id != digest(plan):
        raise ValueError('Native version identity does not match its plan')
    # The workflow publishes here; do not turn registry metadata into arbitrary
    # filesystem reads or accept a historic validation staging directory.
    folder = (Path(manager.root) / 'native-sources' / native_id).resolve()
    if folder.parent != (Path(manager.root) / 'native-sources').resolve():
        raise ValueError('Example artifacts must belong to the native sample store')
    marker = folder / 'native.json'
    _identity(record.get('identity'), marker)
    if marker.stat().st_size > _MAX_MARKER_BYTES:
        raise ValueError('Example publication marker is too large')
    published = json.loads(marker.read_text())
    if published != native:
        raise ValueError('Registry and native publication marker disagree')
    for filename, identity_key, path_key in (
            ('video.mp4', 'video_identity', 'source'),
            ('reference.npz', 'reference_identity', 'reference'),
            ('native-frame-map.npz', 'frame_map_identity', None)):
        path = folder / filename
        if path_key and native.get(path_key) != str(path):
            raise ValueError('Example publication paths disagree')
        _identity(native.get(identity_key), path)
    camera = native['camera']
    if not isinstance(camera, dict) or any(
            not _integer(native.get(key)) or not _integer(camera.get(key)) or native[key] <= 0 or native[key] > 8192 or camera[key] != native[key]
            for key in ('width', 'height')):
        raise ValueError('Camera and video grids disagree')
    from .calibration import pinhole_matrix
    K = pinhole_matrix(camera['K'])
    if abs(K[0, 2] - native['width'] / 2) > .01 or abs(K[1, 2] - native['height'] / 2) > .01 or not isinstance(camera.get('source'), str) or not camera['source'].strip():
        raise ValueError('The rectified calibration is incomplete')
    return native


def get_default_example(manager, registry):
    """Return an executable draft only for an unchanged, prepared full sample.

    This reads bounded registry rows and four small artifact identities. It does
    not prepare data, probe GPUs, decode video/NPZ, or create comparison jobs.
    Submission still uses ``POST /api/comparison/native/<dataset_id>/compare``;
    that endpoint rechecks the frozen source and atomically aligns its GT.
    """
    unavailable = {'id': PRESET_ID, 'available': False,
                   'reason': '完整 ARCTIC 默认示例尚未准备好，请先在“数据 → 开源数据集 / GT”准备 s05/box_grab_01 的完整 Validation 样本。'}
    try:
        with manager.db() as db:
            rows = db.execute('''SELECT id FROM research_records WHERE kind='dataset' AND json_valid(payload)
                AND json_extract(payload,'$.status')='native_prepared'
                AND json_extract(payload,'$.native.plan.dataset')='arctic'
                AND json_extract(payload,'$.native.plan.split')='val'
                AND json_extract(payload,'$.native.plan.sequence')=?
                AND json_extract(payload,'$.native.plan.start_frame')=0
                AND json_type(payload,'$.native.plan.max_frames')='null'
                AND json_extract(payload,'$.native.frames')=?
                ORDER BY created DESC,id LIMIT ?''', (SEQUENCE, FRAMES, _MAX_CANDIDATES)).fetchall()
    except (OSError, ValueError, sqlite3.Error):
        return unavailable
    for row in rows:
        try:
            record = registry.get(row[0], 'dataset')
            native = _checked_native(manager, record)
        except (OSError, ValueError, TypeError, KeyError, OverflowError):
            continue
        return {'id': PRESET_ID, 'available': True, 'reason': '', 'dataset_id': record['id'],
                'name': 'ARCTIC Validation · s05/box_grab_01 · 完整677帧 · 5算法 + GT',
                'description': '完整677帧、30 FPS；使用数据集已知内参，并附加官方双手与相机 GT。',
                'frames': native['frames'], 'fps': native['fps'], 'source': native['source'],
                'camera': copy.deepcopy(native['camera']), 'view': 'full', 'target_fps': 0,
                'start_frame': 0, 'max_frames': None, 'calibration': True,
                'selections': _selections(), 'execution': {'cache': 'exact', 'priority': 'interactive'},
                'reference': {'attach': True, 'role': 'official_MANO_annotation',
                              'label': '官方双手 MANO / 关节 / 网格 / 相机 GT', 'frames': FRAMES}}
    if rows:
        unavailable['reason'] = '完整 ARCTIC 示例的文件或版本校验未通过，请在“数据 → 开源数据集 / GT”重新准备完整样本。'
    return unavailable
