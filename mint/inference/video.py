"""Bounded video decoding for public inference entry points."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import numpy as np

from .frame_store import read_frames, sample_indices


@dataclass(frozen=True)
class VideoBatch:
    frames_rgb: np.ndarray
    fps: float
    source_fps: float
    source_frames: int
    width: int
    height: int
    frame_indices: np.ndarray | None = None
    view: str = 'full'


def read_video(path: str | Path, max_frames: int | None = None, target_fps: float | None = None,
               *, view='full', cache_dir=None, on_step=None, cancel_check=None,
               cancelled_error=None) -> VideoBatch:
    """Decode the whole timeline; long RGB sequences remain on disk instead of RAM."""
    from decord import VideoReader
    source = Path(path).expanduser().resolve()
    if not source.is_file():
        raise FileNotFoundError(f"Video does not exist: {source}")
    import os
    reader = VideoReader(str(source), num_threads=int(os.environ.get('VIEWER_DECORD_THREADS', '8')))
    source_fps = float(reader.get_avg_fps()) or 30.0
    source_count = len(reader)
    indices, output_fps = sample_indices(source_count, source_fps, target_fps, max_frames)
    kwargs = {} if cancelled_error is None else {'cancelled_error': cancelled_error}
    frames = read_frames(reader, indices, source_path=source, view=view, cache_dir=cache_dir,
                         on_step=on_step, cancel_check=cancel_check, **kwargs)
    return VideoBatch(
        frames_rgb=frames,
        fps=output_fps,
        source_fps=source_fps,
        source_frames=source_count,
        width=frames.shape[2],
        height=frames.shape[1],
        frame_indices=indices,
        view=view,
    )

