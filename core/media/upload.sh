#!/bin/bash
# muapi.ai File Upload
# Usage: ./upload.sh --file /path/to/file.jpg
# Returns: CDN URL

set -e

FILE=""
JSON_ONLY=false
JQ_EXPR=".url"

while [[ $# -gt 0 ]]; do
    case $1 in
        --file|-f) FILE="$2"; shift 2 ;;
        --json)    JSON_ONLY=true; JQ_EXPR=""; shift ;;
        --jq)      JQ_EXPR="$2"; shift 2 ;;
        --help|-h)
            echo "Usage: ./upload.sh --file /path/to/file.jpg"
            echo "Returns the CDN URL of the uploaded file."
            exit 0 ;;
        *) shift ;;
    esac
done

if [ -z "$FILE" ]; then echo "Error: --file is required" >&2; exit 1; fi

if [ -f ".env" ]; then source .env 2>/dev/null || true; fi
if [ -z "$MUAPI_KEY" ]; then echo "Error: MUAPI_KEY not set" >&2; exit 1; fi

MUAPI_BASE="https://api.muapi.ai/api/v1"

[ "$JSON_ONLY" = false ] && echo "Uploading $(basename "$FILE")..." >&2
RESP=$(curl -s -X POST "${MUAPI_BASE}/upload_file" -H "x-api-key: $MUAPI_KEY" -F "file=@${FILE}")

if [ "$JSON_ONLY" = true ]; then
    echo "$RESP"
elif [ -n "$JQ_EXPR" ]; then
    echo "$RESP" | jq -r "$JQ_EXPR"
else
    echo "$RESP" | jq -r ".url // empty"
fi
