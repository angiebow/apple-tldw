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
                --exclude '*.pyc' --exclude '.data' --exclude '.git'
                --exclude '*.zip' --exclude '*.parquet'
                # sample media + scratch dirs from the PoCs (not needed at runtime)
                --exclude 'input' --exclude 'output' --exclude 'samples'
                --exclude '*.mov' --exclude '*.mp4' --exclude '*.mkv'
                --exclude '*.webm' --exclude '*.wav' --exclude '*.m4a' --exclude '*.mp3')

# App code + the PoC pipeline modules the backend imports. NOTE:
# poc-content-highlighter is handled separately below — it's mostly training
# artifacts (a 6GB zip, a 1.4GB dataset, unused checkpoints) and the backend
# only needs two model dirs, so copying it wholesale balloons the repo to ~16GB.
for d in backend poc-audio-extraction poc-blooper-detector \
         poc-video-clipper poc-subtitles; do
  echo "  + $d"
  rsync -a "${RSYNC_EXCLUDES[@]}" "$ROOT/$d/" "$DEST/$d/"
done

# The backend loads exactly two locally-trained models (DETECTOR_DIR +
# RERANKER_DIR in server.py), inference files only — skip training checkpoints,
# optimizer states, and training_args so we ship ~0.9GB instead of ~16GB.
for m in distilbert-detector bert-ranker; do
  echo "  + poc-content-highlighter/models/$m (inference files only)"
  rsync -a --exclude 'checkpoint-*' --exclude '*.pt' \
        --exclude 'training_args.bin' \
        "$ROOT/poc-content-highlighter/models/$m/" \
        "$DEST/poc-content-highlighter/models/$m/"
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
