#!/bin/bash
# muapi.ai Image Editing
# Usage: ./edit-image.sh --image-url URL --prompt "..." [--model MODEL]

set -e

IMAGE_URL=""
IMAGE_FILE=""
PROMPT=""
MODEL="flux-kontext-pro"
ASPECT_RATIO="1:1"
NUM_IMAGES=1
ASYNC=false
JSON_ONLY=false
JQ_EXPR=""
TIMEOUT=300

while [[ $# -gt 0 ]]; do
    case $1 in
        --image-url)    IMAGE_URL="$2"; shift 2 ;;
        --file|-f)      IMAGE_FILE="$2"; shift 2 ;;
        --prompt|-p)    PROMPT="$2"; shift 2 ;;
        --model|-m)     MODEL="$2"; shift 2 ;;
        --aspect-ratio) ASPECT_RATIO="$2"; shift 2 ;;
        --num-images)   NUM_IMAGES="$2"; shift 2 ;;
        --async)        ASYNC=true; shift ;;
        --timeout)      TIMEOUT="$2"; shift 2 ;;
        --json)         JSON_ONLY=true; shift ;;
        --jq)           JQ_EXPR="$2"; shift 2 ;;
        --help|-h)
            echo "Usage: ./edit-image.sh --image-url URL --prompt \"...\" [options]"
            echo ""
            muapi image models
            exit 0 ;;
        *) shift ;;
    esac
done

if [ -z "$PROMPT" ]; then echo "Error: --prompt is required" >&2; exit 1; fi

# Auto-upload local file if provided
if [ -n "$IMAGE_FILE" ]; then
    echo "Uploading $(basename "$IMAGE_FILE")..." >&2
    IMAGE_URL=$(bash "$(dirname "$0")/../media/upload.sh" --file "$IMAGE_FILE")
fi

if [ -z "$IMAGE_URL" ]; then echo "Error: --image-url or --file is required" >&2; exit 1; fi

ARGS=(--model "$MODEL" --image "$IMAGE_URL" --aspect-ratio "$ASPECT_RATIO" --num-images "$NUM_IMAGES")
[ "$JSON_ONLY" = true ] && ARGS+=(--output-json)
[ -n "$JQ_EXPR" ]       && ARGS+=(--jq "$JQ_EXPR")
[ "$ASYNC" = true ]     && ARGS+=(--no-wait) || ARGS+=(--wait)

muapi image edit "$PROMPT" "${ARGS[@]}"
