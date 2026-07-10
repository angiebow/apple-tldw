# Deploy the ViReel backend on a free VM (Oracle Always-Free ARM)

A persistent VM suits our architecture (the worker runs in-process, so it must
stay alive between requests — unlike scale-to-zero platforms). Oracle's
Always-Free **Ampere A1** (up to 4 OCPU / 24 GB RAM, ARM64) is genuinely free and
big enough to build + run everything.

Do it in stages so each part is verifiable before moving on.

---

## Stage 0 — Create the VM (Oracle console; you)
1. Sign up at cloud.oracle.com (needs a card for identity; Always-Free isn't charged).
2. **Compute → Instances → Create instance**:
   - Image: **Ubuntu 22.04**, Shape: **VM.Standard.A1.Flex**, 4 OCPU / 24 GB.
     (If you hit "out of capacity", try another Availability Domain or region.)
   - Add your SSH public key (`cat ~/.ssh/id_ed25519.pub`; make one with
     `ssh-keygen -t ed25519` if needed).
3. **Networking → open ports 80 and 443**:
   - VCN → the subnet's **Security List** → add Ingress rules: source `0.0.0.0/0`,
     TCP ports **80** and **443**.
   - On the VM also: `sudo iptables -I INPUT -p tcp --dport 80 -j ACCEPT && sudo iptables -I INPUT -p tcp --dport 443 -j ACCEPT && sudo netfilter-persistent save` (Oracle Ubuntu ships restrictive iptables).
4. Note the VM's **public IP**. Test: `ssh ubuntu@<PUBLIC_IP>`.

## Stage 1 — A free HTTPS domain (DuckDNS; you)
iOS requires HTTPS with a valid cert, which needs a domain name.
1. duckdns.org → sign in → create a subdomain, e.g. `vireel.duckdns.org`.
2. Point it at the VM: set the DuckDNS IP to your VM's public IP (the site has a box for it).
   - `DOMAIN=vireel.duckdns.org` from here on.

## Stage 2 — Get the code onto the VM (from your Mac)
The model weights aren't in git, so copy the assembled tree (~0.95 GB) over SSH:
```bash
# on your Mac, after ./backend/cloud/assemble_space.sh /tmp/vireel-space
rsync -avz --progress /tmp/vireel-space/ ubuntu@<PUBLIC_IP>:~/vireel-backend/
```

## Stage 3 — Build + run (on the VM)
```bash
ssh ubuntu@<PUBLIC_IP>
cd ~/vireel-backend

# Docker (once)
command -v docker || { curl -fsSL https://get.docker.com | sudo sh; sudo usermod -aG docker $USER; newgrp docker; }

# Model-cache dir owned by the container user (uid 1000)
mkdir -p hf-cache && sudo chown -R 1000:1000 hf-cache

# Env for compose
cat > .env <<EOF
DOMAIN=vireel.duckdns.org
TLDW_API_TOKEN=<the token from your Mac>
EOF

# Build + start (first build ~10–20 min: torch, faster-whisper, llama.cpp)
docker compose up -d --build
docker compose logs -f            # watch it boot; Ctrl-C to stop tailing
```

**Verify (still on the VM):**
```bash
curl -s http://localhost:7860/health        # {"status":"ok",...}
curl -s https://vireel.duckdns.org/health    # via Caddy TLS (once the cert is issued)
```

## Stage 4 — Point the apps
In **both** app targets' Info.plist:
- `BackendBaseURL` = `https://vireel.duckdns.org`
- `BackendAPIToken` = the same token

Run either app → the first transcribe will download Whisper + the Llama GGUF on
the VM (slow once), then work.

---

### Notes / gotchas
- **First request is slow** — model weights download on demand; they persist in
  `hf-cache/` after that.
- **llama-cpp-python** compiles during the build; if it fails, paste the build log.
- **CPU speed**: Llama summary + MusicGen are slow on CPU (chosen tradeoff);
  everything else is fine.
- Keep the VM running — this is your backend's home now.
