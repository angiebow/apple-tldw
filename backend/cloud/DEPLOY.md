# Deploy the ViReel backend to a free Hugging Face Docker Space

> Prereq: the CPU port (faster-whisper + llama.cpp) must be in place, or media
> jobs will fail on Linux (MLX is Apple-only). See tasks 6–7.

Because the app spans `backend/` + the `poc-*/` modules, the Space repo needs
those dirs plus the `Dockerfile` and a `README.md` (Space frontmatter) at its
root. `assemble_space.sh` builds that tree, then you push it.

## 1. Create the Space (once, in a browser or CLI)
- huggingface.co → New → **Space** → SDK **Docker** → name e.g. `vireel-backend`.
- Or CLI (needs `pip install huggingface_hub` and `huggingface-cli login`):
  ```
  ! huggingface-cli login
  ! huggingface-cli repo create vireel-backend --type space --space_sdk docker
  ```

## 2. Assemble + push
From the repo root:
```bash
./backend/cloud/assemble_space.sh /tmp/vireel-space
cd /tmp/vireel-space
git init && git lfs install
git remote add origin https://huggingface.co/spaces/<your-user>/vireel-backend
git add -A && git commit -m "ViReel backend"
git push -u origin main            # HF asks for your username + an access token
```
HF builds the image and boots it. First boot downloads model weights (slow).

## 3. Secrets & hardware
- Leave the Space hardware on **CPU basic (free)** — the default.
- In **Settings → Variables and secrets**, add:
  - `TLDW_API_TOKEN` (secret) = a long random string (enables bearer auth).
- **Light mode is on by default** (baked into the Dockerfile): AI music
  (MusicGen) + blooper detection are disabled so CPU requests don't time out.
  The app hides those features automatically (it reads `/health`'s `features`).
  When you later switch to GPU hardware, add a Variable `TLDW_LIGHT_MODE=0` to
  turn the Pro features back on — no code change.

## 4. Point the apps at it
Set `BackendBaseURL` in **both** app targets' Info.plist to
`https://<your-user>-vireel-backend.hf.space` (HTTPS ⇒ iOS ATS is happy). The
client sends `Authorization: Bearer <TLDW_API_TOKEN>`.

## Notes
- Free Spaces sleep after inactivity → first request after idle is a cold start.
- Storage is ephemeral (results are download-then-discard, so that's fine).
- To go faster later: switch the Space hardware to GPU, or move only the worker
  to Modal/ZeroGPU — the storage/queue abstractions stay the same.
