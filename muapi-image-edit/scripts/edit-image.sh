#!/bin/bash
# muapi.ai Image Editing (prompt-based)
# Usage: ./edit-image.sh --image-url URL --prompt "..." [--model MODEL]

set -e

MUAPI_BASE="https://api.muapi.ai/api/v1"

IMAGE_URL=""
IMAGE_FILE=""
PROMPT=""
EFFECT=""
MODEL="flux-kontext-pro"
ASPECT_RATIO="1:1"
NUM_IMAGES=1
ASYNC=false
JSON_ONLY=false
MAX_WAIT=300
POLL_INTERVAL=3

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
        --file) IMAGE_FILE="$2"; shift 2 ;;
        --prompt|-p) PROMPT="$2"; shift 2 ;;
        --effect) EFFECT="$2"; shift 2 ;;
        --model|-m) MODEL="$2"; shift 2 ;;
        --aspect-ratio) ASPECT_RATIO="$2"; shift 2 ;;
        --num-images) NUM_IMAGES="$2"; shift 2 ;;
        --async) ASYNC=true; shift ;;
        --timeout) MAX_WAIT="$2"; shift 2 ;;
        --json) JSON_ONLY=true; shift ;;
        --help|-h)
            echo "muapi.ai Image Edit" >&2
            echo "" >&2
            echo "Usage: ./edit-image.sh --image-url URL --prompt \"...\" [options]" >&2
            echo "" >&2
            echo "Models (--model):" >&2
            echo "  flux-kontext-dev, flux-kontext-pro (default), flux-kontext-max" >&2
            echo "  flux-kontext-effects (use --effect for style name)" >&2
            echo "  gpt4o, gpt4o-edit, reve, seededit, midjourney" >&2
            echo "  midjourney-style, midjourney-omni, qwen" >&2
            exit 0 ;;
        *) shift ;;
    esac
done

if [ -z "$MUAPI_KEY" ]; then echo "Error: MUAPI_KEY not set" >&2; exit 1; fi

HEADERS=(-H "x-api-key: $MUAPI_KEY" -H "Content-Type: application/json")

# Auto-upload local file
if [ -n "$IMAGE_FILE" ]; then
    if [ ! -f "$IMAGE_FILE" ]; then echo "Error: File not found: $IMAGE_FILE" >&2; exit 1; fi
    [ "$JSON_ONLY" = false ] && echo "Uploading $(basename "$IMAGE_FILE")..." >&2
    UPLOAD_RESP=$(curl -s -X POST "${MUAPI_BASE}/upload_file" \
        -H "x-api-key: $MUAPI_KEY" -F "file=@${IMAGE_FILE}")
    IMAGE_URL=$(echo "$UPLOAD_RESP" | grep -o '"url":"[^"]*"' | head -1 | cut -d'"' -f4)
    if [ -z "$IMAGE_URL" ]; then echo "Error: Upload failed" >&2; echo "$UPLOAD_RESP" >&2; exit 1; fi
    [ "$JSON_ONLY" = false ] && echo "Uploaded: $IMAGE_URL" >&2
fi

if [ -z "$IMAGE_URL" ]; then echo "Error: --image-url or --file is required" >&2; exit 1; fi
IMAGE_URL_CLEAN=$(echo "$IMAGE_URL" | tr -d '"')

# Map model to endpoint and build payload
PROMPT_JSON=$(echo "${PROMPT:-}" | python3 -c 'import json,sys; print(json.dumps(sys.stdin.read().rstrip()))')

case $MODEL in
    flux-kontext-dev)
        ENDPOINT="flux-kontext-dev-i2i"
        PAYLOAD="{\"prompt\": $PROMPT_JSON, \"images_list\": [\"$IMAGE_URL_CLEAN\"], \"aspect_ratio\": \"$ASPECT_RATIO\", \"num_images\": $NUM_IMAGES}" ;;
    flux-kontext-pro)
        ENDPOINT="flux-kontext-pro-i2i"
        PAYLOAD="{\"prompt\": $PROMPT_JSON, \"images_list\": [\"$IMAGE_URL_CLEAN\"], \"aspect_ratio\": \"$ASPECT_RATIO\", \"num_images\": $NUM_IMAGES}" ;;
    flux-kontext-max)
        ENDPOINT="flux-kontext-max-i2i"
        PAYLOAD="{\"prompt\": $PROMPT_JSON, \"images_list\": [\"$IMAGE_URL_CLEAN\"], \"aspect_ratio\": \"$ASPECT_RATIO\", \"num_images\": $NUM_IMAGES}" ;;
    flux-kontext-effects)
        ENDPOINT="flux-kontext-effects"
        EFFECT_VAL="${EFFECT:-$PROMPT}"
        EFFECT_JSON=$(echo "$EFFECT_VAL" | python3 -c 'import json,sys; print(json.dumps(sys.stdin.read().rstrip()))')
        PAYLOAD="{\"image_url\": \"$IMAGE_URL_CLEAN\", \"effect\": $EFFECT_JSON}" ;;
    gpt4o)
        ENDPOINT="gpt4o-image-to-image"
        PAYLOAD="{\"prompt\": $PROMPT_JSON, \"image_url\": \"$IMAGE_URL_CLEAN\"}" ;;
    gpt4o-edit)
        ENDPOINT="gpt4o-edit"
        PAYLOAD="{\"prompt\": $PROMPT_JSON, \"image_url\": \"$IMAGE_URL_CLEAN\"}" ;;
    reve)
        ENDPOINT="reve-image-edit"
        PAYLOAD="{\"prompt\": $PROMPT_JSON, \"image_url\": \"$IMAGE_URL_CLEAN\"}" ;;
    seededit)
        ENDPOINT="bytedance-seededit-image"
        PAYLOAD="{\"prompt\": $PROMPT_JSON, \"image_url\": \"$IMAGE_URL_CLEAN\"}" ;;
    midjourney)
        ENDPOINT="midjourney-v7-image-to-image"
        PAYLOAD="{\"prompt\": $PROMPT_JSON, \"image_url\": \"$IMAGE_URL_CLEAN\"}" ;;
    midjourney-style)
        ENDPOINT="midjourney-v7-style-reference"
        PAYLOAD="{\"prompt\": $PROMPT_JSON, \"style_reference_url\": \"$IMAGE_URL_CLEAN\"}" ;;
    midjourney-omni)
        ENDPOINT="midjourney-v7-omni-reference"
        PAYLOAD="{\"prompt\": $PROMPT_JSON, \"omni_reference_url\": \"$IMAGE_URL_CLEAN\"}" ;;
    qwen)
        ENDPOINT="qwen-image-edit"
        PAYLOAD="{\"prompt\": $PROMPT_JSON, \"image_url\": \"$IMAGE_URL_CLEAN\"}" ;;
    *)
        echo "Error: Unknown model '$MODEL'" >&2; exit 1 ;;
esac

[ "$JSON_ONLY" = false ] && echo "Submitting to $ENDPOINT..." >&2

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

[ "$JSON_ONLY" = false ] && echo "Waiting for completion..." >&2
ELAPSED=0; LAST_STATUS=""
while [ $ELAPSED -lt $MAX_WAIT ]; do
    sleep $POLL_INTERVAL; ELAPSED=$((ELAPSED + POLL_INTERVAL))
    RESULT=$(curl -s -X GET "${MUAPI_BASE}/predictions/${REQUEST_ID}/result" "${HEADERS[@]}")
    STATUS=$(echo "$RESULT" | grep -oE '"status"[[:space:]]*:[[:space:]]*"[^"]*"' | sed 's/.*: *"//' | sed 's/"$//')
    if [ "$STATUS" != "$LAST_STATUS" ] && [ "$JSON_ONLY" = false ]; then echo "Status: $STATUS" >&2; LAST_STATUS="$STATUS"; fi
    case $STATUS in
        completed)
            [ "$JSON_ONLY" = false ] && echo "Done!" >&2
            URL=$(echo "$RESULT" | grep -o '"outputs":\[[^]]*\]' | grep -o '"[^"]*\.\(png\|jpg\|jpeg\|webp\)"' | head -1 | tr -d '"')
            [ -n "$URL" ] && [ "$JSON_ONLY" = false ] && echo "Image URL: $URL" >&2
            echo "$RESULT"; exit 0 ;;
        failed)
            ERR=$(echo "$RESULT" | grep -o '"error":"[^"]*"' | head -1 | cut -d'"' -f4)
            echo "Error: ${ERR:-Generation failed}" >&2; echo "$RESULT"; exit 1 ;;
    esac
done
echo "Error: Timeout after ${MAX_WAIT}s — Request ID: $REQUEST_ID" >&2; exit 1
