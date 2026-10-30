#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Internal helper."""
import os
import uuid
import json
import shutil
from pathlib import Path

from utils.logging import rank0_print


def save_checkpoint(accelerator, out_dir: str, step: int, *, model):
    ckpt_dir = os.path.join(out_dir, f"step_{step:08d}")
    from accelerate.utils import broadcast_object_list
    from mint.runtime import atomic_json, file_identity
    completed = Path(ckpt_dir) / 'checkpoint.json'
    if completed.is_file():
        # Periodic and final saves may refer to the same completed step.
        validate_checkpoint(ckpt_dir)
        accelerator.wait_for_everyone()
        return ckpt_dir
    admission = [None]
    if accelerator.is_main_process:
        try:
            unwrapped = accelerator.unwrap_model(model)
            model_bytes = sum(p.numel() * p.element_size() for p in unwrapped.parameters())
            optimizer_bytes = sum(p.numel() * 8 for p in unwrapped.parameters() if p.requires_grad)
            expected = int((model_bytes + optimizer_bytes) * 1.1) + 16 * 2**20
            root = Path(out_dir); root.mkdir(parents=True, exist_ok=True)
            used = sum(p.stat().st_size for p in root.rglob('*') if p.is_file())
            limit = float(os.environ.get('MINT_TRAIN_CHECKPOINT_MAX_GIB', '128')) * 2**30
            reserve = float(os.environ.get('MINT_CACHE_MIN_FREE_GIB', '16')) * 2**30
            if used + expected > limit or shutil.disk_usage(root).free - expected < reserve:
                admission[0] = 'Checkpoint quota/free-space reserve reached; existing complete checkpoints are retained. Adjust MINT_TRAIN_CHECKPOINT_MAX_GIB/storage before resuming.'
        except Exception as error:
            admission[0] = str(error)
    broadcast_object_list(admission, from_process=0)
    if admission[0]:
        raise OSError(admission[0])
    attempt = [uuid.uuid4().hex if accelerator.is_main_process else None]
    broadcast_object_list(attempt, from_process=0)
    temporary = Path(out_dir) / f'.step_{step:08d}.partial-{attempt[0]}'
    accelerator.save_state(str(temporary))
    accelerator.wait_for_everyone()
    if accelerator.is_main_process:
        if Path(ckpt_dir).exists():
            raise FileExistsError('Incomplete checkpoint exists; select a new output directory')
        os.replace(temporary, ckpt_dir)
        atomic_json(completed, {'schema': 1, 'step': step,
            'files': {p.name: file_identity(p) for p in Path(ckpt_dir).iterdir() if p.is_file()},
            'contents': 'Model, optimizer, scheduler and RNG state from Accelerator.save_state'})
    accelerator.wait_for_everyone()
    rank0_print(f"[train]  {ckpt_dir}.")
    return ckpt_dir


def validate_checkpoint(ckpt_dir):
    """New complete checkpoints are self-verifying; explicit legacy resumes remain supported."""
    from mint.runtime import file_identity
    marker = Path(ckpt_dir) / 'checkpoint.json'
    if not marker.exists():
        return
    state = json.loads(marker.read_text())
    if state.get('schema') != 1 or not state.get('files'):
        raise ValueError('Incomplete training checkpoint marker')
    for name, identity in state['files'].items():
        if Path(name).name != name or file_identity(Path(ckpt_dir) / name) != identity:
            raise ValueError('Training checkpoint file changed: ' + name)


def load_checkpoint(accelerator, ckpt_dir: str):
    accelerator.load_state(ckpt_dir)
    rank0_print(f"[train]  {ckpt_dir}.")
