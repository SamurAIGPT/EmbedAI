#!/bin/bash
# muapi.ai Text-to-Video Generation
# Usage: ./generate-video.sh --prompt "..." [--model MODEL] [options]

set -e

MUAPI_BASE="https://api.muapi.ai/api/v1"
SCHEMA_FILE="$(dirname "$0")/../../schema_data.json"

# Defaults
PROMPT=""
MODEL="minimax-pro"
ASPECT_RATIO="16:9"
DURATION=5
GENERATE_AUDIO=true
ASYNC=false
VIEW=false
JSON_ONLY=false
MAX_WAIT=600
POLL_INTERVAL=5
ACTION="generate"
REQUEST_ID=""

# Check for .env
if [ -f ".env" ]; then source .env 2>/dev/null || true; fi

# Parse arguments
while [[ $# -gt 0 ]]; do
    case $1 in
        --prompt|-p) PROMPT="$2"; shift 2 ;;
        --model|-m) MODEL="$2"; shift 2 ;;
        --aspect-ratio) ASPECT_RATIO="$2"; shift 2 ;;
        --duration) DURATION="$2"; shift 2 ;;
        --no-audio) GENERATE_AUDIO=false; shift ;;
        --async) ASYNC=true; shift ;;
        --view) VIEW=true; shift ;;
        --status) ACTION="status"; REQUEST_ID="$2"; shift 2 ;;
        --result) ACTION="result"; REQUEST_ID="$2"; shift 2 ;;
        --timeout) MAX_WAIT="$2"; shift 2 ;;
        --json) JSON_ONLY=true; shift ;;
        --help|-h)
            echo "muapi.ai Text-to-Video" >&2
            echo "" >&2
            echo "Usage: ./generate-video.sh --prompt \"...\" [options]" >&2
            echo "" >&2
            echo "Options:" >&2
            echo "  --prompt, -p    Text description (required)" >&2
            echo "  --model, -m     Model name (default: minimax-pro)" >&2
            echo "  --aspect-ratio  16:9, 9:16, 1:1" >&2
            echo "  --duration      Length in seconds (3-15)" >&2
            echo "  --no-audio      Disable audio generation" >&2
            echo "  --async         Return request_id immediately" >&2
            echo "  --view          Download and open video (macOS only)" >&2
            echo "  --status ID     Check status of a request" >&2
            echo "  --json          Raw JSON output only" >&2
            exit 0 ;;
        *) shift ;;
    esac
done

if [ -z "$MUAPI_KEY" ]; then echo "Error: MUAPI_KEY not set" >&2; exit 1; fi

HEADERS=(-H "x-api-key: $MUAPI_KEY" -H "Content-Type: application/json")

# Handle status/result actions
if [ "$ACTION" = "status" ] || [ "$ACTION" = "result" ]; then
    if [ -z "$REQUEST_ID" ]; then echo "Error: Request ID required" >&2; exit 1; fi
    RESULT=$(curl -s -X GET "${MUAPI_BASE}/predictions/${REQUEST_ID}/result" "${HEADERS[@]}")
    echo "$RESULT"; exit 0
fi

if [ -z "$PROMPT" ]; then echo "Error: --prompt is required" >&2; exit 1; fi

# --- DYNAMIC SCHEMA PARSING ---
if [ ! -f "$SCHEMA_FILE" ]; then echo "Error: schema_data.json not found" >&2; exit 1; fi

MODEL_DATA=$(jq -r ".[] | select(.name == \"$MODEL\")" "$SCHEMA_FILE")
if [ -z "$MODEL_DATA" ]; then
    echo "Error: Model '$MODEL' not found in schema" >&2; exit 1
fi

ENDPOINT=$(echo "$MODEL_DATA" | jq -r '.input_schema.schemas.input_data.endpoint_url')
PARAMS=$(echo "$MODEL_DATA" | jq -r '.input_schema.schemas.input_data.properties | keys[]')

# Build Payload
PROMPT_JSON=$(echo "$PROMPT" | python3 -c 'import json,sys; print(json.dumps(sys.stdin.read().rstrip()))')
PAYLOAD="{\"prompt\": $PROMPT_JSON"

if echo "$PARAMS" | grep -w "aspect_ratio" >/dev/null; then PAYLOAD="$PAYLOAD, \"aspect_ratio\": \"$ASPECT_RATIO\""; fi
if echo "$PARAMS" | grep -w "duration" >/dev/null; then PAYLOAD="$PAYLOAD, \"duration\": $DURATION"; fi
if echo "$PARAMS" | grep -w "generate_audio" >/dev/null; then PAYLOAD="$PAYLOAD, \"generate_audio\": $GENERATE_AUDIO"; fi

PAYLOAD="$PAYLOAD}"

# --- EXECUTION ---
[ "$JSON_ONLY" = false ] && echo "Submitting to $ENDPOINT..." >&2
SUBMIT=$(curl -s -X POST "${MUAPI_BASE}/${ENDPOINT}" "${HEADERS[@]}" -d "$PAYLOAD")

if echo "$SUBMIT" | grep -q '"error"\|"detail"'; then
    ERR=$(echo "$SUBMIT" | jq -r '.error // .detail // empty')
    echo "Error: ${ERR:-$SUBMIT}" >&2; exit 1
fi

REQUEST_ID=$(echo "$SUBMIT" | jq -r '.request_id')
if [ "$ASYNC" = true ]; then echo "$SUBMIT"; exit 0; fi

# Polling
[ "$JSON_ONLY" = false ] && echo "Waiting for completion (Request ID: $REQUEST_ID)..." >&2
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
            [ -z "$EXT" ] || [[ "$EXT" == http* ]] && EXT="mp4"
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
