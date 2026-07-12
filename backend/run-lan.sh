#!/usr/bin/env bash
# Start the ViReel backend for LAN-direct testing (iPhone → Mac over wifi).
#
# Auto-detects the Mac's current wifi IP, syncs tldw/BackendConfig.plist to it
# (so the phone build points at the right address even if DHCP changed the IP),
# and runs the backend in the background. Run from anywhere:
#
#     ./backend/run-lan.sh
#
# Then make sure the iPhone is on the same wifi and rebuild the app (⌘R).
set -uo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
PORT=8100
PLIST="$ROOT/tldw/BackendConfig.plist"

# 1. Current wifi IP.
IP="$(ipconfig getifaddr en0 2>/dev/null || ipconfig getifaddr en1 2>/dev/null || true)"
[ -z "${IP:-}" ] && { echo "❌ No wifi IP found — are you connected to wifi?"; exit 1; }
URL="http://$IP:$PORT"

# 2. Token: env → existing plist → freshly generated (kept out of git).
TOKEN="${TLDW_API_TOKEN:-}"
[ -z "$TOKEN" ] && [ -f "$PLIST" ] && TOKEN="$(/usr/libexec/PlistBuddy -c 'Print :BackendAPIToken' "$PLIST" 2>/dev/null || true)"
[ -z "$TOKEN" ] && TOKEN="$(python3 -c 'import secrets; print(secrets.token_urlsafe(32))')"

# 3. Sync the app's backend pointer (BackendConfig.plist is gitignored).
mkdir -p "$(dirname "$PLIST")"
if [ ! -f "$PLIST" ]; then
  cat > "$PLIST" <<PL
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0"><dict>
	<key>BackendBaseURL</key><string>$URL</string>
	<key>BackendAPIToken</key><string>$TOKEN</string>
</dict></plist>
PL
else
  /usr/libexec/PlistBuddy -c "Set :BackendBaseURL $URL" "$PLIST" 2>/dev/null \
    || /usr/libexec/PlistBuddy -c "Add :BackendBaseURL string $URL" "$PLIST"
  /usr/libexec/PlistBuddy -c "Set :BackendAPIToken $TOKEN" "$PLIST" 2>/dev/null \
    || /usr/libexec/PlistBuddy -c "Add :BackendAPIToken string $TOKEN" "$PLIST"
fi
echo "🔧 synced app config → $URL"

# 4. Restart the backend in the background.
pkill -f 'uvicorn cloud.app' 2>/dev/null; sleep 1
cd "$ROOT/backend"
TLDW_API_TOKEN="$TOKEN" TLDW_PUBLIC_BASE_URL="$URL" TLDW_WHISPER_MODEL="${TLDW_WHISPER_MODEL:-base}" \
  nohup ./venv/bin/uvicorn cloud.app:app --port "$PORT" --host 0.0.0.0 --log-level info \
  > /tmp/vireel_cloud.log 2>&1 &
sleep 4

if curl -s --max-time 5 "$URL/health" >/dev/null; then
  echo "✅ backend up at $URL"
  echo "   log:   /tmp/vireel_cloud.log"
  echo "   stop:  pkill -f 'uvicorn cloud.app'"
  echo "   → iPhone on the same wifi, then rebuild the app (⌘R)."
else
  echo "⚠️  no health response yet — check /tmp/vireel_cloud.log"
fi
