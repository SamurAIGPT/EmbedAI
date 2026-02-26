#!/bin/bash
# muapi.ai Audio & Music Generation
# Usage: ./create-music.sh --op create --style "lo-fi" --prompt "chill beats"

set -e

MUAPI_BASE="https://api.muapi.ai/api/v1"

OP="create"
STYLE=""
PROMPT=""
SUNO_MODEL="V5"
AUDIO_URL=""
AUDIO_FILE=""
VIDEO_URL=""
VIDEO_FILE=""
DURATION=10
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
        --op) OP="$2"; shift 2 ;;
        --style) STYLE="$2"; shift 2 ;;
        --prompt|-p) PROMPT="$2"; shift 2 ;;
        --suno-model) SUNO_MODEL="$2"; shift 2 ;;
        --audio-url) AUDIO_URL="$2"; shift 2 ;;
        --audio-file) AUDIO_FILE="$2"; shift 2 ;;
        --video-url) VIDEO_URL="$2"; shift 2 ;;
        --video-file) VIDEO_FILE="$2"; shift 2 ;;
        --duration) DURATION="$2"; shift 2 ;;
        --async) ASYNC=true; shift ;;
        --timeout) MAX_WAIT="$2"; shift 2 ;;
        --json) JSON_ONLY=true; shift ;;
        --help|-h)
            echo "muapi.ai Audio & Music Generation" >&2
            echo "" >&2
            echo "Operations (--op):" >&2
            echo "  create       Suno music creation (default)" >&2
            echo "  remix        Suno remix (requires --audio-url)" >&2
            echo "  extend       Suno extend (requires --audio-url)" >&2
            echo "  text-to-audio  MMAudio from text prompt" >&2
            echo "  video-to-audio MMAudio from video (requires --video-url)" >&2
            echo "" >&2
            echo "Examples:" >&2
            echo "  bash create-music.sh --style \"lo-fi hip hop\" --prompt \"chill beats\"" >&2
            echo "  bash create-music.sh --op text-to-audio --prompt \"thunderstorm\" --duration 15" >&2
            echo "  bash create-music.sh --op video-to-audio --video-url URL --prompt \"epic score\"" >&2
            echo "" >&2
            echo "File Inputs:" >&2
            echo "  --audio-file     Local audio file for remix/extend" >&2
            echo "  --video-file     Local video file for video-to-audio" >&2
            exit 0 ;;
        *) shift ;;
    esac
done

if [ -z "$MUAPI_KEY" ]; then echo "Error: MUAPI_KEY not set" >&2; exit 1; fi

HEADERS=(-H "x-api-key: $MUAPI_KEY" -H "Content-Type: application/json")
PROMPT_JSON=$(echo "${PROMPT:-}" | python3 -c 'import json,sys; print(json.dumps(sys.stdin.read().rstrip()))')
STYLE_JSON=$(echo "${STYLE:-}" | python3 -c 'import json,sys; print(json.dumps(sys.stdin.read().rstrip()))')

# Auto-upload local files
upload_file() {
    local FPATH="$1"
    if [ ! -f "$FPATH" ]; then echo "Error: File not found: $FPATH" >&2; exit 1; fi
    [ "$JSON_ONLY" = false ] && echo "Uploading $(basename "$FPATH")..." >&2
    local RESP=$(curl -s -X POST "${MUAPI_BASE}/upload_file" -H "x-api-key: $MUAPI_KEY" -F "file=@${FPATH}")
    local URL=$(echo "$RESP" | jq -r '.url // empty')
    if [ -z "$URL" ]; then
        local ERR=$(echo "$RESP" | jq -r '.error // .detail // "Upload failed"')
        echo "Error: $ERR" >&2; exit 1
    fi
    echo "$URL"
}

if [ -n "$AUDIO_FILE" ]; then AUDIO_URL=$(upload_file "$AUDIO_FILE"); fi
if [ -n "$VIDEO_FILE" ]; then VIDEO_URL=$(upload_file "$VIDEO_FILE"); fi

case $OP in
    create)
        if [ -z "$STYLE" ]; then echo "Error: --style is required for create" >&2; exit 1; fi
        ENDPOINT="suno-create-music"
        PAYLOAD="{\"style\": $STYLE_JSON, \"prompt\": $PROMPT_JSON, \"model\": \"$SUNO_MODEL\"}" ;;
    remix)
        if [ -z "$AUDIO_URL" ]; then echo "Error: --audio-url is required for remix" >&2; exit 1; fi
        AUDIO_CLEAN=$(echo "$AUDIO_URL" | tr -d '"')
        ENDPOINT="suno-remix-music"
        PAYLOAD="{\"audio_url\": \"$AUDIO_CLEAN\", \"style\": $STYLE_JSON, \"prompt\": $PROMPT_JSON, \"model\": \"$SUNO_MODEL\"}" ;;
    extend)
        if [ -z "$AUDIO_URL" ]; then echo "Error: --audio-url is required for extend" >&2; exit 1; fi
        AUDIO_CLEAN=$(echo "$AUDIO_URL" | tr -d '"')
        ENDPOINT="suno-extend-music"
        PAYLOAD="{\"audio_url\": \"$AUDIO_CLEAN\", \"prompt\": $PROMPT_JSON, \"model\": \"$SUNO_MODEL\"}" ;;
    text-to-audio)
        if [ -z "$PROMPT" ]; then echo "Error: --prompt is required for text-to-audio" >&2; exit 1; fi
        ENDPOINT="mmaudio-v2/text-to-audio"
        PAYLOAD="{\"prompt\": $PROMPT_JSON, \"duration\": $DURATION}" ;;
    video-to-audio)
        if [ -z "$VIDEO_URL" ]; then echo "Error: --video-url is required for video-to-audio" >&2; exit 1; fi
        VIDEO_CLEAN=$(echo "$VIDEO_URL" | tr -d '"')
        ENDPOINT="mmaudio-v2/video-to-video"
        PAYLOAD="{\"video_url\": \"$VIDEO_CLEAN\", \"prompt\": $PROMPT_JSON}" ;;
    *)
        echo "Error: Unknown operation '$OP'" >&2
        echo "Valid: create, remix, extend, text-to-audio, video-to-audio" >&2
        exit 1 ;;
esac

[ "$JSON_ONLY" = false ] && echo "Submitting $OP to $ENDPOINT..." >&2

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
    [ "$JSON_ONLY" = false ] && echo "Music generation takes 30–90s. Check: bash check-result.sh --id \"$REQUEST_ID\"" >&2
    echo "$SUBMIT"; exit 0
fi

[ "$JSON_ONLY" = false ] && echo "Generating (30–90 seconds)..." >&2
ELAPSED=0; LAST_STATUS=""
while [ $ELAPSED -lt $MAX_WAIT ]; do
    sleep $POLL_INTERVAL; ELAPSED=$((ELAPSED + POLL_INTERVAL))
    RESULT=$(curl -s -X GET "${MUAPI_BASE}/predictions/${REQUEST_ID}/result" "${HEADERS[@]}")
    STATUS=$(echo "$RESULT" | grep -oE '"status"[[:space:]]*:[[:space:]]*"[^"]*"' | sed 's/.*: *"//' | sed 's/"$//')
    if [ "$STATUS" != "$LAST_STATUS" ] && [ "$JSON_ONLY" = false ]; then echo "Status: $STATUS (${ELAPSED}s)" >&2; LAST_STATUS="$STATUS"; fi
    case $STATUS in
        completed)
            [ "$JSON_ONLY" = false ] && echo "" >&2
            [ "$JSON_ONLY" = false ] && echo "Audio generation complete!" >&2
            URL=$(echo "$RESULT" | grep -o '"outputs":\[[^]]*\]' | grep -o '"[^"]*\.\(mp3\|wav\|mp4\)"' | head -1 | tr -d '"')
            [ -n "$URL" ] && [ "$JSON_ONLY" = false ] && echo "Audio URL: $URL" >&2
            echo "$RESULT"; exit 0 ;;
        failed)
            ERR=$(echo "$RESULT" | grep -o '"error":"[^"]*"' | head -1 | cut -d'"' -f4)
            echo "Error: ${ERR:-Generation failed}" >&2; echo "$RESULT"; exit 1 ;;
    esac
done
echo "Error: Timeout after ${MAX_WAIT}s — Request ID: $REQUEST_ID" >&2; exit 1
