#!/bin/bash
# muapi.ai Check Prediction Result
# Usage: ./check-result.sh --id REQUEST_ID [--once] [--timeout 600] [--json] [--jq EXPR]

set -e

REQUEST_ID=""
ONCE=false
TIMEOUT=600
JSON_ONLY=false
JQ_EXPR=""

while [[ $# -gt 0 ]]; do
    case $1 in
        --id)        REQUEST_ID="$2"; shift 2 ;;
        --once)      ONCE=true; shift ;;
        --timeout)   TIMEOUT="$2"; shift 2 ;;
        --json)      JSON_ONLY=true; shift ;;
        --jq)        JQ_EXPR="$2"; shift 2 ;;
        --help|-h)
            echo "Usage: ./check-result.sh --id REQUEST_ID [--once] [--timeout N] [--json] [--jq EXPR]"
            exit 0 ;;
        *) shift ;;
    esac
done

if [ -z "$REQUEST_ID" ]; then echo "Error: --id is required" >&2; exit 1; fi

ARGS=()
[ "$JSON_ONLY" = true ] && ARGS+=(--output-json)
[ -n "$JQ_EXPR" ]       && ARGS+=(--jq "$JQ_EXPR")

if [ "$ONCE" = true ]; then
    muapi predict result "$REQUEST_ID" "${ARGS[@]}"
else
    muapi predict wait "$REQUEST_ID" --timeout "$TIMEOUT" "${ARGS[@]}"
fi
