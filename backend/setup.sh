#!/usr/bin/env bash
# One-time: create ./venv and install the highlighter stack.
set -euo pipefail
cd "$(dirname "$0")"

python3 -m venv venv
# shellcheck disable=SC1091
source venv/bin/activate
pip install --upgrade pip
pip install -r requirements.txt

echo "✅ setup done. Start the server with ./run.sh"
