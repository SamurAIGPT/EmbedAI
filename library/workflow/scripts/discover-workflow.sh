#!/bin/bash
# Assistant Skill: Discover Relevant Workflow
# Thin wrapper around muapi CLI.

QUERY=""
LIMIT=5

while [[ $# -gt 0 ]]; do
    case $1 in
        --query|-q) QUERY="$2"; shift 2 ;;
        --limit)    LIMIT="$2"; shift 2 ;;
        *) shift ;;
    esac
done

if [ -z "$QUERY" ]; then echo "Error: --query is required" >&2; exit 1; fi

muapi workflow discover "$QUERY" --limit "$LIMIT"
