"""Validate complete encoded videos before publishing their final filenames."""
from __future__ import annotations

import json
import os
import subprocess
import tempfile
from pathlib import Path


def atomic_video_file(path, render, expected_frames=None):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    fd, name = tempfile.mkstemp(prefix='.' + path.stem + '.', suffix='.mp4', dir=path.parent)
    os.close(fd)
    temporary = Path(name)
    try:
        render(temporary)
        if temporary.stat().st_size == 0:
            raise RuntimeError('Video encoder produced an empty output')
        if expected_frames is not None:
            validate_video(temporary, expected_frames)
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)
    return path


def validate_video(path, expected_frames=None):
    path = Path(path)
    command = ['ffprobe', '-v', 'error', '-select_streams', 'v:0',
               '-show_entries', 'stream=width,height,nb_frames,avg_frame_rate', '-of', 'json', str(path)]
    probe = subprocess.run(command, capture_output=True, text=True, timeout=30)
    if probe.returncode:
        raise RuntimeError('Encoded video is invalid: ' + probe.stderr[-1500:])
    streams = json.loads(probe.stdout).get('streams', [])
    if not streams or min(int(streams[0].get(k, 0)) for k in ('width', 'height')) <= 0:
        raise RuntimeError('Encoded video has no valid video stream')
    stream = streams[0]
    if expected_frames is not None:
        count = stream.get('nb_frames')
        if count in (None, 'N/A'):
            counted = subprocess.run(['ffprobe', '-v', 'error', '-count_frames', '-select_streams', 'v:0',
                '-show_entries', 'stream=nb_read_frames', '-of', 'json', str(path)],
                capture_output=True, text=True, timeout=300)
            if counted.returncode:
                raise RuntimeError('Could not verify encoded frame count: ' + counted.stderr[-1500:])
            count = json.loads(counted.stdout)['streams'][0]['nb_read_frames']
        if int(count) != int(expected_frames):
            raise RuntimeError(f'Encoded video has {count} frames, expected {expected_frames}')
    return stream
