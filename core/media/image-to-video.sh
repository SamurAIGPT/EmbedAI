#!/bin/bash
# muapi.ai Image-to-Video Generation
# Usage: ./image-to-video.sh --image-url URL --prompt "..." [--model MODEL] [options]

set -e

MUAPI_BASE="https://api.muapi.ai/api/v1"

# Defaults
IMAGE_URL=""
IMAGE_FILE=""
LAST_IMAGE_URL=""
LAST_IMAGE_FILE=""
PROMPT=""
MODEL="kling-pro"
ASPECT_RATIO="16:9"
DURATION=5
ASYNC=false
JSON_ONLY=false
MAX_WAIT=600
POLL_INTERVAL=5
ACTION="generate"
REQUEST_ID=""

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
        --image-url) IMAGE_URL="$2"; shift 2 ;;
        --file|--image) IMAGE_FILE="$2"; shift 2 ;;
        --last-image-url) LAST_IMAGE_URL="$2"; shift 2 ;;
        --last-image-file) LAST_IMAGE_FILE="$2"; shift 2 ;;
        --prompt|-p) PROMPT="$2"; shift 2 ;;
        --model|-m) MODEL="$2"; shift 2 ;;
        --aspect-ratio) ASPECT_RATIO="$2"; shift 2 ;;
        --duration) DURATION="$2"; shift 2 ;;
        --async) ASYNC=true; shift ;;
        --status) ACTION="status"; REQUEST_ID="$2"; shift 2 ;;
        --result) ACTION="result"; REQUEST_ID="$2"; shift 2 ;;
        --timeout) MAX_WAIT="$2"; shift 2 ;;
        --json) JSON_ONLY=true; shift ;;
        --help|-h)
            echo "muapi.ai Image-to-Video" >&2
            echo "" >&2
            echo "Usage: ./image-to-video.sh --image-url URL --prompt \"...\" [options]" >&2
            echo "" >&2
            echo "Models (--model):" >&2
            echo "  kling-std, kling-pro (default), kling-master" >&2
            echo "  veo3, veo3-fast, wan2, wan22, seedance-pro, seedance-lite" >&2
            echo "  hunyuan, runway, pixverse, vidu, midjourney" >&2
            echo "  minimax-std, minimax-pro" >&2
            echo "" >&2
            echo "Options:" >&2
            echo "  --image-url URL         Input image URL" >&2
            echo "  --file PATH             Local file (auto-uploads)" >&2
            echo "  --last-image-url URL    End frame URL (start+end interpolation)" >&2
            echo "  --last-image-file PATH  Local end frame (auto-uploads)" >&2
            echo "  --prompt TEXT           Motion description" >&2
            echo "  --aspect-ratio          16:9, 9:16, 1:1 (default: 16:9)" >&2
            echo "  --duration              5 or 10 seconds (default: 5)" >&2
            echo "  --async                 Return request_id immediately" >&2
            exit 0 ;;
        *) shift ;;
    esac
done

if [ -z "$MUAPI_KEY" ]; then
    echo "Error: MUAPI_KEY not set" >&2
    exit 1
fi

HEADERS=(-H "x-api-key: $MUAPI_KEY" -H "Content-Type: application/json")

# Status/result check
if [ "$ACTION" = "status" ] || [ "$ACTION" = "result" ]; then
    if [ -z "$REQUEST_ID" ]; then echo "Error: Request ID required" >&2; exit 1; fi
    RESULT=$(curl -s -X GET "${MUAPI_BASE}/predictions/${REQUEST_ID}/result" "${HEADERS[@]}")
    STATUS=$(echo "$RESULT" | grep -oE '"status"[[:space:]]*:[[:space:]]*"[^"]*"' | sed 's/.*: *"//' | sed 's/"$//')
    [ "$JSON_ONLY" = false ] && echo "Status: $STATUS" >&2
    if [ "$STATUS" = "completed" ]; then
        URL=$(echo "$RESULT" | grep -o '"outputs":\[[^]]*\]' | grep -o '"[^"]*\.mp4"' | head -1 | tr -d '"')
        [ -n "$URL" ] && [ "$JSON_ONLY" = false ] && echo "Video URL: $URL" >&2
    fi
    echo "$RESULT"
    exit 0
fi

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

if [ -n "$IMAGE_FILE" ]; then IMAGE_URL=$(upload_file "$IMAGE_FILE"); fi
if [ -n "$LAST_IMAGE_FILE" ]; then LAST_IMAGE_URL=$(upload_file "$LAST_IMAGE_FILE"); fi

if [ -z "$IMAGE_URL" ]; then
    echo "Error: --image-url or --file is required" >&2
    exit 1
fi

# Map model to endpoint
case $MODEL in
    kling-std)     ENDPOINT="kling-v2.1-standard-i2v" ;;
    kling-pro)     ENDPOINT="kling-v2.1-pro-i2v" ;;
    kling-master)  ENDPOINT="kling-v2.1-master-i2v" ;;
    veo3)          ENDPOINT="veo3-image-to-video" ;;
    veo3-fast)     ENDPOINT="veo3-fast-image-to-video" ;;
    wan2)          ENDPOINT="wan2.1-image-to-video" ;;
    wan22)         ENDPOINT="wan2.2-image-to-video" ;;
    seedance-pro)  ENDPOINT="seedance-pro-i2v" ;;
    seedance-lite) ENDPOINT="seedance-lite-i2v" ;;
    hunyuan)       ENDPOINT="hunyuan-image-to-video" ;;
    runway)        ENDPOINT="runway-image-to-video" ;;
    pixverse)      ENDPOINT="pixverse-v4.5-i2v" ;;
    vidu)          ENDPOINT="vidu-v2.0-i2v" ;;
    midjourney)    ENDPOINT="midjourney-v7-image-to-video" ;;
    minimax-std)   ENDPOINT="minimax-hailuo-02-standard-i2v" ;;
    minimax-pro)   ENDPOINT="minimax-hailuo-02-pro-i2v" ;;
    *)
        echo "Error: Unknown model '$MODEL'" >&2
        exit 1 ;;
esac

# Build payload
PROMPT_JSON=$(echo "$PROMPT" | python3 -c 'import json,sys; print(json.dumps(sys.stdin.read().rstrip()))')
IMAGE_URL_CLEAN=$(echo "$IMAGE_URL" | tr -d '"')

if [ -n "$LAST_IMAGE_URL" ]; then
    LAST_JSON=$(echo "$LAST_IMAGE_URL" | tr -d '"')
    PAYLOAD="{\"prompt\": $PROMPT_JSON, \"image_url\": \"$IMAGE_URL_CLEAN\", \"last_image\": \"$LAST_JSON\", \"aspect_ratio\": \"$ASPECT_RATIO\", \"duration\": $DURATION}"
else
    # veo3 uses images_list array
    if [[ "$ENDPOINT" == *"veo3"* ]]; then
        PAYLOAD="{\"prompt\": $PROMPT_JSON, \"images_list\": [\"$IMAGE_URL_CLEAN\"], \"aspect_ratio\": \"$ASPECT_RATIO\"}"
    else
        PAYLOAD="{\"prompt\": $PROMPT_JSON, \"image_url\": \"$IMAGE_URL_CLEAN\", \"aspect_ratio\": \"$ASPECT_RATIO\", \"duration\": $DURATION}"
    fi
fi

[ "$JSON_ONLY" = false ] && echo "Submitting to $ENDPOINT..." >&2

SUBMIT=$(curl -s -X POST "${MUAPI_BASE}/${ENDPOINT}" "${HEADERS[@]}" -d "$PAYLOAD")

if echo "$SUBMIT" | grep -q '"error"\|"detail"'; then
    ERR=$(echo "$SUBMIT" | grep -o '"detail":"[^"]*"' | head -1 | cut -d'"' -f4)
    [ -z "$ERR" ] && ERR=$(echo "$SUBMIT" | grep -o '"error":"[^"]*"' | head -1 | cut -d'"' -f4)
    echo "Error: ${ERR:-$SUBMIT}" >&2
    exit 1
fi

REQUEST_ID=$(echo "$SUBMIT" | grep -oE '"request_id"[[:space:]]*:[[:space:]]*"[^"]*"' | sed 's/.*: *"//' | sed 's/"$//')

if [ -z "$REQUEST_ID" ]; then
    echo "Error: No request_id in response" >&2
    echo "$SUBMIT" >&2
    exit 1
fi

[ "$JSON_ONLY" = false ] && echo "Request ID: $REQUEST_ID" >&2

if [ "$ASYNC" = true ]; then
    [ "$JSON_ONLY" = false ] && echo "" >&2
    [ "$JSON_ONLY" = false ] && echo "Request submitted. Video generation may take 1-5 minutes." >&2
    [ "$JSON_ONLY" = false ] && echo "Check: bash check-result.sh --id \"$REQUEST_ID\"" >&2
    echo "$SUBMIT"
    exit 0
fi

[ "$JSON_ONLY" = false ] && echo "Waiting for completion..." >&2

ELAPSED=0
LAST_STATUS=""
while [ $ELAPSED -lt $MAX_WAIT ]; do
    sleep $POLL_INTERVAL
    ELAPSED=$((ELAPSED + POLL_INTERVAL))

    RESULT=$(curl -s -X GET "${MUAPI_BASE}/predictions/${REQUEST_ID}/result" "${HEADERS[@]}")
    STATUS=$(echo "$RESULT" | grep -oE '"status"[[:space:]]*:[[:space:]]*"[^"]*"' | sed 's/.*: *"//' | sed 's/"$//')

    if [ "$STATUS" != "$LAST_STATUS" ] && [ "$JSON_ONLY" = false ]; then
        echo "Status: $STATUS (${ELAPSED}s)" >&2
        LAST_STATUS="$STATUS"
    fi

    case $STATUS in
        completed)
            [ "$JSON_ONLY" = false ] && echo "" >&2
            [ "$JSON_ONLY" = false ] && echo "Video generation complete!" >&2
            URL=$(echo "$RESULT" | grep -o '"outputs":\[[^]]*\]' | grep -o '"[^"]*\.mp4"' | head -1 | tr -d '"')
            [ -n "$URL" ] && [ "$JSON_ONLY" = false ] && echo "Video URL: $URL" >&2
            echo "$RESULT"
            exit 0 ;;
        failed)
            ERR=$(echo "$RESULT" | grep -o '"error":"[^"]*"' | head -1 | cut -d'"' -f4)
            echo "Error: Generation failed: ${ERR:-unknown}" >&2
            echo "$RESULT"
            exit 1 ;;
    esac
done

echo "Error: Timeout after ${MAX_WAIT}s" >&2
echo "Request ID: $REQUEST_ID — Check: bash check-result.sh --id \"$REQUEST_ID\"" >&2
exit 1
