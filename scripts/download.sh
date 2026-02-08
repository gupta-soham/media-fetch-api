#!/usr/bin/env bash
# Download media via Media Fetch API (extract + stream or server-side yt-dlp).
# No yt-dlp or cookies on the client. Run from media-fetch-api: ./download.sh ... or ./scripts/download.sh ...
#
# Usage:
#   ./download.sh <media_url> [output_file]
#   ./download.sh <api_base_url> <media_url> [output_file]
#
# Examples:
#   ./download.sh "https://www.youtube.com/watch?v=..." video.mp4
#   ./download.sh "https://your-ngrok.ngrok-free.dev" "https://www.youtube.com/watch?v=..." video.mp4

set -e
DEFAULT_API="${MEDIA_FETCH_API_URL:-https://ronny-jazzier-productively.ngrok-free.dev}"

case $# in
  1) API="$DEFAULT_API";     MEDIA_URL="$1"; OUT="download.mp4" ;;
  2) API="$DEFAULT_API";     MEDIA_URL="$1"; OUT="$2" ;;
  3) API="${1%/}";           MEDIA_URL="$2"; OUT="$3" ;;
  *) echo "Usage: $0 <media_url> [output_file]  OR  $0 <api_base_url> <media_url> [output_file]" >&2; exit 1 ;;
esac

H="ngrok-skip-browser-warning: true"
ENCODED=$(printf '%s' "$MEDIA_URL" | python3 -c "import sys, urllib.parse; print(urllib.parse.quote(sys.stdin.read(), safe=''))")

echo "Downloading: $MEDIA_URL" >&2
HTTP_CODE=$(curl -s -w "%{http_code}" -o "$OUT" -H "$H" "$API/api/download?url=$ENCODED" | tr -d '\r\n')

if [ "$HTTP_CODE" != "200" ]; then
  echo "Download failed (HTTP $HTTP_CODE)." >&2
  if [ -s "$OUT" ]; then
    echo "Response:" >&2
    head -c 500 "$OUT" >&2
    echo "" >&2
  fi
  rm -f "$OUT"
  exit 1
fi

if [ ! -s "$OUT" ]; then
  echo "Output file is empty." >&2
  rm -f "$OUT"
  exit 1
fi

echo "Saved: $OUT" >&2
