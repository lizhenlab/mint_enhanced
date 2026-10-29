"""Command-line prediction runner."""

from __future__ import annotations

import json
import os
import time
from pathlib import Path

import numpy as np

from .acceleration import COMPILE_MODES, FP8_MODES
from .engine import StudentEngine
from .video import read_video
from mint.runtime import StageTimer, atomic_json, atomic_npz, video_identity
from mint.visualization.encoding import atomic_video_file
from .checkpoints import update_task
from .options import SMOOTHING_PRESETS
from .hand_smoothing import canonical_hand_mode, encode_hand_smoothing_mode


def inference_main(argv: list[str] | None = None) -> int:
    import argparse

    parser = argparse.ArgumentParser(description="Run MINT prediction-only inference on one video.")
    parser.add_argument("--input", required=True, help="Input video path")
    parser.add_argument("--output", default="artifacts/inference", help="Output directory")
    parser.add_argument("--checkpoint", required=True, help="Checkpoint file or Accelerate checkpoint directory")
    parser.add_argument(
        "--config", default="configs/training/mint_step2.yaml"
    )
    parser.add_argument("--devices", default="shared", help="shared: conservatively share up to 8 GPUs; idle: idle GPUs only; auto: fixed replicas on all visible GPUs; or cpu / 0,1")
    parser.add_argument("--max-frames", type=int, default=None, help="Optional explicit frame limit; default processes the full video")
    parser.add_argument("--target-fps", type=float, default=0.0, help="0 preserves every source frame; positive values request timeline sampling")
    parser.add_argument("--video-view", choices=('full', 'left', 'right'), default='full', help="Choose a view only for side-by-side stereo inputs")
    parser.add_argument("--frame-cache-dir", default=None, help="Disk directory for decoded long-video frames")
    parser.add_argument("--window", type=int, default=None)
    parser.add_argument(
        "--window-batch-size", type=int, default=None,
        help="Independent windows per forward; compile keeps local batch=1 per GPU",
    )
    parser.add_argument("--camera-mode", choices=("chunked", "max_chunked", "streaming", "full"), default="max_chunked")
    parser.add_argument("--hand-mode", choices=("hard", "blend", "smooth"), default="smooth")
    parser.add_argument("--smoothing-preset", choices=tuple(SMOOTHING_PRESETS), default="production",
                        help="UKF defaults: production=.6,.6,2; viewer=.7,.5,.3 (only smooth mode)")
    parser.add_argument(
        "--compile-mode", choices=("off", *COMPILE_MODES), default="off",
        help="torch.compile mode; use auto for reduce-overhead on CUDA",
    )
    parser.add_argument(
        "--fp8-mode", choices=("off", *FP8_MODES), default="off",
        help="FP8 mode; auto enables dynamic FP8 on supported CUDA devices",
    )
    parser.add_argument(
        "--warmup-passes", type=int, default=0,
        help="Optional compile/CUDA Graph warmup passes before timed inference",
    )
    parser.add_argument("--render-mode", choices=("mesh", "skeleton", "mesh_skel"), default="mesh_skel")
    parser.add_argument("--no-render", action="store_true")
    parser.add_argument("--no-resume", action="store_true", help="Recompute windows instead of reusing saved predictions")
    args = parser.parse_args(argv)
    if args.hand_mode == 'smooth':
        args.hand_mode = canonical_hand_mode(encode_hand_smoothing_mode(SMOOTHING_PRESETS[args.smoothing_preset]))
    if args.devices in {'idle', 'shared'}:
        os.environ.setdefault('MINT_MANO_DEVICE', 'cpu')

    source = Path(args.input).expanduser().resolve()
    output = Path(args.output).expanduser().resolve()
    output.mkdir(parents=True, exist_ok=True)
    def decode_progress(done, total):
        if done == total or done % max(1, total // 20) < 96:
            print(f"[decode] {done}/{total}", flush=True)
    timer = StageTimer()
    with timer.stage('decode_seconds'):
        video = read_video(source, max_frames=args.max_frames, target_fps=args.target_fps,
                           view=args.video_view, cache_dir=args.frame_cache_dir, on_step=decode_progress)
    engine = None
    try:
        with timer.stage('model_load_seconds'):
            engine = StudentEngine(args.config, ckpt=args.checkpoint, devices=args.devices,
                window=args.window, compile_mode=args.compile_mode, fp8_mode=args.fp8_mode)
        warmup = None
        with timer.stage('warmup_seconds'):
            if args.warmup_passes > 0 and engine.compile_mode is not None:
                warmup = engine.warmup_acceleration(window_batch_size=args.window_batch_size or engine.parallel_device_count,
                                                   passes=args.warmup_passes)
        with timer.stage('inference_seconds'):
            prediction = engine.predict(video.frames_rgb, cam_mode=args.camera_mode,
                window_batch_size=args.window_batch_size, hand_mode=args.hand_mode, resume=not args.no_resume,
                cache_identity=video_identity(source, video.frame_indices, video.view, video.fps),
                task_parameters={'kind': 'video', 'source': str(source), 'video_view': video.view,
                    'target_fps': args.target_fps, 'max_frames': args.max_frames, 'checkpoint': str(Path(args.checkpoint).resolve()),
                    'config': str(Path(args.config).resolve()), 'cam_mode': args.camera_mode, 'hand_mode': args.hand_mode})
        acceleration = engine.acceleration_metadata
    finally:
        if engine is not None:
            with timer.stage('model_release_seconds'):
                engine.close()
    task_id = prediction.get('_task', {}).get('id')
    arrays = {key: np.asarray(value) for key, value in prediction.items() if not key.startswith("_")}
    arrays['source_frame_indices'] = video.frame_indices
    arrays['fps'] = np.asarray(video.fps)
    with timer.stage("prediction_write_seconds"):
        atomic_npz(output / "prediction.npz", **arrays)
    if not args.no_render:
        from mint.visualization.render import render_prediction

        try:
            with timer.stage('render_and_video_write_seconds'):
                atomic_video_file(output / 'prediction.mp4',
                    lambda temporary: render_prediction(video.frames_rgb, prediction, temporary, video.fps,
                                                         mode=args.render_mode), len(video.frames_rgb))
        except Exception as exc:
            update_task(task_id, status='failed', stage='render', error=str(exc))
            raise
    elapsed = timer.snapshot()['end_to_end_seconds']
    summary = {
        "schema_version": 2,
        "stages": timer.snapshot(),
        "task": prediction.get("_task"),
        "input_name": source.name,
        "frames": len(video.frames_rgb),
        "fps": video.fps,
        "source_frames": video.source_frames,
        "source_fps": video.source_fps,
        "video_view": video.view,
        "frame_cache": str(video.frames_rgb.filename) if isinstance(video.frames_rgb, np.memmap) else None,
        "source_first_frame": int(video.frame_indices[0]),
        "source_last_frame": int(video.frame_indices[-1]),
        "duration_seconds": round(elapsed, 4),
        "model_load_seconds": round(timer.seconds["model_load_seconds"], 4),
        "inference_seconds": round(timer.seconds["inference_seconds"], 4),
        "camera_mode": args.camera_mode,
        "hand_mode": args.hand_mode,
        "smoothing_preset": args.smoothing_preset if args.hand_mode.startswith('smooth') else None,
        "window_batch_size": args.window_batch_size,
        "acceleration": acceleration,
        "warmup": warmup,
        "timings": prediction.get("_timings"),
        "rendered": not args.no_render,
    }
    atomic_json(output / "summary.json", summary)
    update_task(task_id, status="completed", stage="completed", outputs={"directory": str(output)}, stages=timer.snapshot())
    print(f"Artifacts written to {output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(inference_main())
