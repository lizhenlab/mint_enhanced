"""Validate selected training inputs without creating a model or allocating CUDA."""
from __future__ import annotations

from pathlib import Path

from .doctor import Check


def training_inputs(config, root=None):
    import yaml
    root = Path(root or Path(__file__).resolve().parents[1])
    def absolute(value):
        path = Path(value).expanduser()
        return path if path.is_absolute() else root / path
    config = absolute(config)
    cfg = yaml.safe_load(config.read_text())
    checks = []
    roots = cfg.get('data', {}).get('root', [])
    if isinstance(roots, str): roots = [roots]
    for value in roots:
        path = absolute(value['root'] if isinstance(value, dict) else value)
        checks.append(Check('training data', (path/'meta/info.json').is_file(), str(path)))
    for name, value in (('pretrained backbone', cfg.get('model', {}).get('pretrained')),
                        ('initialization', cfg.get('train', {}).get('init_from'))):
        if value:
            path = absolute(value)
            checks.append(Check(name, path.exists(), str(path)))
    return checks
