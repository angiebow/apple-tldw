#!/usr/bin/env bash
# Start the FastAPI server on http://127.0.0.1:8000
# The first /segment call downloads the embedding model (~80MB) and is slow;
# subsequent calls are fast.
set -euo pipefail
cd "$(dirname "$0")"

if [ ! -d venv ]; then
  echo "No venv found. Run ./setup.sh first." >&2
  exit 1
fi

exec ./venv/bin/uvicorn server:app --host 127.0.0.1 --port 8000 "$@"
