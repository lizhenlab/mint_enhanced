"""Cheap input admission checks, using the same frame-selection contract as decode."""
from __future__ import annotations

import json
import os
import shutil
from pathlib import Path

from mint.runtime import file_identity
from mint.inference.frame_store import sample_indices
from .contracts import source_request
from .registry import backend_spec, normalize_options


def probe_video(path):
    try:
        from decord import VideoReader
        reader = VideoReader(str(path), num_threads=1)
        shape = reader[0].shape
        return {'frames': len(reader), 'fps': float(reader.get_avg_fps()), 'height': int(shape[0]), 'width': int(shape[1])}
    except Exception as error:
        raise ValueError('无法读取视频元数据或首帧：' + str(error)) from error


def inspect_request(manager, source, selections, *, view='full', target_fps=0, max_frames=None,
                    start_frame=0, camera=None, dataset=None, title=None, execution=None, _backend_lookup=None, **unknown):
    if unknown:
        raise ValueError('Unknown preflight fields: ' + ', '.join(unknown))
    context = None
    if dataset:
        from .datasets import resolve_dataset_input
        resolved, context = resolve_dataset_input(dataset, source=source, view=view, target_fps=target_fps,
            max_frames=max_frames, start_frame=start_frame, camera=camera)
        source, view, target_fps, max_frames, start_frame, camera = (resolved[k] for k in
            ('source', 'view', 'target_fps', 'max_frames', 'start_frame', 'camera'))
        if dataset.get('attach_gt'):
            from .reference_validity import assert_reference_request_current
            assert_reference_request_current(manager, {'algorithm': 'reference', 'import': {
                'format': 'lerobot_reference', 'dataset_context': context,
                'path': context['identity']['parquet']['path'], 'identity': context['identity']['parquet']}})
            selections = [row for row in selections if row.get('algorithm') != 'reference'] + [{'algorithm': 'reference'}]
    from .execution import execution_options
    execution_options(execution)
    request = source_request(source, view=view, target_fps=target_fps, max_frames=max_frames,
                             start_frame=start_frame, camera=camera)
    camera = request['camera']
    info = probe_video(source)
    indices, fps = sample_indices(info['frames'], info['fps'], request['target_fps'])
    indices = indices[indices >= request['start_frame']]
    if request['max_frames'] is not None:
        indices = indices[:request['max_frames']]
    count = len(indices)
    errors, warnings, backends = [], [], []
    if not count:
        errors.append('所选帧范围为空')
    width = info['width'] if view == 'full' else info['width'] // 2
    if view != 'full' and info['width'] % 2:
        errors.append('双目画面宽度为奇数，无法等宽划分左右眼')
    if camera and (camera['width'] != info['width'] or camera['height'] != info['height']):
        errors.append('标定 width/height 必须对应完整源视频像素网格')
    if not isinstance(selections, list) or not 1 <= len(selections) <= 8:
        raise ValueError('Choose between one and eight algorithm variants')
    for selection in selections:
        if not isinstance(selection, dict) or set(selection) - {'algorithm', 'options', 'checkpoint', 'config'}:
            raise ValueError('Unknown algorithm selection fields')
        algorithm = selection.get('algorithm')
        spec = (_backend_lookup or backend_spec)(algorithm, **{k: selection[k] for k in ('checkpoint', 'config') if selection.get(k)})
        options = normalize_options(algorithm, selection.get('options'))
        reasons = []
        if not spec['ready']:
            reasons.append('缺少依赖或资产：' + ', '.join(spec['missing']))
        if spec['camera_required'] and not camera:
            reasons.append('需要明确提供针孔相机标定')
        if spec.get('import_only') and not (context and dataset.get('attach_gt')):
            reasons.append('Reference 需要显式导入 GT；普通视频不能自动生成 GT')
        if algorithm.startswith('ace'):
            minimum = 81 if options['mode'] == 'independent81' else 5
            if count < minimum:
                reasons.append(f"{options['mode']} 至少需要 {minimum} 帧，当前 {count} 帧")
            if options['mode'] == 'native_tiled' and (count - 1) % 4:
                warnings.append(spec['label'] + ' 原生 VAE 仅处理 4k+1 帧；末尾将明确记录为未覆盖，可改用 independent81')
        if algorithm == 'hawor_full':
            warnings.append('HaWoR Full 包含连续 SLAM，跟踪阶段不能按 GPU 数量等比例加速')
        backends.append({'algorithm': algorithm, 'label': spec['label'], 'ready': not reasons,
                         'errors': reasons, 'version': spec['version'], 'options': options,
                         'minimum_free_mib': spec.get('min_free_mib', 0)})
        errors.extend(spec['label'] + '：' + reason for reason in reasons)
    rgb_bytes = count * width * info['height'] * 3 + 4096
    result_bytes = count * len(backends) * (2 * (21 + 778) * 3 * 4 + 256)
    input_cached = False
    manifest = manager.root / 'inputs' / request['id'] / 'sample.json'
    try:
        cached = json.loads(manifest.read_text())
        input_cached = cached['request'] == request and file_identity(cached['frames_path']) == cached['frames_identity']
    except (OSError, KeyError, ValueError):
        pass
    disk = shutil.disk_usage(manager.root)
    reserve = int(float(os.environ.get('MINT_CACHE_MIN_FREE_GIB', '16')) * 2**30)
    quota = int(float(os.environ.get('MINT_COMPARISON_INPUT_MAX_GIB', '256')) * 2**30)
    needed = (0 if input_cached else rgb_bytes) + result_bytes
    if not input_cached and rgb_bytes > quota:
        errors.append('完整 RGB 缓存超过已配置输入缓存配额；请先调整 MINT_COMPARISON_INPUT_MAX_GIB')
    if disk.free - needed < reserve:
        errors.append('预计输入与几何产物将触及磁盘保留空间，请调整存储位置或释放已确认可删除的缓存')
    if file_identity(source) != request['source']:
        errors.append('预检期间源文件发生变化，请重新选择')
    return {'ready': not errors, 'errors': errors, 'warnings': list(dict.fromkeys(warnings)),
            'sample_id': request['id'], 'source': str(source), 'source_frames': info['frames'],
            'input_identity': context['identity'] if context else request['source'],
            'frames': count, 'source_fps': info['fps'], 'fps': fps, 'width': width, 'height': info['height'],
            'view': view, 'first_source_frame': int(indices[0]) if count else None,
            'last_source_frame': int(indices[-1]) if count else None, 'calibration': bool(camera),
            'gt': bool(context and dataset.get('attach_gt')), 'backends': backends,
            'storage': {'rgb_bytes': rgb_bytes, 'geometry_bytes_estimate': result_bytes, 'input_cached': input_cached,
                'additional_bytes_estimate': needed, 'disk_free_bytes': disk.free, 'reserve_bytes': reserve,
                'note': '估算涵盖 RGB 与标准几何；原生临时文件和编码大小依内容变化，写入时仍执行配额检查'}}


def inspect_collection(manager, *, items, selections, options=None, kind='batch', title=None):
    from mint.runtime import digest
    from .collections import collection_request, validate_collection_reference
    items, options = collection_request(kind, items, selections, options)
    specs = {}
    def lookup(name, **custom):
        key = digest({'algorithm': name, **custom})
        if key not in specs:
            specs[key] = backend_spec(name, **custom)
        return specs[key]
    rows, references = [], []
    for index, item in enumerate(items):
        try:
            row = inspect_request(manager, item.get('source', ''), selections, dataset=item.get('dataset'),
                                  _backend_lookup=lookup, **options)
            if kind == 'evaluation' and not row['gt'] and not item.get('reference'):
                row['errors'].append('量化评测需要显式 GT；无 GT 时请使用批量比较和差异分析')
            if item.get('reference'):
                try:
                    identity = validate_collection_reference(item['reference'], row['frames'] or None, manager=manager)
                    row['reference_identity'] = identity
                    references.append({'index': index, 'identity': identity})
                except (ValueError, OSError) as error:
                    row['errors'].append(str(error))
            row['gt'] = row['gt'] or bool(item.get('reference'))
            row['ready'] = not row['errors']
        except (ValueError, OSError) as error:
            row = {'ready': False, 'source': item.get('source'), 'frames': 0, 'errors': [str(error)], 'warnings': []}
        rows.append({'index': index, **row})
    ready = all(row['ready'] for row in rows)
    receipt = digest({'kind': kind, 'items': items, 'selections': selections, 'options': options,
                      'inputs': [{'sample_id': row.get('sample_id'), 'input_identity': row.get('input_identity'),
                                  'backends': [{'algorithm': spec['algorithm'], 'version': spec['version'], 'options': spec['options']}
                                               for spec in row.get('backends', [])]} for row in rows],
                      'references': references}) if ready else None
    return {'ready': ready, 'preflight_id': receipt, 'inputs': rows, 'total_inputs': len(rows),
            'total_frames': sum(row['frames'] for row in rows),
            'errors': [f"输入 {r['index'] + 1}：{error}" for r in rows for error in r['errors']],
            'warnings': [f"输入 {r['index'] + 1}：{warning}" for r in rows for warning in r['warnings']],
            'reference_inputs': sum(bool(row.get('gt')) for row in rows),
            'calibrated_inputs': sum(bool(row.get('calibration')) for row in rows),
            'note': '逐输入检查，后台仍逐项重验来源身份；RGB 缓存按配额滚动复用，不截断视频'}
