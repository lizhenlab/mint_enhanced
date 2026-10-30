"""External repositories are configured dependencies, not vendored model code."""
from __future__ import annotations

import hashlib
import importlib.metadata
import json
import os
from pathlib import Path

from mint.runtime import digest, file_identity

PROJECT = Path(__file__).resolve().parents[2]
ALGORITHMS = {
    'reference': {'label': 'Reference · 声明的 GT', 'root_env': 'MINT_COMPARISON_MINT_ROOT', 'default_root': str(PROJECT),
        'world': True, 'camera_required': False, 'min_free_mib': 0, 'memory_fraction': 0,
        'modes': ['normalized'], 'default_mode': 'normalized', 'sources': [], 'assets': [], 'import_only': True,
        'description': '仅导入标准几何与显式帧映射；来源由用户声明，平台不自动认定标注质量'},
    'mint': {'label': 'MINT', 'root_env': 'MINT_COMPARISON_MINT_ROOT', 'default_root': str(PROJECT),
        'world': True, 'camera_required': False, 'min_free_mib': 16000, 'memory_fraction': .30,
        'modes': ['max_chunked', 'chunked'], 'default_mode': 'max_chunked',
        'sources': ['mint/inference/engine.py', 'mint/inference/idle_engine.py', 'mint/visualization/mano.py',
                    'mint/visualization/geometry.py', 'configs/training/mint_step2.yaml'],
        'assets': ['checkpoints/model.safetensors'], 'description': '原生相机与双手预测；保留 MINT 窗口拼接和平滑'},
    'hawor_full': {'label': 'HaWoR · Full', 'root_env': 'MINT_COMPARISON_HAWOR_ROOT', 'default_root': '/data1/lizhen_algo/hawor',
        'world': True, 'camera_required': False, 'min_free_mib': 22000, 'memory_fraction': .60,
        'modes': ['native'], 'default_mode': 'native',
        'sources': ['scripts/scripts_test_video/detect_track_video.py', 'scripts/scripts_test_video/hawor_video.py',
                    'scripts/scripts_test_video/hawor_slam.py', 'lib/models/hawor.py', 'hawor/utils/process.py', 'deploy/run_demo.py'],
        'assets': ['weights/hawor/checkpoints/hawor.ckpt', 'weights/hawor/checkpoints/infiller.pt',
                   'weights/external/detector.pt', 'weights/external/droid.pth',
                   'thirdparty/Metric3D/weights/metric_depth_vit_large_800k.pth'],
        'description': '检测、手估计、SLAM、尺度与补全；SLAM 失败时保留相机系部分结果'},
    'hawor_camera': {'label': 'HaWoR · Camera only', 'root_env': 'MINT_COMPARISON_HAWOR_ROOT', 'default_root': '/data1/lizhen_algo/hawor',
        'world': False, 'camera_required': False, 'min_free_mib': 18000, 'memory_fraction': .50,
        'modes': ['native'], 'default_mode': 'native',
        'sources': ['scripts/scripts_test_video/detect_track_video.py', 'scripts/scripts_test_video/hawor_video.py',
                    'lib/models/hawor.py', 'hawor/utils/process.py', 'deploy/run_demo.py'],
        'assets': ['weights/hawor/checkpoints/hawor.ckpt', 'weights/external/detector.pt'],
        'description': '仅检测跟踪与原生手部估计，不运行 SLAM 或世界补全'},
    'ace_kfree': {'label': 'ACE · K-free', 'root_env': 'MINT_COMPARISON_ACE_ROOT', 'default_root': '/data1/lizhen_algo/ace_ego_hand',
        'world': False, 'camera_required': False, 'min_free_mib': 22000, 'memory_fraction': .65,
        'modes': ['independent81', 'native_tiled'], 'default_mode': 'independent81',
        'sources': ['ace_ego_hand/inference.py', 'ace_ego_hand/video_vae.py', 'ace_ego_hand/mano_utils.py',
                    'ace_ego_hand/world_export.py', 'ace_ego_hand/long_video.py', 'options/ace_ego_hand_kfree.yml'],
        'assets': ['checkpoints/ace_ego_hand_kfree.pt', 'ckpt/Wan2.2-Fun-5B-Control/diffusion_pytorch_model.safetensors',
                   'ckpt/Wan2.2-Fun-5B-Control/Wan2.2_VAE.pth', 'cache/caption_embed.pt'],
        'description': '自身预测内参；相机系 MANO；81 帧独立片段与原生整段 VAE 明确分开'},
    'ace_kgiven': {'label': 'ACE · K-given', 'root_env': 'MINT_COMPARISON_ACE_ROOT', 'default_root': '/data1/lizhen_algo/ace_ego_hand',
        'world': False, 'camera_required': True, 'min_free_mib': 22000, 'memory_fraction': .65,
        'modes': ['independent81', 'native_tiled'], 'default_mode': 'independent81',
        'sources': ['ace_ego_hand/inference.py', 'ace_ego_hand/video_vae.py', 'ace_ego_hand/mano_utils.py',
                    'ace_ego_hand/world_export.py', 'ace_ego_hand/long_video.py', 'options/ace_ego_hand_k.yml'],
        'assets': ['checkpoints/ace_ego_hand_k.pt', 'ckpt/Wan2.2-Fun-5B-Control/diffusion_pytorch_model.safetensors',
                   'ckpt/Wan2.2-Fun-5B-Control/Wan2.2_VAE.pth', 'cache/caption_embed.pt'],
        'description': '需要显式针孔标定；与 K-free 分别记录，不混合额外输入协议'},
}


def root_for(algorithm):
    spec = ALGORITHMS[algorithm]
    return Path(os.environ.get(spec['root_env'], spec['default_root'])).expanduser().resolve()


def backend_spec(algorithm):
    if algorithm not in ALGORITHMS:
        raise ValueError('Unknown algorithm: ' + str(algorithm))
    spec = ALGORITHMS[algorithm]
    root = root_for(algorithm)
    python = root / '.venv/bin/python'
    missing = [str(p) for p in [python, *[root / p for p in spec['sources'] + spec['assets']]] if not p.is_file()]
    identity = {'source_root': str(root), 'sources': {}, 'assets': {p: file_identity(root / p) for p in spec['assets']}}
    for path in spec['sources']:
        target = root / path
        identity['sources'][path] = hashlib.sha256(target.read_bytes()).hexdigest() if target.is_file() else None
    directories = (['mint/inference', 'mint/visualization', 'model_train/_vendor/lingbot_map'] if algorithm == 'mint' else
                   ['ace_ego_hand', 'options'] if algorithm.startswith('ace') else
                   ['hawor', 'lib', 'scripts', 'thirdparty/DROID-SLAM/droid_slam', 'thirdparty/Metric3D/mono'] if algorithm.startswith('hawor') else [])
    for directory in directories:
        for target in sorted((root / directory).rglob('*')):
            if target.is_file() and target.suffix in {'.py', '.yaml', '.yml', '.cu', '.cpp', '.h'}:
                identity['sources'][str(target.relative_to(root))] = hashlib.sha256(target.read_bytes()).hexdigest()
    identity['interpreter'] = file_identity(python)
    # Native decoders and their licensed MANO assets are also part of the result identity.
    for name in (['assets/mano/mano_left/MANO_LEFT.pkl', 'assets/mano/mano_right/MANO_RIGHT.pkl'] if algorithm == 'mint' else
                 ['_DATA/data/mano/MANO_RIGHT.pkl', '_DATA/data_left/mano_left/MANO_LEFT.pkl'] if algorithm.startswith('hawor') else
                 ['data/mano/mano/MANO_LEFT.pkl', 'data/mano/mano/MANO_RIGHT.pkl'] if algorithm.startswith('ace') else []):
        identity['assets'][name] = file_identity(root / name)
        if not (root / name).is_file():
            missing.append(str(root / name))
    implementation = {name: hashlib.sha256((PROJECT / 'mint/comparison' / name).read_bytes()).hexdigest()
                      for name in ('contracts.py', 'registry.py', 'normalizers.py', 'prepare.py', 'native.py', 'worker.py',
                                   'native_pool.py','native_process.py','native_tasks.py','hawor_parallel.py','read_only.py')}
    identity['adapter'] = implementation
    return {**{k: v for k, v in spec.items() if k not in {'sources', 'assets', 'root_env', 'default_root'}},
            'id': algorithm, 'root': str(root), 'python': str(python), 'ready': not missing,
            'missing': missing, 'identity': identity, 'version': digest(identity)}


def normalize_options(algorithm, options=None):
    from mint.inference.hand_smoothing import canonical_hand_mode
    if algorithm not in ALGORITHMS:
        raise ValueError('Unknown algorithm')
    options = dict(options or {})
    allowed = {'mode', 'hand_mode', 'encode_width', 'stride', 'merge', 'focal', 'analysis_width', 'resource_policy', 'max_gpus'}
    if set(options) - allowed:
        raise ValueError('Unknown algorithm options: ' + ', '.join(sorted(set(options) - allowed)))
    mode = options.get('mode', ALGORITHMS[algorithm]['default_mode'])
    if mode not in ALGORITHMS[algorithm]['modes']:
        raise ValueError('Unsupported algorithm mode')
    policy = options.get('resource_policy', 'shared')
    if policy not in {'shared', 'idle', 'dedicated'}:
        raise ValueError('resource_policy must be shared, idle or dedicated')
    gpus = int(options.get('max_gpus', 1 if algorithm=='reference' else 8))
    if not 1 <= gpus <= 8 or (algorithm=='reference' and gpus!=1):
        raise ValueError('Choose 1..8 GPUs; references use no GPU')
    result = {'mode': mode, 'resource_policy': policy, 'max_gpus': gpus}
    if algorithm == 'mint':
        result['hand_mode'] = canonical_hand_mode(options.get('hand_mode', 'smooth'))
    elif algorithm.startswith('ace'):
        width, stride = int(options.get('encode_width', 832)), int(options.get('stride', 80))
        merge = options.get('merge', 'center')
        if width < 32 or width > 832 or width % 32 or stride not in (40, 80) or merge not in {'keep', 'center', 'blend'}:
            raise ValueError('ACE: encode_width is a 32px multiple up to 832, stride 40/80, merge keep/center/blend')
        result.update(encode_width=width, stride=stride, merge=merge)
    elif algorithm.startswith('hawor'):
        focal = options.get('focal')
        if focal is not None:
            from .contracts import positive_number
            focal = positive_number(focal, 'focal')
        result['focal'] = focal
        width = int(options.get('analysis_width', 832))
        if width != 0 and not 320 <= width <= 1920:
            raise ValueError('HaWoR analysis_width must be 0 for native resolution, or 320..1920')
        result['analysis_width'] = width
    return result
