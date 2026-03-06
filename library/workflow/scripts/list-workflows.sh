#!/bin/bash
# Assistant Skill: List Workflows
# Thin wrapper around muapi CLI.

JSON_ONLY=false
LIMIT=20

while [[ $# -gt 0 ]]; do
    case $1 in
        --json) JSON_ONLY=true; shift ;;
        --limit) LIMIT="$2"; shift 2 ;;
        *) shift ;;
    esac
done

if [ "$JSON_ONLY" = true ]; then
    muapi workflow list --output-json
else
    muapi workflow list | head -n $((LIMIT + 5))
fi
