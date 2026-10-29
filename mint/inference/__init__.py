"""Prediction engine for MINT checkpoints."""

__all__ = ["StudentEngine"]


def __getattr__(name):
    # Telemetry and scheduler tests do not need model or training dependencies.
    if name == "StudentEngine":
        from .engine import StudentEngine
        return StudentEngine
    raise AttributeError(name)

