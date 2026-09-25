#!/bin/bash
# The build moved to run_batch.py on 25 September 2026, so it runs on Windows
# too. This stays so anything that still calls run-batch.sh keeps working.
#
#   bash run-batch.sh [target]      same as: python run_batch.py [target]
DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PY="${PY:-$DIR/../scraper/.venv/bin/python}"
[ -x "$PY" ] || PY="$(command -v python3)"
exec "$PY" "$DIR/run_batch.py" "$@"
