#!/bin/bash
# muapi.ai Video Effects
# Usage: ./video-effects.sh --op face-swap --video-url URL --face-url URL

set -e

MUAPI_BASE="https://api.muapi.ai/api/v1"

OP=""
VIDEO_URL=""
IMAGE_URL=""
FACE_URL=""
AUDIO_URL=""
PROMPT=""
EFFECT=""
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
        --video-url) VIDEO_URL="$2"; shift 2 ;;
        --image-url) IMAGE_URL="$2"; shift 2 ;;
        --face-url) FACE_URL="$2"; shift 2 ;;
        --audio-url) AUDIO_URL="$2"; shift 2 ;;
        --prompt|-p) PROMPT="$2"; shift 2 ;;
        --effect) EFFECT="$2"; shift 2 ;;
        --async) ASYNC=true; shift ;;
        --timeout) MAX_WAIT="$2"; shift 2 ;;
        --json) JSON_ONLY=true; shift ;;
        --help|-h)
            echo "muapi.ai Video Effects" >&2
            echo "" >&2
            echo "Operations (--op):" >&2
            echo "  wan-effects    Wan AI effects on image (--image-url, --prompt)" >&2
            echo "  video-effect   Named effect on video (--video-url, --effect)" >&2
            echo "  image-effect   Named effect on image (--image-url, --effect)" >&2
            echo "  dance          Dance animation (--image-url, --audio-url)" >&2
            echo "  face-swap      Face swap in video (--video-url, --face-url)" >&2
            echo "  dress-change   Change outfit (--image-url, --prompt)" >&2
            echo "  luma-modify    Modify video with prompt (--video-url, --prompt)" >&2
            echo "  luma-reframe   Reframe video (--video-url, --prompt)" >&2
            echo "  vidu-reference Vidu character reference (--image-url, --prompt)" >&2
            exit 0 ;;
        *) shift ;;
    esac
done

if [ -z "$MUAPI_KEY" ]; then echo "Error: MUAPI_KEY not set" >&2; exit 1; fi
if [ -z "$OP" ]; then echo "Error: --op is required" >&2; exit 1; fi

HEADERS=(-H "x-api-key: $MUAPI_KEY" -H "Content-Type: application/json")
PROMPT_JSON=$(echo "${PROMPT:-}" | python3 -c 'import json,sys; print(json.dumps(sys.stdin.read().rstrip()))')
EFFECT_JSON=$(echo "${EFFECT:-}" | python3 -c 'import json,sys; print(json.dumps(sys.stdin.read().rstrip()))')

clean_url() { echo "$1" | tr -d '"'; }

case $OP in
    wan-effects)
        if [ -z "$IMAGE_URL" ]; then echo "Error: --image-url required" >&2; exit 1; fi
        ENDPOINT="generate_wan_ai_effects"
        PAYLOAD="{\"image_url\": \"$(clean_url "$IMAGE_URL")\", \"prompt\": $PROMPT_JSON}" ;;
    video-effect)
        if [ -z "$VIDEO_URL" ]; then echo "Error: --video-url required" >&2; exit 1; fi
        ENDPOINT="video-effects"
        PAYLOAD="{\"video_url\": \"$(clean_url "$VIDEO_URL")\", \"effect\": $EFFECT_JSON}" ;;
    image-effect)
        if [ -z "$IMAGE_URL" ]; then echo "Error: --image-url required" >&2; exit 1; fi
        ENDPOINT="image-effects"
        PAYLOAD="{\"image_url\": \"$(clean_url "$IMAGE_URL")\", \"effect\": $EFFECT_JSON}" ;;
    dance)
        if [ -z "$IMAGE_URL" ] || [ -z "$AUDIO_URL" ]; then
            echo "Error: --image-url and --audio-url are required for dance" >&2; exit 1
        fi
        ENDPOINT="ai-dance-effects"
        PAYLOAD="{\"image_url\": \"$(clean_url "$IMAGE_URL")\", \"audio_url\": \"$(clean_url "$AUDIO_URL")\"}" ;;
    face-swap)
        if [ -z "$VIDEO_URL" ] || [ -z "$FACE_URL" ]; then
            echo "Error: --video-url and --face-url are required for face-swap" >&2; exit 1
        fi
        ENDPOINT="ai-video-face-swap"
        PAYLOAD="{\"video_url\": \"$(clean_url "$VIDEO_URL")\", \"face_image_url\": \"$(clean_url "$FACE_URL")\"}" ;;
    dress-change)
        if [ -z "$IMAGE_URL" ]; then echo "Error: --image-url required" >&2; exit 1; fi
        ENDPOINT="ai-dress-change"
        PAYLOAD="{\"image_url\": \"$(clean_url "$IMAGE_URL")\", \"prompt\": $PROMPT_JSON}" ;;
    luma-modify)
        if [ -z "$VIDEO_URL" ]; then echo "Error: --video-url required" >&2; exit 1; fi
        ENDPOINT="luma-modify-video"
        PAYLOAD="{\"video_url\": \"$(clean_url "$VIDEO_URL")\", \"prompt\": $PROMPT_JSON}" ;;
    luma-reframe)
        if [ -z "$VIDEO_URL" ]; then echo "Error: --video-url required" >&2; exit 1; fi
        ENDPOINT="luma-flash-reframe"
        PAYLOAD="{\"video_url\": \"$(clean_url "$VIDEO_URL")\", \"prompt\": $PROMPT_JSON}" ;;
    vidu-reference)
        if [ -z "$IMAGE_URL" ]; then echo "Error: --image-url required" >&2; exit 1; fi
        ENDPOINT="vidu-q1-reference"
        PAYLOAD="{\"image_url\": \"$(clean_url "$IMAGE_URL")\", \"prompt\": $PROMPT_JSON}" ;;
    *)
        echo "Error: Unknown operation '$OP'" >&2; exit 1 ;;
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
    [ "$JSON_ONLY" = false ] && echo "Check: bash check-result.sh --id \"$REQUEST_ID\"" >&2
    echo "$SUBMIT"; exit 0
fi

[ "$JSON_ONLY" = false ] && echo "Processing..." >&2
ELAPSED=0; LAST_STATUS=""
while [ $ELAPSED -lt $MAX_WAIT ]; do
    sleep $POLL_INTERVAL; ELAPSED=$((ELAPSED + POLL_INTERVAL))
    RESULT=$(curl -s -X GET "${MUAPI_BASE}/predictions/${REQUEST_ID}/result" "${HEADERS[@]}")
    STATUS=$(echo "$RESULT" | grep -oE '"status"[[:space:]]*:[[:space:]]*"[^"]*"' | sed 's/.*: *"//' | sed 's/"$//')
    if [ "$STATUS" != "$LAST_STATUS" ] && [ "$JSON_ONLY" = false ]; then echo "Status: $STATUS (${ELAPSED}s)" >&2; LAST_STATUS="$STATUS"; fi
    case $STATUS in
        completed)
            [ "$JSON_ONLY" = false ] && echo "Done!" >&2
            URL=$(echo "$RESULT" | grep -o '"outputs":\[[^]]*\]' | grep -o '"[^"]*\.\(mp4\|gif\|png\|jpg\)"' | head -1 | tr -d '"')
            [ -n "$URL" ] && [ "$JSON_ONLY" = false ] && echo "Result URL: $URL" >&2
            echo "$RESULT"; exit 0 ;;
        failed)
            ERR=$(echo "$RESULT" | grep -o '"error":"[^"]*"' | head -1 | cut -d'"' -f4)
            echo "Error: ${ERR:-Operation failed}" >&2; echo "$RESULT"; exit 1 ;;
    esac
done
echo "Error: Timeout after ${MAX_WAIT}s — Request ID: $REQUEST_ID" >&2; exit 1
