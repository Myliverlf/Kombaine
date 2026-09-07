#!/usr/bin/env bash
set -euo pipefail
cd /root/prop-desk/strategy_combine
export OMP_NUM_THREADS=1
export MKL_NUM_THREADS=1
export TOKENIZERS_PARALLELISM=false
PY=/root/prop-desk/strategy_combine/.venv-timesfm/bin/python
[ -x "$PY" ] || PY=/usr/bin/python3
exec "$PY" code/strategy_architect_autopilot.py "$@"
