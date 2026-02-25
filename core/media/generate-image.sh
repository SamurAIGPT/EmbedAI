#!/bin/bash
# muapi.ai Text-to-Image Generation
# Usage: ./generate-image.sh --prompt "..." [--model MODEL] [options]

set -e

MUAPI_BASE="https://api.muapi.ai/api/v1"

# Defaults
PROMPT=""
MODEL="flux-dev"
WIDTH=1024
HEIGHT=1024
ASPECT_RATIO=""
NUM_IMAGES=1
ASYNC=false
JSON_ONLY=false
MAX_WAIT=300
POLL_INTERVAL=3

# Check for --add-key first
for arg in "$@"; do
    if [ "$arg" = "--add-key" ]; then
        shift
        KEY_VALUE=""
        if [[ -n "$1" && ! "$1" =~ ^-- ]]; then
            KEY_VALUE="$1"
        fi
        if [ -z "$KEY_VALUE" ]; then
            echo "Enter your muapi.ai API key:" >&2
            read -r KEY_VALUE
        fi
        if [ -n "$KEY_VALUE" ]; then
            grep -v "^MUAPI_KEY=" .env > .env.tmp 2>/dev/null || true
            mv .env.tmp .env 2>/dev/null || true
            echo "MUAPI_KEY=$KEY_VALUE" >> .env
            echo "MUAPI_KEY saved to .env" >&2
        fi
        exit 0
    fi
done

# Load .env
if [ -f ".env" ]; then source .env 2>/dev/null || true; fi

# Parse arguments
while [[ $# -gt 0 ]]; do
    case $1 in
        --prompt|-p) PROMPT="$2"; shift 2 ;;
        --model|-m) MODEL="$2"; shift 2 ;;
        --width) WIDTH="$2"; shift 2 ;;
        --height) HEIGHT="$2"; shift 2 ;;
        --aspect-ratio) ASPECT_RATIO="$2"; shift 2 ;;
        --num-images) NUM_IMAGES="$2"; shift 2 ;;
        --async) ASYNC=true; shift ;;
        --timeout) MAX_WAIT="$2"; shift 2 ;;
        --json) JSON_ONLY=true; shift ;;
        --help|-h)
            echo "muapi.ai Text-to-Image" >&2
            echo "" >&2
            echo "Usage: ./generate-image.sh --prompt \"...\" [options]" >&2
            echo "" >&2
            echo "Options:" >&2
            echo "  --prompt, -p    Text description (required)" >&2
            echo "  --model, -m     Model (default: flux-dev)" >&2
            echo "                  flux-dev, flux-schnell, nano-banana, nano-banana-pro," >&2
            echo "                  midjourney-v7-text-to-image, gpt4o-text-to-image," >&2
            echo "                  google-imagen4, wan2.1-text-to-image, qwen-text-to-image-2512," >&2
            echo "                  bytedance-seedream-v4.5, ideogram-v3-t2i, reve-text-to-image" >&2
            echo "  --width         Image width (default: 1024)" >&2
            echo "  --height        Image height (default: 1024)" >&2
            echo "  --aspect-ratio  1:1, 16:9, 9:16, 4:3, 3:4 (overrides width/height for some models)" >&2
            echo "  --num-images    Number of images 1-4 (default: 1)" >&2
            echo "  --async         Return request_id immediately" >&2
            echo "  --timeout       Max wait seconds (default: 300)" >&2
            echo "  --json          Raw JSON output only" >&2
            exit 0 ;;
        *) shift ;;
    esac
done

# Validate key
if [ -z "$MUAPI_KEY" ]; then
    echo "Error: MUAPI_KEY not set" >&2
    echo "Run: bash generate-image.sh --add-key \"your_key\"" >&2
    echo "Or:  export MUAPI_KEY=your_key" >&2
    exit 1
fi

if [ -z "$PROMPT" ]; then
    echo "Error: --prompt is required" >&2
    exit 1
fi

# Map model flag to endpoint
case $MODEL in
    flux-dev)            ENDPOINT="flux-dev-image" ;;
    flux-schnell)        ENDPOINT="flux-schnell-image" ;;
    nano-banana)         ENDPOINT="nano-banana" ;;
    nano-banana-pro)     ENDPOINT="nano-banana-pro" ;;
    midjourney-v7-text-to-image) ENDPOINT="midjourney-v7-text-to-image" ;;
    gpt4o-text-to-image)  ENDPOINT="gpt4o-text-to-image" ;;
    google-imagen4)      ENDPOINT="google-imagen4" ;;
    wan2.1-text-to-image) ENDPOINT="wan2.1-text-to-image" ;;
    qwen-text-to-image-2512) ENDPOINT="qwen-text-to-image-2512" ;;
    bytedance-seedream-v4.5) ENDPOINT="bytedance-seedream-v4.5" ;;
    ideogram-v3-t2i)     ENDPOINT="ideogram-v3-t2i" ;;
    reve-text-to-image)  ENDPOINT="reve-text-to-image" ;;
    *)
        echo "Error: Unknown model '$MODEL'" >&2
        echo "Valid: flux-dev, flux-schnell, nano-banana, nano-banana-pro," >&2
        echo "       midjourney-v7-text-to-image, gpt4o-text-to-image," >&2
        echo "       google-imagen4, wan2.1-text-to-image, qwen-text-to-image-2512," >&2
        echo "       bytedance-seedream-v4.5, ideogram-v3-t2i, reve-text-to-image" >&2
        exit 1 ;;
esac

# Build payload
if [ -n "$ASPECT_RATIO" ]; then
    PAYLOAD="{\"prompt\": $(echo "$PROMPT" | python3 -c 'import json,sys; print(json.dumps(sys.stdin.read().rstrip()))'), \"aspect_ratio\": \"$ASPECT_RATIO\", \"num_images\": $NUM_IMAGES}"
else
    PAYLOAD="{\"prompt\": $(echo "$PROMPT" | python3 -c 'import json,sys; print(json.dumps(sys.stdin.read().rstrip()))'), \"width\": $WIDTH, \"height\": $HEIGHT, \"num_images\": $NUM_IMAGES}"
fi

HEADERS=(-H "x-api-key: $MUAPI_KEY" -H "Content-Type: application/json")

[ "$JSON_ONLY" = false ] && echo "Submitting to $ENDPOINT..." >&2

# Submit request
SUBMIT=$(curl -s -X POST "${MUAPI_BASE}/${ENDPOINT}" "${HEADERS[@]}" -d "$PAYLOAD")

if echo "$SUBMIT" | grep -q '"error"'; then
    ERR=$(echo "$SUBMIT" | grep -o '"error":"[^"]*"' | head -1 | cut -d'"' -f4)
    [ -z "$ERR" ] && ERR=$(echo "$SUBMIT" | grep -o '"detail":"[^"]*"' | head -1 | cut -d'"' -f4)
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

# Async: return immediately
if [ "$ASYNC" = true ]; then
    [ "$JSON_ONLY" = false ] && echo "" >&2
    [ "$JSON_ONLY" = false ] && echo "Request submitted. Check status with:" >&2
    [ "$JSON_ONLY" = false ] && echo "  bash check-result.sh --id \"$REQUEST_ID\"" >&2
    echo "$SUBMIT"
    exit 0
fi

# Poll for result
[ "$JSON_ONLY" = false ] && echo "Waiting for completion..." >&2

ELAPSED=0
LAST_STATUS=""
while [ $ELAPSED -lt $MAX_WAIT ]; do
    sleep $POLL_INTERVAL
    ELAPSED=$((ELAPSED + POLL_INTERVAL))

    RESULT=$(curl -s -X GET "${MUAPI_BASE}/predictions/${REQUEST_ID}/result" "${HEADERS[@]}")
    STATUS=$(echo "$RESULT" | grep -oE '"status"[[:space:]]*:[[:space:]]*"[^"]*"' | sed 's/.*: *"//' | sed 's/"$//')

    if [ "$STATUS" != "$LAST_STATUS" ] && [ "$JSON_ONLY" = false ]; then
        echo "Status: $STATUS" >&2
        LAST_STATUS="$STATUS"
    fi

    case $STATUS in
        completed)
            [ "$JSON_ONLY" = false ] && echo "" >&2
            [ "$JSON_ONLY" = false ] && echo "Generation complete!" >&2
            URL=$(echo "$RESULT" | grep -o '"outputs":\[[^]]*\]' | grep -o '"[^"]*\.\(png\|jpg\|jpeg\|webp\)"' | head -1 | tr -d '"')
            [ -n "$URL" ] && [ "$JSON_ONLY" = false ] && echo "Image URL: $URL" >&2
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
echo "Request ID: $REQUEST_ID" >&2
echo "Check with: bash check-result.sh --id \"$REQUEST_ID\"" >&2
exit 1
