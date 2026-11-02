"""Bounded, read-only inspection of one published frame and its native parameters.

Native caches are evidence, not interchangeable result formats. Imported and
derived results never inherit a neighbouring run's native parameters.
"""
from __future__ import annotations

import json
import re
import threading
import zipfile
import zlib
from collections import OrderedDict
from pathlib import Path

import numpy as np

from mint.runtime import file_identity
from .contracts import integer_number, json_values, load_result
from .results import frame_payload

MAX_JSON_BYTES = 8 * 2**20
MAX_MEMBER_BYTES = 64 * 2**20
MAX_ARCHIVE_BYTES = 128 * 2**20
MAX_OUTPUT_VALUES = 8192
MAX_CACHE_BYTES = 64 * 2**20
MAX_NATIVE_FILES = 4096
_CACHE = OrderedDict()
_CACHE_BYTES = 0
_CACHE_LOCK = threading.RLock()


class NativeUnavailable(ValueError):
    """A normal, displayable lack of safe native evidence."""


def _inside(root, path):
    root, path = Path(root).resolve(), Path(path).resolve()
    if not path.is_relative_to(root):
        raise NativeUnavailable('Native file escapes this run/native directory')
    return path


def _json(path, *, root):
    path = _inside(root, path)
    if path.stat().st_size > MAX_JSON_BYTES:
        raise NativeUnavailable('Native JSON exceeds the read limit')
    value = json.loads(path.read_text())
    if not isinstance(value, dict):
        raise NativeUnavailable('Expected a native JSON object')
    return value


class NativeArchive:
    """Inspect headers before NumPy allocation; cache bounded numeric members.

NPZ compression prevents memory mapping. A small process-local LRU, keyed by
stat identity on every request, prevents reloading a full video during playback.
Only named parameter members are opened, never image/mesh/latent video tensors.
"""
    def __init__(self, root, path):
        self.path = _inside(root, path)
        self.identity = file_identity(self.path)
        if self.identity.get('missing'):
            raise NativeUnavailable('Native parameter file is missing: ' + self.path.name)
        if self.identity['size'] > MAX_ARCHIVE_BYTES:
            raise NativeUnavailable('Native archive exceeds the read limit')
        self.key = tuple(sorted(self.identity.items()))
        self.layouts = {}
        with zipfile.ZipFile(self.path) as archive:
            members = archive.infolist()
            if len(members) > 128 or len({v.filename for v in members}) != len(members):
                raise NativeUnavailable('Native archive has too many or duplicate members')
            if sum(v.file_size for v in members) > MAX_ARCHIVE_BYTES:
                raise NativeUnavailable('Native archive expanded size exceeds the read limit')
            for member in members:
                if not re.fullmatch(r'[A-Za-z][A-Za-z0-9_]*\.npy', member.filename):
                    raise NativeUnavailable('Unsafe native archive member')
                if member.file_size > MAX_MEMBER_BYTES:
                    raise NativeUnavailable('Native array exceeds the member size limit')
                with archive.open(member) as stream:
                    version = np.lib.format.read_magic(stream)
                    if version == (1, 0):
                        shape, fortran, dtype = np.lib.format.read_array_header_1_0(stream, max_header_size=4096)
                    elif version == (2, 0):
                        shape, fortran, dtype = np.lib.format.read_array_header_2_0(stream, max_header_size=4096)
                    else:
                        raise NativeUnavailable('Unsupported native array header')
                    if dtype.hasobject or dtype.kind not in 'bifuUS' or len(shape) > 6:
                        raise NativeUnavailable('Native arrays must be non-object numeric arrays or identity strings')
                    elements = 1
                    for length in shape:
                        if length < 0 or length > 10_000_000:
                            raise NativeUnavailable('Native array shape exceeds the read limit')
                        elements *= length
                    if elements * dtype.itemsize > MAX_MEMBER_BYTES or stream.tell() + elements * dtype.itemsize != member.file_size:
                        raise NativeUnavailable('Native array size/header does not match its archive member')
                    self.layouts[member.filename[:-4]] = (shape, dtype)

    def get(self, name):
        global _CACHE_BYTES
        if name not in self.layouts:
            raise NativeUnavailable('Required native field is missing: ' + name)
        key = (self.key, name)
        with _CACHE_LOCK:
            cached = _CACHE.get(key)
            if cached is not None:
                _CACHE.move_to_end(key)
                return cached
        # allow_pickle=False remains explicit even after independent header checks.
        with np.load(self.path, allow_pickle=False) as values:
            value = values[name].copy()
        if file_identity(self.path) != self.identity:
            raise NativeUnavailable('Native parameter file changed during the read')
        value.flags.writeable = False
        with _CACHE_LOCK:
            if key not in _CACHE:
                while _CACHE and _CACHE_BYTES + value.nbytes > MAX_CACHE_BYTES:
                    _, removed = _CACHE.popitem(last=False)
                    _CACHE_BYTES -= removed.nbytes
                if value.nbytes <= MAX_CACHE_BYTES:
                    _CACHE[key] = value
                    _CACHE_BYTES += value.nbytes
        return value

    def row(self, name, index, shape, *, axis=0):
        layout, dtype = self.layouts.get(name, (None, None))
        if layout != tuple(shape) or dtype.kind not in 'bifu':
            raise NativeUnavailable('Unexpected native field layout: ' + name)
        return np.take(self.get(name), index, axis=axis)


def _field(name, value, source, *, label=None, representation='raw', units=None,
           coordinate_frame=None, note=''):
    value = np.asarray(value)
    if value.size > MAX_OUTPUT_VALUES:
        raise NativeUnavailable('Single-frame native parameter output exceeds the size limit')
    if value.dtype.kind not in 'bifu':
        raise NativeUnavailable('Native parameter output must be numeric')
    return dict(name=name, label=label or name, representation=representation, units=units,
                shape=list(value.shape), value=value, source_file=source,
                coordinate_frame=coordinate_frame, note=note)


def _native(fields, source):
    if sum(np.asarray(v['value']).size for v in fields) > MAX_OUTPUT_VALUES or len(fields) > 64:
        raise NativeUnavailable('Single-frame native parameter output exceeds the size limit')
    return dict(available=bool(fields), reason=None if fields else 'No native parameters exist for this frame',
                source=source, fields=fields)


def _mint(root, frame, n):
    archive = NativeArchive(root, root / 'mint_native.npz')
    hands = archive.row('hand', frame, (n, 218)).reshape(2, 109)
    source = 'native/mint_native.npz'
    slots = 'Native hand slots are [left, right]. Values precede MANO decoding.'
    fields = [_field('hand109', hands, source, label='MINT hand109', representation='packed_mano', note=slots)]
    for name, value, representation, units, coordinates, note in (
        ('transl_cam', hands[:, :3], 'translation', 'm', 'native_camera', 'MANO decoder translation parameter; this is not the wrist joint position.'),
        ('orient6d', hands[:, 3:9], 'rotation_6d', None, 'native_camera', 'Native global orientation input to the MINT MANO decoder.'),
        ('pose6d', hands[:, 9:99].reshape(2, 15, 6), 'rotation_6d', None, 'mano_local', '15 native local joint rotation encodings.'),
        ('betas', hands[:, 99:], 'mano_shape', 'dimensionless', 'mano_shape', '10 per-frame MANO shape coefficients; no temporal average applied.'),
    ):
        fields.append(_field(name, value, source, representation=representation, units=units,
                             coordinate_frame=coordinates, note=slots + ' ' + note))
    fields.append(_field('pose_enc', archive.row('pose_enc', frame, (n, 9)), source,
                         representation='camera_pose_encoding', note='Raw MINT camera encoding; the decoded camera-to-world matrix is in standard.T_world_camera. Native encoding is unchanged by the display origin.'))
    if 'hand_presence_logits' in archive.layouts:
        fields.append(_field('hand_presence_logits', archive.row('hand_presence_logits', frame, (n, 2)), source,
                             representation='logit', note='Native [left, right] logits, before sigmoid and threshold.'))
    return _native(fields, 'MINT native prediction')


def _native_files(root, pattern):
    values = []
    for path in root.glob(pattern):
        values.append(path)
        if len(values) > MAX_NATIVE_FILES:
            raise NativeUnavailable('Native file inventory exceeds the inspection limit')
    return sorted(values)


def _ace(root, run_id, frame, n):
    files = _native_files(root, 'ace_segment_*.npz')
    if not files:
        raise NativeUnavailable('Native ACE segment files are missing')
    fields = []
    for path in files:
        archive = NativeArchive(root, path)
        if 'run_id' not in archive.layouts or archive.get('run_id').shape != () or str(archive.get('run_id')) != run_id:
            raise NativeUnavailable('Native ACE segment belongs to another run or lacks run identity')
        ids = archive.get('frame_indices')
        if ids.dtype.kind not in 'iu' or ids.ndim != 1 or not len(ids) or np.any(ids < 0) or np.any(ids >= n) or np.any(np.diff(ids) <= 0):
            raise NativeUnavailable('Native ACE segment has no valid sample-position mapping')
        index = int(np.searchsorted(ids, frame))
        if index >= len(ids) or ids[index] != frame:
            continue
        source = 'native/' + path.name
        note = 'Raw segment before native segment merge; sample position ' + str(frame) + '. Hand slots are verified by is_right=[0,1]. These parameters need not equal the merged geometry.'
        handedness = archive.row('is_right', index, (len(ids), 2))
        if not np.array_equal(handedness, [0, 1]):
            raise NativeUnavailable('Native ACE left/right slot order is not verified')
        specs = [
            ('go', (2, 1, 3, 3), 'rotation_matrix', None, 'native_camera'),
            ('hp', (2, 15, 3, 3), 'rotation_matrix', None, 'mano_local'),
            ('betas', (2, 10), 'mano_shape', 'dimensionless', 'mano_shape'),
            ('cam_trans', (2, 3), 'translation', 'm', 'native_camera'),
            ('is_right', (2,), 'handedness', None, None),
            ('exists_2d', (2,), 'probability', None, None),
            ('exists_3d', (2,), 'probability', None, None),
            ('joints_2d', (2, 21, 2), 'points_2d', 'px', 'native_encode_pixel_grid'),
            ('joints_cam_direct', (2, 21, 3), 'points_3d', 'm', 'native_camera'),
            ('wrist_cam_direct', (2, 3), 'points_3d', 'm', 'native_camera'),
        ]
        for name, shape, representation, units, coordinates in specs:
            if name in archive.layouts:
                fields.append(_field(path.stem + '.' + name, archive.row(name, index, (len(ids), *shape)), source,
                                     label=name, representation=representation, units=units,
                                     coordinate_frame=coordinates, note=note + (' MANO decoder translation, not wrist position.' if name == 'cam_trans' else '')))
        if 'K_matrix' in archive.layouts:
            if archive.layouts['K_matrix'][0] != (3, 3):
                raise NativeUnavailable('Unexpected native ACE K_matrix layout')
            fields.append(_field(path.stem + '.K_matrix', archive.get('K_matrix'), source, label='K_matrix',
                                 representation='pinhole_intrinsics', units='px', coordinate_frame='native_encode_pixel_grid',
                                 note='Raw segment intrinsics in the encoded image grid; standard.K is in sample pixels.'))
        if len(fields) > 64:
            raise NativeUnavailable('Too many overlapping native ACE segments for a single-frame response')
    return _native(fields, 'ACE raw segment predictions before merge')


def _hawor_world(root, frame, n):
    archive = NativeArchive(root, root / 'hawor_world_native.npz')
    if 'hand_order' not in archive.layouts or archive.get('hand_order').tolist() != ['left', 'right']:
        raise NativeUnavailable('Native HaWoR world hand order is not verified')
    fields, source = [], 'native/hawor_world_native.npz'
    specs = [('translation', (3,), 'translation', 'm', 'native_world'),
             ('root_rotation', (3,), 'axis_angle', 'rad', 'native_world'),
             ('hand_pose', (45,), 'axis_angle', 'rad', 'mano_local'),
             ('shape', (10,), 'mano_shape', 'dimensionless', 'mano_shape'),
             ('valid', (), 'validity', None, None)]
    for name, tail, representation, units, coordinates in specs:
        # Native HaWoR versions may preserve matrix local rotations instead.
        shape = archive.layouts.get(name, (None, None))[0]
        expected = (2, n, *tail)
        if name == 'root_rotation' and shape == (2, n, 3, 3):
            expected, representation, units = shape, 'rotation_matrix', None
        if name == 'hand_pose' and shape in ((2, n, 15, 3), (2, n, 15, 3, 3)):
            expected = shape
            if len(shape) == 5:
                representation, units = 'rotation_matrix', None
        fields.append(_field(name, archive.row(name, frame, expected, axis=1), source,
                             representation=representation, units=units, coordinate_frame=coordinates,
                             note='Native [left, right] world infiller output; world values are unchanged by display origin. Translation is a MANO parameter, not wrist position.'))
    for name, tail, representation, units in [('camera_rotation_c2w', (3, 3), 'rotation_matrix', None),
                                             ('camera_translation_c2w', (3,), 'translation', 'm')]:
        fields.append(_field(name, archive.row(name, frame, (n, *tail)), source,
                             representation=representation, units=units, coordinate_frame='native_world',
                             note='Raw camera-to-world pose; standard.T_world_camera uses the requested display origin.'))
    if 'img_focal' in archive.layouts:
        if archive.layouts['img_focal'][0] != ():
            raise NativeUnavailable('Unexpected native HaWoR focal layout')
        fields.append(_field('img_focal', archive.get('img_focal'), source, representation='focal_length', units='px',
                             coordinate_frame='native_analysis_pixel_grid'))
    return fields


def _hawor_camera(root, run_id, frame, n):
    # frame_chunks_all.npy is joblib/pickle and is deliberately never opened by
    # the web process. The explicit frame mapping itself must be frozen by the
    # native motion marker, either inside each JSON or as the safe hand plan.
    unavailable = ('HaWoR camera frame mapping is unavailable: JSON lacks frozen frame_indices '
                   'and safe hand plan identity is not frozen; pickle chunk indexes are not read')
    if not (root / 'motion.complete.json').is_file():
        raise NativeUnavailable(unavailable + ' (motion-stage marker is missing)')
    marker = _json(root / 'motion.complete.json', root=root)
    if marker.get('run_id') != run_id or not isinstance(marker.get('files'), dict):
        raise NativeUnavailable('HaWoR camera motion-stage identity is unavailable')
    plan_path = _inside(root, root / 'hawor-plan.json')
    frozen_plan = marker['files'].get(str(plan_path)) == file_identity(plan_path) and plan_path.is_file()
    candidates = []
    if frozen_plan:
        plan = _json(plan_path, root=root)
        if plan.get('frames') != n or not isinstance(plan.get('chunks'), list):
            raise NativeUnavailable('Frozen HaWoR hand plan lacks explicit chunk frames')
        if len(plan['chunks']) > MAX_NATIVE_FILES:
            raise NativeUnavailable('HaWoR camera chunk inventory exceeds the read limit')
        for chunk in plan['chunks']:
            if not isinstance(chunk, dict) or isinstance(chunk.get('slot'), bool) or chunk.get('slot') not in (0, 1):
                raise NativeUnavailable('Invalid HaWoR camera hand slot')
            ids = np.asarray(chunk.get('frames'))
            _hawor_frame_ids(ids, n)
            position = int(np.searchsorted(ids, frame))
            if position >= len(ids) or ids[position] != frame:
                continue
            slot = chunk['slot']
            path = root / 'input/cam_space' / str(slot) / f'{ids[0]}_{ids[-1]}.json'
            candidates.append((path, slot, ids))
    else:
        for path in _native_files(root, 'input/cam_space/[01]/*.json'):
            # Names only bound the candidate inventory; names never establish
            # frame-to-row mapping. Only marker-frozen frame_indices do that.
            match = re.fullmatch(r'(\d+)_(\d+)\.json', path.name)
            if match and int(match[1]) <= frame <= int(match[2]):
                candidates.append((path, int(path.parent.name), None))
    fields, slots = [], set()
    for path, slot, planned_ids in candidates:
        path = _inside(root, path)
        if marker['files'].get(str(path)) != file_identity(path):
            raise NativeUnavailable('HaWoR camera JSON no longer matches its native motion-stage identity')
        record = _json(path, root=root)
        ids = np.asarray(record['frame_indices']) if 'frame_indices' in record else planned_ids
        if ids is None:
            raise NativeUnavailable(unavailable)
        _hawor_frame_ids(ids, n)
        if planned_ids is not None and not np.array_equal(ids, planned_ids):
            raise NativeUnavailable('Frozen HaWoR camera JSON and hand plan frame mappings disagree')
        index = int(np.searchsorted(ids, frame))
        if index >= len(ids) or ids[index] != frame:
            continue
        if slot in slots:
            raise NativeUnavailable('Ambiguous overlapping HaWoR camera chunks')
        slots.add(slot)
        for name, tail, representation, units, coordinates in [
            ('init_root_orient', (3, 3), 'rotation_matrix', None, 'native_analysis_camera'),
            ('init_hand_pose', (15, 3, 3), 'rotation_matrix', None, 'mano_local'),
            ('init_trans', (3,), 'translation', 'm', 'native_analysis_camera'),
            ('init_betas', (10,), 'mano_shape', 'dimensionless', 'mano_shape'),
        ]:
            if name not in record:
                continue
            values = np.asarray(record[name])
            if values.dtype.kind not in 'bifu' or values.shape != (1, len(ids), *tail):
                raise NativeUnavailable('Unexpected HaWoR camera JSON parameter layout: ' + name)
            side = ('left', 'right')[slot]
            fields.append(_field(side + '.' + name, values[0, index], 'native/' + str(path.relative_to(root)),
                                 label=side + ' ' + name, representation=representation, units=units,
                                 coordinate_frame=coordinates,
                                 note='Native camera-stage ' + side + ' slot; explicit safe chunk-frame mapping. MANO translation is not wrist position. Analysis camera may be remapped; see input_camera_remap.'))
    if not fields and not frozen_plan:
        raise NativeUnavailable(unavailable)
    return fields


def _hawor_frame_ids(ids, n):
    if ids.dtype.kind not in 'iu' or ids.ndim != 1 or not len(ids) or np.any(ids < 0) or np.any(ids >= n) or np.any(np.diff(ids) <= 0):
        raise NativeUnavailable('Invalid HaWoR camera chunk frame mapping')


def frame_parameters(manager, run_id, frame=0, *, origin_source_frame=None):
    """Return one sample-position frame. Only manager.root and manager.run are used."""
    row = manager.run(run_id)
    if not row.get('result'):
        raise ValueError('This run has no validated result yet')
    # The manager validates run identity. Paths are nevertheless kept local even
    # when this helper is used by a read-only preview manager.
    if not re.fullmatch(r'[a-f0-9]{64}', str(run_id)):
        raise ValueError('Invalid run ID')
    store = Path(manager.root).resolve()
    directory = _inside(store / 'runs', store / 'runs' / run_id)
    sample_id = row['sample_id']
    if not re.fullmatch(r'[A-Za-z0-9_-]{1,128}', str(sample_id)):
        raise ValueError('Invalid sample ID')
    sample = _json(store / 'inputs' / sample_id / 'sample.json', root=store / 'inputs')
    n = integer_number(sample.get('frames'), 'Sample frames', minimum=1)
    frame = integer_number(frame, 'frame', maximum=n - 1)
    if origin_source_frame is not None:
        origin_source_frame = integer_number(origin_source_frame, 'origin')
    metadata, arrays = load_result(directory / 'result')
    if metadata.get('run_id') != run_id or metadata.get('sample_id') != sample_id:
        raise ValueError('Published result identity does not match the requested run and sample')
    from .tools import frame_mapping
    ids, _, _ = frame_mapping(metadata, arrays, sample, sample_id=sample_id)
    payload = frame_payload(directory / 'result', sample, frame, 1, origin_source_frame=origin_source_frame)
    keys = ('available', 'K', 'T_world_camera', 'pose_valid', 'geometry_valid', 'presence', 'detected',
            'tracked', 'filled', 'confidence', 'exists_2d', 'exists_3d')
    standard = {key: payload[key][0] if payload.get(key) is not None else None for key in keys}
    standard.update(K_source=metadata.get('K_source'), camera_source=metadata.get('camera_source'),
                    input_camera_remap=metadata.get('input_camera_remap'),
                    native_analysis_size=metadata.get('native_analysis_size'), encoded_size=metadata.get('encoded_size'))
    native = dict(available=False, reason=None, source=None, fields=[])
    try:
        if row.get('imported') or metadata.get('import_source') or metadata.get('reference_role') == 'user_declared_ground_truth':
            raise NativeUnavailable('Imported/reference result: native model parameters are not claimed or substituted')
        algorithm = row.get('algorithm')
        if algorithm not in ('mint', 'ace_kfree', 'ace_kgiven', 'hawor_camera', 'hawor_full') or metadata.get('algorithm') != algorithm:
            raise NativeUnavailable('Derived or unsupported result: no native parameter source is claimed')
        request = _json(directory / 'request.json', root=directory)
        if request.get('id') != run_id or request.get('sample_id') != sample_id or request.get('algorithm') != algorithm or request.get('import'):
            raise NativeUnavailable('Native request identity does not match this published run and sample')
        if not standard['available']:
            raise NativeUnavailable('The selected sample frame is unavailable in this published run')
        root = _inside(directory, directory / 'native')
        if algorithm == 'mint':
            native = _mint(root, frame, n)
        elif algorithm.startswith('ace_'):
            native = _ace(root, run_id, frame, n)
        else:
            fields, notes = [], []
            if algorithm == 'hawor_full' and (root / 'hawor_world_native.npz').exists():
                fields = _hawor_world(root, frame, n)
            try:
                fields += _hawor_camera(root, run_id, frame, n)
            except (NativeUnavailable, OSError, ValueError, KeyError, TypeError) as exc:
                notes.append(str(exc))
            if not fields:
                raise NativeUnavailable('; '.join(notes) or 'Native HaWoR parameters are missing')
            native = _native(fields, 'HaWoR native parameters')
            native['note'] = '; '.join(notes) if notes else None
    except (NativeUnavailable, OSError, ValueError, KeyError, TypeError, EOFError,
            RuntimeError, NotImplementedError, zipfile.BadZipFile, zlib.error) as exc:
        native['reason'] = str(exc) or 'Native parameter file is unavailable or malformed'
    return json_values(dict(schema=1, run_id=run_id, sample_id=sample_id, sample_position=frame,
        source_frame_index=int(ids[frame]), timestamp_s=sample['timestamps_s'][frame],
        coordinate_conventions=dict(hand_order=['left', 'right'], camera='opencv_xright_ydown_zforward',
            world='native' if origin_source_frame is None else f'camera_at_source_frame_{origin_source_frame}',
            transform='T_world_camera maps camera to world; column vectors', units='m',
            native_origin='Native parameter coordinates are never rebased by the display origin'),
        standard=standard, native=native))
