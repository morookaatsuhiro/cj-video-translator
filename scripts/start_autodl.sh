#!/usr/bin/env bash
set -euo pipefail
cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.."
cuda_packages=${CJVMT_CUDA12_PACKAGES:-/root/miniconda3/lib/python3.12/site-packages/nvidia}
export CJVMT_LOCAL_OMNI_LD_LIBRARY_PATH="${LD_LIBRARY_PATH:-}"
export LD_LIBRARY_PATH="$cuda_packages/cublas/lib:$cuda_packages/cudnn/lib:$cuda_packages/cuda_runtime/lib:${LD_LIBRARY_PATH:-}"
exec .venv/bin/cjvmt realtime --input file_sim --backend local_omni --port "${CJVMT_PORT:-6006}" "$@"
