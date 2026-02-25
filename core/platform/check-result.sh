#!/bin/bash
# muapi.ai Check Prediction Result
# Usage: ./check-result.sh --id REQUEST_ID [--once] [--timeout 600]

set -e

MUAPI_BASE="https://api.muapi.ai/api/v1"

REQUEST_ID=""
ONCE=false
JSON_ONLY=false
MAX_WAIT=600
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
        --id) REQUEST_ID="$2"; shift 2 ;;
        --once) ONCE=true; shift ;;
        --timeout) MAX_WAIT="$2"; shift 2 ;;
        --json) JSON_ONLY=true; shift ;;
        --help|-h)
            echo "muapi.ai Check Prediction Result" >&2
            echo "" >&2
            echo "Usage: ./check-result.sh --id REQUEST_ID [options]" >&2
            echo "" >&2
            echo "Options:" >&2
            echo "  --id ID       Request ID to check (required)" >&2
            echo "  --once        Check once and return (no polling)" >&2
            echo "  --timeout N   Max wait seconds (default: 600)" >&2
            echo "  --json        Output raw JSON only" >&2
            exit 0 ;;
        *) shift ;;
    esac
done

if [ -z "$MUAPI_KEY" ]; then
    echo "Error: MUAPI_KEY not set" >&2
    echo "Run: bash setup.sh --add-key \"your_key\"" >&2
    exit 1
fi

if [ -z "$REQUEST_ID" ]; then
    echo "Error: --id is required" >&2
    exit 1
fi

HEADERS=(-H "x-api-key: $MUAPI_KEY")

# Single check mode
if [ "$ONCE" = true ]; then
    RESULT=$(curl -s -X GET "${MUAPI_BASE}/predictions/${REQUEST_ID}/result" "${HEADERS[@]}")
    STATUS=$(echo "$RESULT" | grep -oE '"status"[[:space:]]*:[[:space:]]*"[^"]*"' | sed 's/.*: *"//' | sed 's/"$//')
    [ "$JSON_ONLY" = false ] && echo "Status: $STATUS" >&2
    if [ "$STATUS" = "completed" ]; then
        URL=$(echo "$RESULT" | grep -o '"outputs":\[[^]]*\]' | grep -o '"[^"]*\.\(mp4\|png\|jpg\|jpeg\|webp\|mp3\|wav\)"' | head -1 | tr -d '"')
        [ -n "$URL" ] && [ "$JSON_ONLY" = false ] && echo "Result URL: $URL" >&2
    fi
    echo "$RESULT"
    exit 0
fi

# Poll mode
[ "$JSON_ONLY" = false ] && echo "Polling result for $REQUEST_ID..." >&2

ELAPSED=0
LAST_STATUS=""

while [ $ELAPSED -lt $MAX_WAIT ]; do
    RESULT=$(curl -s -X GET "${MUAPI_BASE}/predictions/${REQUEST_ID}/result" "${HEADERS[@]}")
    STATUS=$(echo "$RESULT" | grep -oE '"status"[[:space:]]*:[[:space:]]*"[^"]*"' | sed 's/.*: *"//' | sed 's/"$//')

    if [ "$STATUS" != "$LAST_STATUS" ] && [ "$JSON_ONLY" = false ]; then
        echo "Status: $STATUS (${ELAPSED}s elapsed)" >&2
        LAST_STATUS="$STATUS"
    fi

    case $STATUS in
        completed)
            [ "$JSON_ONLY" = false ] && echo "" >&2
            [ "$JSON_ONLY" = false ] && echo "Done!" >&2
            # Extract result URL (try mp4, then image, then audio)
            URL=$(echo "$RESULT" | grep -o '"outputs":\[[^]]*\]' | grep -o '"[^"]*\.mp4"' | head -1 | tr -d '"')
            if [ -z "$URL" ]; then
                URL=$(echo "$RESULT" | grep -o '"outputs":\[[^]]*\]' | grep -o '"[^"]*\.\(png\|jpg\|jpeg\|webp\)"' | head -1 | tr -d '"')
            fi
            if [ -z "$URL" ]; then
                URL=$(echo "$RESULT" | grep -o '"outputs":\[[^]]*\]' | grep -o '"[^"]*\.\(mp3\|wav\)"' | head -1 | tr -d '"')
            fi
            [ -n "$URL" ] && [ "$JSON_ONLY" = false ] && echo "Result URL: $URL" >&2
            echo "$RESULT"
            exit 0 ;;
        failed)
            ERR=$(echo "$RESULT" | grep -o '"error":"[^"]*"' | head -1 | cut -d'"' -f4)
            [ "$JSON_ONLY" = false ] && echo "Error: ${ERR:-Generation failed}" >&2
            echo "$RESULT"
            exit 1 ;;
    esac

    sleep $POLL_INTERVAL
    ELAPSED=$((ELAPSED + POLL_INTERVAL))
done

echo "Error: Timeout after ${MAX_WAIT}s" >&2
echo "Request ID: $REQUEST_ID is still processing." >&2
echo "Run again: bash check-result.sh --id \"$REQUEST_ID\"" >&2
exit 1
