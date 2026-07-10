---
title: ViReel Backend
emoji: 🎬
colorFrom: purple
colorTo: blue
sdk: docker
app_port: 7860
pinned: false
---

# ViReel cloud backend

Async, storage-backed API for the ViReel iOS + macOS apps. Deployed as a free
Hugging Face **Docker Space** — HF serves it over HTTPS at
`https://<user>-<space>.hf.space`, which also satisfies the iOS App Transport
Security (HTTPS-only) requirement.

- Contract: `backend/cloud/openapi.yaml`
- Flow: `POST /uploads` → `PUT` bytes → `POST /jobs` → poll `GET /jobs/{id}` →
  `GET /jobs/{id}/result` → download.
- Free tier is **CPU-only**: transcription + ranking work; the Llama summary and
  MusicGen backsound are slow. Upgrade the Space to GPU (or move the worker to
  Modal/ZeroGPU) later without changing the API.

Set the Space secret `TLDW_API_TOKEN` to require `Authorization: Bearer <token>`.
