#!/usr/bin/env bash
set -euo pipefail
MINT_PROJECT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
cd "$MINT_PROJECT_DIR"
export PYTHONNOUSERSITE=1
export PYTORCH_CUDA_ALLOC_CONF="${PYTORCH_CUDA_ALLOC_CONF:-expandable_segments:False}"
export TMPDIR="$MINT_PROJECT_DIR/.tmp"
export XDG_CACHE_HOME="$MINT_PROJECT_DIR/.cache"
export PIP_CACHE_DIR="$MINT_PROJECT_DIR/.cache/pip"
export VIEWER_CACHE_DIR="${VIEWER_CACHE_DIR:-$MINT_PROJECT_DIR/.cache/viewer}"
export MUJOCO_GL="${MUJOCO_GL:-egl}"
export OMP_NUM_THREADS="${OMP_NUM_THREADS:-8}"
export MKL_NUM_THREADS="${MKL_NUM_THREADS:-8}"
mkdir -p "$TMPDIR" "$XDG_CACHE_HOME" "$VIEWER_CACHE_DIR"
exec "$MINT_PROJECT_DIR/.venv/bin/python" -m mint "$@"
