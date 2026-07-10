#!/usr/bin/env bash
# End-to-end smoke test of the cloud flow against a running dev server.
# Usage:  BASE=http://127.0.0.1:8100 ./cloud/smoke_test.sh
set -euo pipefail
BASE="${BASE:-http://127.0.0.1:8100}"

echo "1) health"; curl -sf "$BASE/health"; echo

echo "2) mint upload"
UP=$(curl -sf -X POST "$BASE/uploads" -H 'content-type: application/json' \
     -d '{"filename":"sample.mp4","content_type":"video/mp4"}')
echo "$UP"
KEY=$(printf '%s' "$UP" | python3 -c 'import sys,json;print(json.load(sys.stdin)["media_key"])')
URL=$(printf '%s' "$UP" | python3 -c 'import sys,json;print(json.load(sys.stdin)["upload_url"])')

echo "3) PUT media to $URL"
echo "fake-video-bytes" > /tmp/vireel_sample.mp4
curl -sf -X PUT "$URL" --data-binary @/tmp/vireel_sample.mp4; echo

echo "4) create job for $KEY"
JOB=$(curl -sf -X POST "$BASE/jobs" -H 'content-type: application/json' \
      -d "{\"type\":\"transcribe\",\"media_key\":\"$KEY\",\"params\":{\"model\":\"turbo\"}}")
echo "$JOB"
JID=$(printf '%s' "$JOB" | python3 -c 'import sys,json;print(json.load(sys.stdin)["id"])')

echo "5) poll job $JID"
for i in $(seq 1 30); do
  J=$(curl -sf "$BASE/jobs/$JID")
  ST=$(printf '%s' "$J" | python3 -c 'import sys,json;print(json.load(sys.stdin)["status"])')
  PR=$(printf '%s' "$J" | python3 -c 'import sys,json;print(json.load(sys.stdin)["progress"])')
  echo "   status=$ST progress=$PR"
  [ "$ST" = "done" ] && break
  [ "$ST" = "error" ] && { echo "JOB ERRORED: $J"; exit 1; }
  sleep 0.3
done

echo "6) fetch result"
RES=$(curl -sf "$BASE/jobs/$JID/result"); echo "$RES"
DL=$(printf '%s' "$RES" | python3 -c 'import sys,json;o=json.load(sys.stdin)["outputs"];print(o[0]["download_url"] if o else "")')
echo "7) download output from $DL"
[ -n "$DL" ] && curl -sf "$DL"; echo
echo "OK"
