# Hugging Face Docker Space image for the ViReel cloud backend (free CPU tier).
# BUILD CONTEXT = the repo root (tldw/), because the app spans backend/ + the
# poc-*/ modules. HF builds the Space repo as context, so the Space repo must
# contain backend/ and the poc-*/ dirs at its root (see DEPLOY.md's assemble step).

FROM python:3.11-slim

# ffmpeg: clip/merge/subtitle burn. build-essential + git: llama-cpp-python build.
RUN apt-get update && apt-get install -y --no-install-recommends \
        ffmpeg git build-essential \
    && rm -rf /var/lib/apt/lists/*

# HF Spaces run the container as uid 1000.
RUN useradd -m -u 1000 user
USER user
ENV HOME=/home/user \
    PATH=/home/user/.local/bin:$PATH \
    HF_HOME=/home/user/.cache/huggingface \
    PYTHONUNBUFFERED=1 \
    TLDW_STORAGE_BACKEND=local \
    TLDW_CLOUD_DATA=/home/user/app/.data \
    # Free CPU tier: disable GPU-heavy AI music + blooper detection so requests
    # don't time out. Override with a Space Variable TLDW_LIGHT_MODE=0 on GPU.
    TLDW_LIGHT_MODE=1

WORKDIR /home/user/app

# Deps first for layer caching.
COPY --chown=user backend/cloud/requirements-cloud.txt ./requirements.txt
RUN pip install --no-cache-dir \
        --extra-index-url https://download.pytorch.org/whl/cpu \
        -r requirements.txt

# App code + the PoC pipeline modules it imports (incl. the local model dirs
# under poc-content-highlighter/models used by the virality detector/reranker).
COPY --chown=user backend/ ./backend/
COPY --chown=user poc-audio-extraction/ ./poc-audio-extraction/
COPY --chown=user poc-blooper-detector/ ./poc-blooper-detector/
COPY --chown=user poc-video-clipper/ ./poc-video-clipper/
COPY --chown=user poc-subtitles/ ./poc-subtitles/
COPY --chown=user poc-content-highlighter/ ./poc-content-highlighter/

# Run from backend/ so `import cloud.app` and `import server` resolve.
WORKDIR /home/user/app/backend

# HF routes external HTTPS to this port (matches app_port in README.md).
EXPOSE 7860
CMD ["uvicorn", "cloud.app:app", "--host", "0.0.0.0", "--port", "7860"]
