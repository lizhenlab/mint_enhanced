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


def inference_configuration(config, checkpoint):
    """Only configuration fields consumed by StudentEngine inference, including its actual window."""
    import yaml
    config = Path(config)
    if not config.is_file():
        return None
    cfg = yaml.safe_load(config.read_text())
    from mint.inference.engine import _clip_len_from_ckpt
    window = _clip_len_from_ckpt(str(checkpoint)) or cfg.get('data', {}).get('clip_len') or 16
    model = {key: value for key, value in cfg['model'].items() if not key.startswith('_') and key not in {'pretrained', 'freeze'}}
    return {'model': model, 'size_hw': cfg.get('data', {}).get('size_hw', [378, 518]), 'window': int(window)}


def backend_spec(algorithm, *, checkpoint=None, config=None):
    if algorithm not in ALGORITHMS:
        raise ValueError('Unknown algorithm: ' + str(algorithm))
    spec = ALGORITHMS[algorithm]
    root = root_for(algorithm)
    selected = {}
    assets = list(spec['assets'])
    if checkpoint or config:
        if algorithm != 'mint':
            raise ValueError('Custom checkpoints currently apply to the MINT backend only')
        from eval.model_effect.visualization.viewer import ckpts
        weight = ckpts.resolve_checkpoint_path(checkpoint or str(root / 'checkpoints/model.safetensors'))
        if weight is None:
            raise ValueError('MINT checkpoint is unavailable')
        weight = ckpts.checkpoint_weight_file(weight)
        configuration = Path(config or ckpts.config_for_ckpt(str(weight), str(root / 'configs/training/mint_step2.yaml')) or root / 'configs/training/mint_step2.yaml').expanduser().resolve()
        if not configuration.is_file():
            raise ValueError('The selected MINT configuration is unavailable')
        selected = {'checkpoint': str(weight), 'config': str(configuration)}
        assets = [str(weight)]
    python = root / '.venv/bin/python'
    missing = [str(p) for p in [python, *[root / p for p in spec['sources'] + assets]] if not p.is_file()]
    identity = {'source_root': str(root), 'sources': {}, 'assets': {p: file_identity(root / p) for p in assets}}
    if selected:
        identity['selected_configuration'] = {'file': file_identity(selected['config']), 'sha256': hashlib.sha256(Path(selected['config']).read_bytes()).hexdigest()}
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
    # Installing a different framework does not change the Python executable.
    # Hash small package records without importing frameworks or starting CUDA.
    packages = ('torch-', 'torchvision-', 'numpy-', 'scipy-', 'smplx-', 'decord-', 'einops-',
                'opencv_python', 'safetensors-', 'PyYAML-', 'pyyaml-', 'joblib-')
    environment = {}
    for site in (root / '.venv/lib').glob('python*/site-packages'):
        for distribution in site.glob('*.dist-info'):
            if distribution.name.startswith(packages):
                for filename in ('METADATA', 'WHEEL', 'direct_url.json'):
                    record = distribution / filename
                    if record.is_file():
                        environment[str(record.relative_to(site))] = hashlib.sha256(record.read_bytes()).hexdigest()
    identity['environment'] = environment
    if algorithm == 'mint':
        identity['inference_configuration'] = inference_configuration(
            selected.get('config', root / 'configs/training/mint_step2.yaml'),
            selected.get('checkpoint', root / 'checkpoints/model.safetensors'))
    # Native decoders and their licensed MANO assets are also part of the result identity.
    for name in (['assets/mano/mano_left/MANO_LEFT.pkl', 'assets/mano/mano_right/MANO_RIGHT.pkl'] if algorithm == 'mint' else
                 ['_DATA/data/mano/MANO_RIGHT.pkl', '_DATA/data_left/mano_left/MANO_LEFT.pkl'] if algorithm.startswith('hawor') else
                 ['data/mano/mano/MANO_LEFT.pkl', 'data/mano/mano/MANO_RIGHT.pkl'] if algorithm.startswith('ace') else []):
        identity['assets'][name] = file_identity(root / name)
        if not (root / name).is_file():
            missing.append(str(root / name))
    implementation = {name: hashlib.sha256((PROJECT / 'mint/comparison' / name).read_bytes()).hexdigest()
                      for name in ('contracts.py', 'registry.py', 'normalizers.py', 'prepare.py', 'native.py', 'worker.py',
                                   'native_pool.py','native_process.py','native_tasks.py','hawor_parallel.py','hawor_slam.py','read_only.py')}
    identity['adapter'] = implementation
    if algorithm == 'reference':
        identity['reference_adapter'] = hashlib.sha256((PROJECT / 'mint/comparison/datasets.py').read_bytes()).hexdigest()
    return {**{k: v for k, v in spec.items() if k not in {'sources', 'assets', 'root_env', 'default_root'}},
            'id': algorithm, 'root': str(root), 'python': str(python), 'ready': not missing,
            'missing': missing, 'identity': identity, 'version': digest(identity), **selected,
            **({'label': 'MINT · ' + Path(selected['checkpoint']).parent.name} if selected else {})}


def normalize_options(algorithm, options=None):
    from .contracts import integer_number
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
    gpus = integer_number(options.get('max_gpus', 1 if algorithm=='reference' else 8), 'max_gpus', minimum=1, maximum=8)
    if not 1 <= gpus <= 8 or (algorithm=='reference' and gpus!=1):
        raise ValueError('Choose 1..8 GPUs; references use no GPU')
    result = {'mode': mode, 'resource_policy': policy, 'max_gpus': gpus}
    if algorithm == 'mint':
        result['hand_mode'] = canonical_hand_mode(options.get('hand_mode', 'smooth'))
    elif algorithm.startswith('ace'):
        width = integer_number(options.get('encode_width', 832), 'encode_width', minimum=32, maximum=832)
        stride = integer_number(options.get('stride', 80), 'stride', minimum=1)
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
        width = integer_number(options.get('analysis_width', 832), 'analysis_width', maximum=1920)
        if width != 0 and not 320 <= width <= 1920:
            raise ValueError('HaWoR analysis_width must be 0 for native resolution, or 320..1920')
        result['analysis_width'] = width
    return result


def parameter_schema(algorithm):
    """The same capability declaration drives single, batch and evaluation forms."""
    def choice(name, label, values, default, scope='numerical'):
        return {'name': name, 'label': label, 'choices': values, 'default': default, 'scope': scope}
    def number(name, label, default, minimum=None, maximum=None, step=1, scope='numerical'):
        return {'name': name, 'label': label, 'default': default, 'minimum': minimum, 'maximum': maximum, 'step': step, 'scope': scope}
    fields = []
    if algorithm == 'mint':
        fields += [choice('mode', '相机模式', [['max_chunked', 'Max chunked'], ['chunked', 'Chunked']], 'max_chunked'),
            choice('hand_mode', '手部后处理', [['smooth', 'Production · 0.6 / 0.6 / 2'], ['smooth@0.7,0.5,0.3', 'Viewer · 0.7 / 0.5 / 0.3'], ['blend', '线性融合'], ['hard', '原始硬切']], 'smooth')]
    elif algorithm.startswith('ace'):
        fields += [choice('mode', '序列策略', [['independent81', '独立 81 帧 · 覆盖全片'], ['native_tiled', '原生整段 VAE · 4k+1 截尾']], 'independent81'),
            choice('encode_width', '编码宽度', [['832', '832'], ['640', '640'], ['512', '512']], '832'),
            choice('stride', '独立片段步长', [['80', '80'], ['40', '40']], '80'),
            choice('merge', '片段合并', [['center', 'Center · 保留完整原生预测'], ['keep', 'Keep'], ['blend', 'Blend · SO(3) 融合']], 'center')]
    elif algorithm.startswith('hawor'):
        fields += [number('analysis_width', '工作宽度（0=原尺寸）', 832, 0, 1920),
                   number('focal', '样本像素焦距（留空用原生假定）', '', .0001, None, 'any')]
    if not ALGORITHMS[algorithm].get('import_only'):
        fields += [number('max_gpus', '最多 GPU（按阶段自动调度）', 8, 1, 8, scope='runtime'),
            choice('resource_policy', '资源策略', [['shared', 'Shared · 适度共享'], ['idle', 'Idle · 等待空闲'], ['dedicated', 'Dedicated · 独占对照']], 'shared', scope='runtime')]
    return {'fields': fields, 'accepts_checkpoint': algorithm == 'mint',
            'minimum_frames_by_mode': {'independent81': 81, 'native_tiled': 5} if algorithm.startswith('ace') else {},
            'world': ALGORITHMS[algorithm]['world'], 'requires_calibration': ALGORITHMS[algorithm]['camera_required']}
