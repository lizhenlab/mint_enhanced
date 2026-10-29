"""Compatibility exports for the shared mint.inference implementation."""
from mint.inference import hand_smoothing as _core

__all__ = [name for name in dir(_core) if not name.startswith("_")]

def __getattr__(name):
    return getattr(_core, name)
