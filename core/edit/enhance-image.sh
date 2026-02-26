#!/bin/bash
# muapi.ai Image Enhancement (one-click operations)
# Usage: ./enhance-image.sh --op upscale --image-url URL

set -e

MUAPI_BASE="https://api.muapi.ai/api/v1"

OP=""
IMAGE_URL=""
IMAGE_FILE=""
FACE_URL=""
FACE_FILE=""
MASK_URL=""
MASK_FILE=""
PROMPT=""
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
        --op) OP="$2"; shift 2 ;;
        --image-url) IMAGE_URL="$2"; shift 2 ;;
        --file) IMAGE_FILE="$2"; shift 2 ;;
        --face-url) FACE_URL="$2"; shift 2 ;;
        --face-file) FACE_FILE="$2"; shift 2 ;;
        --mask-url) MASK_URL="$2"; shift 2 ;;
        --mask-file) MASK_FILE="$2"; shift 2 ;;
        --prompt|-p) PROMPT="$2"; shift 2 ;;
        --async) ASYNC=true; shift ;;
        --timeout) MAX_WAIT="$2"; shift 2 ;;
        --json) JSON_ONLY=true; shift ;;
        --help|-h)
            echo "muapi.ai Image Enhancement" >&2
            echo "" >&2
            echo "Usage: ./enhance-image.sh --op OPERATION --image-url URL" >&2
            echo "" >&2
            echo "Operations (--op):" >&2
            echo "  upscale          Upscale image resolution" >&2
            echo "  background-remove Remove image background" >&2
            echo "  face-swap        Swap face (requires --face-url)" >&2
            echo "  skin-enhance     Smooth and enhance skin" >&2
            echo "  colorize         Colorize black & white photo" >&2
            echo "  ghibli           Studio Ghibli art style" >&2
            echo "  anime            Anime style (optional --prompt)" >&2
            echo "  extend           Extend/outpaint image" >&2
            echo "  product-shot     Clean product shot background" >&2
            echo "  product-photo    Professional product photography (requires --prompt)" >&2
            echo "  object-erase     Erase object (requires --mask-url or --mask-file index)" >&2
            echo "" >&2
            echo "File Inputs:" >&2
            echo "  --file           Main image file" >&2
            echo "  --face-file      Face image for swap" >&2
            echo "  --mask-file      Mask image for erase" >&2
            exit 0 ;;
        *) shift ;;
    esac
done

if [ -z "$MUAPI_KEY" ]; then echo "Error: MUAPI_KEY not set" >&2; exit 1; fi
if [ -z "$OP" ]; then echo "Error: --op is required" >&2; exit 1; fi

HEADERS=(-H "x-api-key: $MUAPI_KEY" -H "Content-Type: application/json")

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
if [ -n "$FACE_FILE" ]; then FACE_URL=$(upload_file "$FACE_FILE"); fi
if [ -n "$MASK_FILE" ]; then MASK_URL=$(upload_file "$MASK_FILE"); fi

if [ -z "$IMAGE_URL" ]; then echo "Error: --image-url or --file is required" >&2; exit 1; fi
IMAGE_URL_CLEAN=$(echo "$IMAGE_URL" | tr -d '"')

# Map operation to endpoint and payload
PROMPT_JSON=$(echo "${PROMPT:-}" | python3 -c 'import json,sys; print(json.dumps(sys.stdin.read().rstrip()))')

case $OP in
    upscale)
        ENDPOINT="ai-image-upscale"
        PAYLOAD="{\"image_url\": \"$IMAGE_URL_CLEAN\"}" ;;
    background-remove)
        ENDPOINT="ai-background-remover"
        PAYLOAD="{\"image_url\": \"$IMAGE_URL_CLEAN\"}" ;;
    face-swap)
        if [ -z "$FACE_URL" ]; then echo "Error: --face-url is required for face-swap" >&2; exit 1; fi
        FACE_CLEAN=$(echo "$FACE_URL" | tr -d '"')
        ENDPOINT="ai-image-face-swap"
        PAYLOAD="{\"image_url\": \"$IMAGE_URL_CLEAN\", \"face_image_url\": \"$FACE_CLEAN\"}" ;;
    skin-enhance)
        ENDPOINT="ai-skin-enhancer"
        PAYLOAD="{\"image_url\": \"$IMAGE_URL_CLEAN\"}" ;;
    colorize)
        ENDPOINT="ai-color-photo"
        PAYLOAD="{\"image_url\": \"$IMAGE_URL_CLEAN\"}" ;;
    ghibli)
        ENDPOINT="ai-ghibli-style"
        PAYLOAD="{\"image_url\": \"$IMAGE_URL_CLEAN\"}" ;;
    anime)
        ENDPOINT="ai-anime-generator"
        PAYLOAD="{\"image_url\": \"$IMAGE_URL_CLEAN\", \"prompt\": $PROMPT_JSON}" ;;
    extend)
        ENDPOINT="ai-image-extension"
        PAYLOAD="{\"image_url\": \"$IMAGE_URL_CLEAN\"}" ;;
    product-shot)
        ENDPOINT="ai-product-shot"
        PAYLOAD="{\"image_url\": \"$IMAGE_URL_CLEAN\"}" ;;
    product-photo)
        ENDPOINT="ai-product-photography"
        PAYLOAD="{\"image_url\": \"$IMAGE_URL_CLEAN\", \"prompt\": $PROMPT_JSON}" ;;
    object-erase)
        if [ -z "$MASK_URL" ]; then echo "Error: --mask-url is required for object-erase" >&2; exit 1; fi
        MASK_CLEAN=$(echo "$MASK_URL" | tr -d '"')
        ENDPOINT="ai-object-eraser"
        PAYLOAD="{\"image_url\": \"$IMAGE_URL_CLEAN\", \"mask_url\": \"$MASK_CLEAN\"}" ;;
    *)
        echo "Error: Unknown operation '$OP'" >&2
        echo "Valid: upscale, background-remove, face-swap, skin-enhance, colorize," >&2
        echo "       ghibli, anime, extend, product-shot, product-photo, object-erase" >&2
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
    [ "$JSON_ONLY" = false ] && echo "Check: bash check-result.sh --id \"$REQUEST_ID\"" >&2
    echo "$SUBMIT"; exit 0
fi

[ "$JSON_ONLY" = false ] && echo "Processing..." >&2
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
            echo "Error: ${ERR:-Enhancement failed}" >&2; echo "$RESULT"; exit 1 ;;
    esac
done
echo "Error: Timeout after ${MAX_WAIT}s — Request ID: $REQUEST_ID" >&2; exit 1
