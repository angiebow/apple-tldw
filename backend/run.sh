#!/usr/bin/env bash
# Serve the highlighter on http://127.0.0.1:8000
set -euo pipefail
cd "$(dirname "$0")"

# shellcheck disable=SC1091
source venv/bin/activate

# Override where the fine-tuned detector/reranker live, if needed.
export TLDW_MODELS_DIR="${TLDW_MODELS_DIR:-../poc-content-highlighter/models}"

exec uvicorn server:app --host 127.0.0.1 --port 8000 --reload
