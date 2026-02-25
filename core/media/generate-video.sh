#!/bin/bash
# muapi.ai Text-to-Video Generation
# Usage: ./generate-video.sh --prompt "..." [--model MODEL] [options]

set -e

MUAPI_BASE="https://api.muapi.ai/api/v1"

# Defaults
PROMPT=""
MODEL="minimax-pro"
ASPECT_RATIO="16:9"
DURATION=5
GENERATE_AUDIO=true
ASYNC=false
JSON_ONLY=false
MAX_WAIT=600
POLL_INTERVAL=5
ACTION="generate"
REQUEST_ID=""

# Check for --add-key first
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

# Parse arguments
while [[ $# -gt 0 ]]; do
    case $1 in
        --prompt|-p) PROMPT="$2"; shift 2 ;;
        --model|-m) MODEL="$2"; shift 2 ;;
        --aspect-ratio) ASPECT_RATIO="$2"; shift 2 ;;
        --duration) DURATION="$2"; shift 2 ;;
        --no-audio) GENERATE_AUDIO=false; shift ;;
        --async) ASYNC=true; shift ;;
        --status) ACTION="status"; REQUEST_ID="$2"; shift 2 ;;
        --result) ACTION="result"; REQUEST_ID="$2"; shift 2 ;;
        --timeout) MAX_WAIT="$2"; shift 2 ;;
        --json) JSON_ONLY=true; shift ;;
        --help|-h)
            echo "muapi.ai Text-to-Video" >&2
            echo "" >&2
            echo "Usage: ./generate-video.sh --prompt \"...\" [options]" >&2
            echo "" >&2
            echo "Models (--model):" >&2
            echo "  veo3, veo3-fast, kling-v3.0-pro-text-to-video, openai-sora-2-pro-text-to-video" >&2
            echo "  wan2.5-text-to-video, wan2.2-text-to-video, hunyuan-text-to-video" >&2
            echo "  minimax-hailuo-02-pro-t2v (default), pixverse-v5.5-t2v, vidu-v2.0-t2v" >&2
            echo "  luma-dream-machine, runway-text-to-video" >&2
            echo "" >&2
            echo "Options:" >&2
            echo "  --aspect-ratio  16:9, 9:16, 1:1 (default: 16:9)" >&2
            echo "  --duration      Video length in seconds (default: 5)" >&2
            echo "  --no-audio      Disable audio generation" >&2
            echo "  --async         Return request_id immediately" >&2
            echo "  --status ID     Check status of a request" >&2
            echo "  --result ID     Get result of a completed request" >&2
            echo "  --timeout       Max wait seconds (default: 600)" >&2
            exit 0 ;;
        *) shift ;;
    esac
done

if [ -z "$MUAPI_KEY" ]; then
    echo "Error: MUAPI_KEY not set" >&2
    echo "Run: bash generate-video.sh --add-key \"your_key\"" >&2
    exit 1
fi

HEADERS=(-H "x-api-key: $MUAPI_KEY" -H "Content-Type: application/json")

# Handle status/result actions
if [ "$ACTION" = "status" ] || [ "$ACTION" = "result" ]; then
    if [ -z "$REQUEST_ID" ]; then echo "Error: Request ID required" >&2; exit 1; fi
    [ "$JSON_ONLY" = false ] && echo "Checking result for $REQUEST_ID..." >&2
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

# Map model flag to endpoint
case $MODEL in
    veo3)          ENDPOINT="veo3-text-to-video" ;;
    veo3-fast)     ENDPOINT="veo3-fast-text-to-video" ;;
    kling-v3.0-pro) ENDPOINT="kling-v3.0-pro-text-to-video" ;;
    openai-sora-2-pro) ENDPOINT="openai-sora-2-pro-text-to-video" ;;
    wan2.5)        ENDPOINT="wan2.5-text-to-video" ;;
    wan2.2)        ENDPOINT="wan2.2-text-to-video" ;;
    hunyuan)       ENDPOINT="hunyuan-text-to-video" ;;
    runway)        ENDPOINT="runway-text-to-video" ;;
    pixverse-v5.5) ENDPOINT="pixverse-v5.5-t2v" ;;
    vidu-v2.0)     ENDPOINT="vidu-v2.0-t2v" ;;
    minimax-pro)   ENDPOINT="minimax-hailuo-02-pro-t2v" ;;
    luma-dream)    ENDPOINT="luma-dream-machine" ;;
    *)
        echo "Error: Unknown model '$MODEL'" >&2
        echo "Valid: veo3, veo3-fast, kling-v3.0-pro, openai-sora-2-pro," >&2
        echo "       wan2.5, wan2.2, hunyuan, runway, pixverse-v5.5," >&2
        echo "       vidu-v2.0, minimax-pro, luma-dream" >&2
        exit 1 ;;
esac

if [ -z "$PROMPT" ]; then echo "Error: --prompt is required" >&2; exit 1; fi

PROMPT_JSON=$(echo "$PROMPT" | python3 -c 'import json,sys; print(json.dumps(sys.stdin.read().rstrip()))')
PAYLOAD="{\"prompt\": $PROMPT_JSON, \"aspect_ratio\": \"$ASPECT_RATIO\", \"duration\": $DURATION, \"generate_audio\": $GENERATE_AUDIO}"

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
    [ "$JSON_ONLY" = false ] && echo "Check status: bash check-result.sh --id \"$REQUEST_ID\"" >&2
    echo "$SUBMIT"
    exit 0
fi

[ "$JSON_ONLY" = false ] && echo "Waiting for completion (this may take several minutes)..." >&2

ELAPSED=0
LAST_STATUS=""
while [ $ELAPSED -lt $MAX_WAIT ]; do
    sleep $POLL_INTERVAL
    ELAPSED=$((ELAPSED + POLL_INTERVAL))

    RESULT=$(curl -s -X GET "${MUAPI_BASE}/predictions/${REQUEST_ID}/result" "${HEADERS[@]}")
    STATUS=$(echo "$RESULT" | grep -oE '"status"[[:space:]]*:[[:space:]]*"[^"]*"' | sed 's/.*: *"//' | sed 's/"$//')

    if [ "$STATUS" != "$LAST_STATUS" ] && [ "$JSON_ONLY" = false ]; then
        echo "Status: $STATUS (${ELAPSED}s elapsed)" >&2
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
echo "Request ID: $REQUEST_ID" >&2
echo "Check with: bash check-result.sh --id \"$REQUEST_ID\"" >&2
exit 1
