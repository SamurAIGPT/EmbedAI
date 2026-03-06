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

if [ "$JSON_ONLY" = true ]; then
    muapi upload file "$FILE" --output-json
elif [ -n "$JQ_EXPR" ]; then
    muapi upload file "$FILE" --output-json --jq "$JQ_EXPR" | tr -d '"'
else
    muapi upload file "$FILE" --output-json --jq ".url" | tr -d '"'
fi
