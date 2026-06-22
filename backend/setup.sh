#!/usr/bin/env bash
# Create an isolated virtualenv and install the BERTopic stack.
# Run once:  ./setup.sh
set -euo pipefail
cd "$(dirname "$0")"

PYTHON="${PYTHON:-python3}"

echo "==> Creating virtualenv in ./venv (using $PYTHON)"
"$PYTHON" -m venv venv

echo "==> Upgrading pip"
./venv/bin/pip install --upgrade pip

echo "==> Installing requirements (this is large: torch, umap, hdbscan...)"
./venv/bin/pip install -r requirements.txt

echo
echo "Done. Start the server with:  ./run.sh"
