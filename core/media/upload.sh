#!/bin/bash
# muapi.ai File Upload
# Usage: ./upload.sh --file /path/to/file.jpg
# Returns: CDN URL

set -e

MUAPI_BASE="https://api.muapi.ai/api/v1"
FILE=""
JSON_ONLY=false

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
        --file|-f) FILE="$2"; shift 2 ;;
        --json) JSON_ONLY=true; shift ;;
        --help|-h)
            echo "muapi.ai File Upload" >&2
            echo "Usage: ./upload.sh --file /path/to/file.jpg" >&2
            echo "Returns the CDN URL of the uploaded file." >&2
            echo "" >&2
            echo "Supported: jpg, jpeg, png, gif, webp, mp4, mov, webm, mp3, wav" >&2
            exit 0 ;;
        *) shift ;;
    esac
done

if [ -z "$MUAPI_KEY" ]; then
    echo "Error: MUAPI_KEY not set" >&2
    exit 1
fi

if [ -z "$FILE" ]; then
    echo "Error: --file is required" >&2
    exit 1
fi

if [ ! -f "$FILE" ]; then
    echo "Error: File not found: $FILE" >&2
    exit 1
fi

FILENAME=$(basename "$FILE")
[ "$JSON_ONLY" = false ] && echo "Uploading $FILENAME..." >&2

RESPONSE=$(curl -s -X POST "${MUAPI_BASE}/upload_file" \
    -H "x-api-key: $MUAPI_KEY" \
    -F "file=@${FILE}")

if echo "$RESPONSE" | grep -q '"error"\|"detail"'; then
    ERR=$(echo "$RESPONSE" | grep -o '"detail":"[^"]*"' | head -1 | cut -d'"' -f4)
    [ -z "$ERR" ] && ERR=$(echo "$RESPONSE" | grep -o '"error":"[^"]*"' | head -1 | cut -d'"' -f4)
    echo "Error: ${ERR:-Upload failed}" >&2
    exit 1
fi

CDN_URL=$(echo "$RESPONSE" | grep -o '"url":"[^"]*"' | head -1 | cut -d'"' -f4)

if [ -z "$CDN_URL" ]; then
    echo "Error: No URL in upload response" >&2
    echo "$RESPONSE" >&2
    exit 1
fi

[ "$JSON_ONLY" = false ] && echo "Uploaded: $CDN_URL" >&2

if [ "$JSON_ONLY" = true ]; then
    echo "$RESPONSE"
else
    echo "$CDN_URL"
fi
