#!/usr/bin/env bash
# Assemble a self-contained Hugging Face Docker Space repo from the monorepo.
# Usage (from repo root):  ./backend/cloud/assemble_space.sh /tmp/vireel-space
set -euo pipefail

DEST="${1:-/tmp/vireel-space}"
ROOT="$(cd "$(dirname "$0")/../.." && pwd)"   # repo root (tldw/)

echo "Assembling Space at $DEST from $ROOT"
mkdir -p "$DEST"

# Dockerfile + Space README at the Space root.
cp "$ROOT/backend/cloud/Dockerfile"       "$DEST/Dockerfile"
cp "$ROOT/backend/cloud/space_README.md"  "$DEST/README.md"

# App code + the PoC modules the pipeline imports. Exclude local venvs, caches,
# and the dev-only .data so the push stays lean.
RSYNC_EXCLUDES=(--exclude '.venv' --exclude 'venv' --exclude '__pycache__'
                --exclude '*.pyc' --exclude '.data' --exclude '.git')

for d in backend poc-audio-extraction poc-blooper-detector \
         poc-video-clipper poc-subtitles poc-content-highlighter; do
  echo "  + $d"
  rsync -a "${RSYNC_EXCLUDES[@]}" "$ROOT/$d/" "$DEST/$d/"
done

# .gitattributes so model weights / media go through Git LFS.
cat > "$DEST/.gitattributes" <<'EOF'
*.bin filter=lfs diff=lfs merge=lfs -text
*.gguf filter=lfs diff=lfs merge=lfs -text
*.safetensors filter=lfs diff=lfs merge=lfs -text
*.pt filter=lfs diff=lfs merge=lfs -text
*.onnx filter=lfs diff=lfs merge=lfs -text
EOF

echo "Done. Next:"
echo "  cd $DEST && git init && git lfs install && git add -A && git commit -m 'ViReel backend'"
echo "  git remote add origin https://huggingface.co/spaces/<user>/vireel-backend && git push -u origin main"
