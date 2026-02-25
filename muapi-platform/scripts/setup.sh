#!/bin/bash
# muapi.ai Platform Setup
# Usage: ./setup.sh --add-key [KEY] | --show-config

set -e

MUAPI_BASE="https://api.muapi.ai/api/v1"

ACTION="help"

while [[ $# -gt 0 ]]; do
    case $1 in
        --add-key)
            ACTION="add-key"
            KEY_VALUE=""
            if [[ -n "$2" && ! "$2" =~ ^-- ]]; then
                KEY_VALUE="$2"
                shift
            fi
            shift ;;
        --show-config)
            ACTION="show-config"
            shift ;;
        --test)
            ACTION="test"
            shift ;;
        --help|-h)
            echo "muapi.ai Platform Setup" >&2
            echo "" >&2
            echo "Usage:" >&2
            echo "  ./setup.sh --add-key [KEY]   Save MUAPI_KEY to .env" >&2
            echo "  ./setup.sh --show-config      Show current configuration" >&2
            echo "  ./setup.sh --test             Test API key validity" >&2
            exit 0 ;;
        *) shift ;;
    esac
done

if [ -f ".env" ]; then source .env 2>/dev/null || true; fi

case $ACTION in
    add-key)
        if [ -z "$KEY_VALUE" ]; then
            echo "Enter your muapi.ai API key (get one at https://muapi.ai/dashboard):"
            read -r KEY_VALUE
        fi
        if [ -z "$KEY_VALUE" ]; then
            echo "Error: No API key provided" >&2; exit 1
        fi
        # Remove existing key and add new one
        grep -v "^MUAPI_KEY=" .env > .env.tmp 2>/dev/null || true
        mv .env.tmp .env 2>/dev/null || true
        echo "MUAPI_KEY=$KEY_VALUE" >> .env
        echo "MUAPI_KEY saved to .env"
        echo ""
        echo "You can now use all muapi scripts. Example:"
        echo "  bash generate-image.sh --prompt \"a sunset\" --model flux-dev" ;;

    show-config)
        echo "muapi.ai Configuration"
        echo "====================="
        if [ -n "$MUAPI_KEY" ]; then
            MASKED="${MUAPI_KEY:0:8}...${MUAPI_KEY: -4}"
            echo "MUAPI_KEY: $MASKED"
            echo "Status: Configured"
        else
            echo "MUAPI_KEY: Not set"
            echo "Status: Not configured"
            echo ""
            echo "Run: bash setup.sh --add-key \"your_key_here\""
        fi
        echo ""
        echo "API Base URL: $MUAPI_BASE" ;;

    test)
        if [ -z "$MUAPI_KEY" ]; then
            echo "Error: MUAPI_KEY not set. Run: bash setup.sh --add-key" >&2; exit 1
        fi
        echo "Testing API key..."
        # Test by checking an obviously invalid prediction (expect 404, not 401)
        RESPONSE=$(curl -s -o /dev/null -w "%{http_code}" \
            -H "x-api-key: $MUAPI_KEY" \
            "${MUAPI_BASE}/predictions/test-connection/result")
        if [ "$RESPONSE" = "401" ]; then
            echo "Error: API key is invalid or expired" >&2; exit 1
        elif [ "$RESPONSE" = "404" ] || [ "$RESPONSE" = "200" ]; then
            echo "API key is valid!"
        else
            echo "Warning: Unexpected response code: $RESPONSE (may still be valid)"
        fi ;;

    *)
        echo "muapi.ai Platform Setup" >&2
        echo "Usage: ./setup.sh --add-key [KEY] | --show-config | --test" >&2
        exit 0 ;;
esac
