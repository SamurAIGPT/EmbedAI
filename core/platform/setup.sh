#!/bin/bash
# muapi.ai Platform Setup
# Usage: ./setup.sh --add-key [KEY] | --show-config | --test

set -e

case "${1:-}" in
    --add-key)
        if [ -n "${2:-}" ]; then
            MUAPI_API_KEY="$2" muapi auth configure --api-key "$2"
        else
            muapi auth configure
        fi ;;

    --show-config)
        muapi auth whoami ;;

    --test)
        muapi auth whoami > /dev/null && echo "API key is valid!" ;;

    --help|-h)
        echo "muapi.ai Platform Setup"
        echo ""
        echo "Usage:"
        echo "  ./setup.sh --add-key [KEY]   Save API key"
        echo "  ./setup.sh --show-config      Show current configuration"
        echo "  ./setup.sh --test             Test API key validity" ;;

    *)
        echo "Usage: ./setup.sh --add-key [KEY] | --show-config | --test" ;;
esac
