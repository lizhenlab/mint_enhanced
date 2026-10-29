"""Shared inference parameter validation and explicit smoothing presets."""
from __future__ import annotations

from dataclasses import dataclass
from .hand_smoothing import canonical_hand_mode

VIDEO_VIEWS = ('full', 'left', 'right')
CAMERA_MODES = ('chunked', 'max_chunked', 'streaming', 'full')
SMOOTHING_PRESETS = {
    'production': {'q': .6, 'r': .6, 'beta': 2.0},
    'viewer': {'q': .7, 'r': .5, 'beta': .3},
}


@dataclass(frozen=True)
class InferenceOptions:
    video_view: str = 'full'
    cam_mode: str = 'max_chunked'
    hand_mode: str = 'smooth'

    def __post_init__(self):
        if self.video_view not in VIDEO_VIEWS:
            raise ValueError('Video view must be full, left or right')
        if self.cam_mode not in CAMERA_MODES:
            raise ValueError('Unknown camera mode: ' + str(self.cam_mode))
        object.__setattr__(self, 'hand_mode', canonical_hand_mode(self.hand_mode))
