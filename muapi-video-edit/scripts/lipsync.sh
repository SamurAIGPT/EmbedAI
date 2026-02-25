#!/bin/bash
# muapi.ai Lipsync
# Usage: ./lipsync.sh --video-url URL --audio-url URL [--model sync]

set -e

MUAPI_BASE="https://api.muapi.ai/api/v1"

VIDEO_URL=""
AUDIO_URL=""
MODEL="sync"
ASYNC=false
JSON_ONLY=false
MAX_WAIT=300
POLL_INTERVAL=5

for arg in "$@"; do
    if [ "$arg" = "--add-key" ]; then
        shift
        KEY_VALUE=""
        if [[ -n "$1" && ! "$1" =~ ^-- ]]; then KEY_VALUE="$1"; fi
        if [ -z "$KEY_VALUE" ]; then echo "Enter your muapi.ai API key:" >&2; read -r KEY_VALUE; fi
        if [ -n "$KEY_VALUE" ]; then
            grep -v "^MUAPI_KEY=" .env > .env.tmp 2>/dev/null || true
            mv .env.tmp .env 2>/dev/null || true
            echo "MUAPI_KEY=$KEY_VALUE" >> .env
            echo "MUAPI_KEY saved to .env" >&2
        fi
        exit 0
    fi
done

if [ -f ".env" ]; then source .env 2>/dev/null || true; fi

while [[ $# -gt 0 ]]; do
    case $1 in
        --video-url) VIDEO_URL="$2"; shift 2 ;;
        --audio-url) AUDIO_URL="$2"; shift 2 ;;
        --model|-m) MODEL="$2"; shift 2 ;;
        --async) ASYNC=true; shift ;;
        --timeout) MAX_WAIT="$2"; shift 2 ;;
        --json) JSON_ONLY=true; shift ;;
        --help|-h)
            echo "muapi.ai Lipsync" >&2
            echo "" >&2
            echo "Usage: ./lipsync.sh --video-url URL --audio-url URL [options]" >&2
            echo "" >&2
            echo "Models (--model):" >&2
            echo "  sync      Sync Labs — high quality (default)" >&2
            echo "  latent    LatentSync — open source" >&2
            echo "  creatify  Creatify — fast" >&2
            echo "  veed      Veed — reliable" >&2
            exit 0 ;;
        *) shift ;;
    esac
done

if [ -z "$MUAPI_KEY" ]; then echo "Error: MUAPI_KEY not set" >&2; exit 1; fi
if [ -z "$VIDEO_URL" ]; then echo "Error: --video-url is required" >&2; exit 1; fi
if [ -z "$AUDIO_URL" ]; then echo "Error: --audio-url is required" >&2; exit 1; fi

HEADERS=(-H "x-api-key: $MUAPI_KEY" -H "Content-Type: application/json")

VIDEO_CLEAN=$(echo "$VIDEO_URL" | tr -d '"')
AUDIO_CLEAN=$(echo "$AUDIO_URL" | tr -d '"')

case $MODEL in
    sync)     ENDPOINT="sync-lipsync" ;;
    latent)   ENDPOINT="latentsync-video" ;;
    creatify) ENDPOINT="creatify-lipsync" ;;
    veed)     ENDPOINT="veed-lipsync" ;;
    *)
        echo "Error: Unknown model '$MODEL'" >&2
        echo "Valid: sync, latent, creatify, veed" >&2
        exit 1 ;;
esac

PAYLOAD="{\"video_url\": \"$VIDEO_CLEAN\", \"audio_url\": \"$AUDIO_CLEAN\"}"

[ "$JSON_ONLY" = false ] && echo "Submitting lipsync (model: $MODEL)..." >&2

SUBMIT=$(curl -s -X POST "${MUAPI_BASE}/${ENDPOINT}" "${HEADERS[@]}" -d "$PAYLOAD")

if echo "$SUBMIT" | grep -q '"error"\|"detail"'; then
    ERR=$(echo "$SUBMIT" | grep -o '"detail":"[^"]*"' | head -1 | cut -d'"' -f4)
    [ -z "$ERR" ] && ERR=$(echo "$SUBMIT" | grep -o '"error":"[^"]*"' | head -1 | cut -d'"' -f4)
    echo "Error: ${ERR:-$SUBMIT}" >&2; exit 1
fi

REQUEST_ID=$(echo "$SUBMIT" | grep -oE '"request_id"[[:space:]]*:[[:space:]]*"[^"]*"' | sed 's/.*: *"//' | sed 's/"$//')
if [ -z "$REQUEST_ID" ]; then echo "Error: No request_id" >&2; echo "$SUBMIT" >&2; exit 1; fi
[ "$JSON_ONLY" = false ] && echo "Request ID: $REQUEST_ID" >&2

if [ "$ASYNC" = true ]; then
    [ "$JSON_ONLY" = false ] && echo "Lipsync takes 30–120s. Check: bash check-result.sh --id \"$REQUEST_ID\"" >&2
    echo "$SUBMIT"; exit 0
fi

[ "$JSON_ONLY" = false ] && echo "Processing lipsync (30–120s)..." >&2
ELAPSED=0; LAST_STATUS=""
while [ $ELAPSED -lt $MAX_WAIT ]; do
    sleep $POLL_INTERVAL; ELAPSED=$((ELAPSED + POLL_INTERVAL))
    RESULT=$(curl -s -X GET "${MUAPI_BASE}/predictions/${REQUEST_ID}/result" "${HEADERS[@]}")
    STATUS=$(echo "$RESULT" | grep -oE '"status"[[:space:]]*:[[:space:]]*"[^"]*"' | sed 's/.*: *"//' | sed 's/"$//')
    if [ "$STATUS" != "$LAST_STATUS" ] && [ "$JSON_ONLY" = false ]; then echo "Status: $STATUS (${ELAPSED}s)" >&2; LAST_STATUS="$STATUS"; fi
    case $STATUS in
        completed)
            [ "$JSON_ONLY" = false ] && echo "" >&2
            [ "$JSON_ONLY" = false ] && echo "Lipsync complete!" >&2
            URL=$(echo "$RESULT" | grep -o '"outputs":\[[^]]*\]' | grep -o '"[^"]*\.mp4"' | head -1 | tr -d '"')
            [ -n "$URL" ] && [ "$JSON_ONLY" = false ] && echo "Video URL: $URL" >&2
            echo "$RESULT"; exit 0 ;;
        failed)
            ERR=$(echo "$RESULT" | grep -o '"error":"[^"]*"' | head -1 | cut -d'"' -f4)
            echo "Error: ${ERR:-Lipsync failed}" >&2; echo "$RESULT"; exit 1 ;;
    esac
done
echo "Error: Timeout after ${MAX_WAIT}s — Request ID: $REQUEST_ID" >&2; exit 1
