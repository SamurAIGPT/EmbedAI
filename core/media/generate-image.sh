#!/bin/bash
# muapi.ai Text-to-Image Generation
# Usage: ./generate-image.sh --prompt "..." [--model MODEL] [options]

set -e

MUAPI_BASE="https://api.muapi.ai/api/v1"
SCHEMA_FILE="$(dirname "$0")/../../schema_data.json"

# Defaults
PROMPT=""
MODEL="flux-dev"
WIDTH=1024
HEIGHT=1024
ASPECT_RATIO=""
RESOLUTION="1k"
NUM_IMAGES=1
ASYNC=false
VIEW=false
JSON_ONLY=false
MAX_WAIT=300
POLL_INTERVAL=3

# Check for .env and setup
if [ -f ".env" ]; then source .env 2>/dev/null || true; fi

# Parse arguments
while [[ $# -gt 0 ]]; do
    case $1 in
        --prompt|-p) PROMPT="$2"; shift 2 ;;
        --model|-m) MODEL="$2"; shift 2 ;;
        --width) WIDTH="$2"; shift 2 ;;
        --height) HEIGHT="$2"; shift 2 ;;
        --aspect-ratio) ASPECT_RATIO="$2"; shift 2 ;;
        --resolution) RESOLUTION="$2"; shift 2 ;;
        --num-images) NUM_IMAGES="$2"; shift 2 ;;
        --async) ASYNC=true; shift ;;
        --view) VIEW=true; shift ;;
        --timeout) MAX_WAIT="$2"; shift 2 ;;
        --json) JSON_ONLY=true; shift ;;
        --help|-h)
            echo "muapi.ai Text-to-Image" >&2
            echo "" >&2
            echo "Usage: ./generate-image.sh --prompt \"...\" [options]" >&2
            echo "" >&2
            echo "Options:" >&2
            echo "  --prompt, -p    Text description (required)" >&2
            echo "  --model, -m     Model name (default: flux-dev)" >&2
            echo "  --aspect-ratio  1:1, 16:9, 9:16, 4:3, 3:4, 21:9" >&2
            echo "  --resolution    1k, 2k, 4k (for supported models)" >&2
            echo "  --width/--height Manual pixel override" >&2
            echo "  --async         Return request_id immediately" >&2
            echo "  --view          Download and open image (macOS only)" >&2
            echo "  --json          Raw JSON output only" >&2
            exit 0 ;;
        *) shift ;;
    esac
done

if [ -z "$MUAPI_KEY" ]; then echo "Error: MUAPI_KEY not set" >&2; exit 1; fi
if [ -z "$PROMPT" ]; then echo "Error: --prompt is required" >&2; exit 1; fi

# --- DYNAMIC SCHEMA PARSING ---
if [ ! -f "$SCHEMA_FILE" ]; then echo "Error: schema_data.json not found at $SCHEMA_FILE" >&2; exit 1; fi

MODEL_DATA=$(jq -r ".[] | select(.name == \"$MODEL\")" "$SCHEMA_FILE")
if [ -z "$MODEL_DATA" ]; then
    echo "Error: Model '$MODEL' not found in schema_data.json" >&2
    echo "Available models: $(jq -r '.[] | .name' "$SCHEMA_FILE" | head -10)..." >&2
    exit 1
fi

ENDPOINT=$(echo "$MODEL_DATA" | jq -r '.input_schema.schemas.input_data.endpoint_url')
PARAMS=$(echo "$MODEL_DATA" | jq -r '.input_schema.schemas.input_data.properties | keys[]')

# Auto-map aspect ratio to width/height if model doesn't support aspect_ratio field
SUPPORTS_AR=$(echo "$PARAMS" | grep -w "aspect_ratio" || true)
if [ -n "$ASPECT_RATIO" ] && [ -z "$SUPPORTS_AR" ]; then
    case $ASPECT_RATIO in
        "1:1")   WIDTH=1024; HEIGHT=1024 ;;
        "16:9")  WIDTH=1344; HEIGHT=768 ;;
        "9:16")  WIDTH=768;  HEIGHT=1344 ;;
        "4:3")   WIDTH=1152; HEIGHT=896 ;;
        "3:4")   WIDTH=896;  HEIGHT=1152 ;;
        "21:9")  WIDTH=1536; HEIGHT=640 ;;
    esac
fi

# Build Payload Dynamically
PROMPT_JSON=$(echo "$PROMPT" | python3 -c 'import json,sys; print(json.dumps(sys.stdin.read().rstrip()))')
PAYLOAD="{\"prompt\": $PROMPT_JSON"

# Add numeric parameters
if echo "$PARAMS" | grep -w "num_images" >/dev/null; then PAYLOAD="$PAYLOAD, \"num_images\": $NUM_IMAGES"; fi
if echo "$PARAMS" | grep -w "width" >/dev/null && [ -z "$SUPPORTS_AR" ]; then PAYLOAD="$PAYLOAD, \"width\": $WIDTH, \"height\": $HEIGHT"; fi

# Add string parameters
if [ -n "$SUPPORTS_AR" ]; then PAYLOAD="$PAYLOAD, \"aspect_ratio\": \"${ASPECT_RATIO:-1:1}\""; fi
if echo "$PARAMS" | grep -w "resolution" >/dev/null; then PAYLOAD="$PAYLOAD, \"resolution\": \"$RESOLUTION\""; fi

PAYLOAD="$PAYLOAD}"

# --- EXECUTION ---
HEADERS=(-H "x-api-key: $MUAPI_KEY" -H "Content-Type: application/json")
[ "$JSON_ONLY" = false ] && echo "Submitting to $ENDPOINT (Model: $MODEL)..." >&2

SUBMIT=$(curl -s -X POST "${MUAPI_BASE}/${ENDPOINT}" "${HEADERS[@]}" -d "$PAYLOAD")

if echo "$SUBMIT" | grep -q '"error"\|"detail"'; then
    ERR=$(echo "$SUBMIT" | jq -r '.error // .detail // empty')
    echo "Error: ${ERR:-$SUBMIT}" >&2; exit 1
fi

REQUEST_ID=$(echo "$SUBMIT" | jq -r '.request_id')
[ "$JSON_ONLY" = false ] && echo "Request ID: $REQUEST_ID" >&2

if [ "$ASYNC" = true ]; then echo "$SUBMIT"; exit 0; fi

# Polling
[ "$JSON_ONLY" = false ] && echo "Waiting for completion..." >&2
ELAPSED=0
while [ $ELAPSED -lt $MAX_WAIT ]; do
    sleep $POLL_INTERVAL
    ELAPSED=$((ELAPSED + POLL_INTERVAL))
    RESULT=$(curl -s -X GET "${MUAPI_BASE}/predictions/${REQUEST_ID}/result" "${HEADERS[@]}")
    STATUS=$(echo "$RESULT" | jq -r '.status')
    if [ "$STATUS" = "completed" ]; then
        URL=$(echo "$RESULT" | jq -r '.outputs[0]')
        [ "$JSON_ONLY" = false ] && echo "Success! URL: $URL" >&2
        
        if [ "$VIEW" = true ]; then
            EXT="${URL##*.}"
            [ -z "$EXT" ] || [[ "$EXT" == http* ]] && EXT="jpg"
            OUTPUT_DIR="$(dirname "$0")/../../media_outputs"
            mkdir -p "$OUTPUT_DIR"
            TEMP_FILE="$OUTPUT_DIR/muapi_$(date +%s).$EXT"
            [ "$JSON_ONLY" = false ] && echo "Downloading to $TEMP_FILE..." >&2
            curl -s -o "$TEMP_FILE" "$URL"
            if [[ "$OSTYPE" == "darwin"* ]]; then
                open "$TEMP_FILE"
            fi
        fi
        
        echo "$RESULT"; exit 0
    elif [ "$STATUS" = "failed" ]; then
        echo "Error: $(echo "$RESULT" | jq -r '.output.error')" >&2; exit 1
    fi
done
exit 1
