#!/bin/sh
# What does the app see, and who can reach it? A measurement for the machine you run it on.
#
#   sh scripts/probe-docker-trust.sh [--strict]
#
# TRUSTED_PROXIES names the addresses whose X-Forwarded-For and X-Forwarded-Proto the server
# believes. Whether an address is a safe thing to name depends on the Docker host: on a native
# Linux engine a connection from the host arrives from the network's gateway address and other
# networks cannot reach the app; on Docker Desktop other containers can reach it and ALSO arrive
# from the gateway address. This script starts a throwaway server that only reports who connected,
# on a throwaway network with the same addressing as compose.yaml, and prints what it saw.
#
# It changes nothing else: one network, one container, two short-lived curl containers, all removed.
# --strict exits 1 when another network can reach the app or arrives looking like the host side
# (do not trust the gateway address on such a machine; trust the proxy container's own address).

PROBE_NET=open-dots-probe-net
PROBE_NAME=open-dots-probe
SUBNET=172.31.251.0/24
GATEWAY=172.31.251.1
PROBE_IP=172.31.251.5
PORT=18099
PY_IMAGE=${PY_IMAGE:-python:3.14-slim-bookworm}
CURL_IMAGE=${CURL_IMAGE:-curlimages/curl}
STRICT=0
[ "$1" = "--strict" ] && STRICT=1

cleanup() {
  docker rm -f "$PROBE_NAME" >/dev/null 2>&1
  docker network rm "$PROBE_NET" >/dev/null 2>&1
}
trap cleanup EXIT INT TERM
cleanup

docker network create --subnet "$SUBNET" --gateway "$GATEWAY" "$PROBE_NET" >/dev/null || exit 2

# A server that answers with the address of whoever connected to it.
docker run -d --name "$PROBE_NAME" --network "$PROBE_NET" --ip "$PROBE_IP" -p "127.0.0.1:$PORT:8000" "$PY_IMAGE" python -c '
import http.server
class H(http.server.BaseHTTPRequestHandler):
    def do_GET(self):
        body = ("peer=%s\n" % self.client_address[0]).encode()
        self.send_response(200); self.send_header("Content-Length", str(len(body))); self.end_headers(); self.wfile.write(body)
    def log_message(self, *args): pass
http.server.ThreadingHTTPServer(("0.0.0.0", 8000), H).serve_forever()
' >/dev/null || exit 2

for i in 1 2 3 4 5 6 7 8 9 10; do
  curl -s -m 2 "http://127.0.0.1:$PORT/" >/dev/null 2>&1 && break
  sleep 1
done

seen() { sed -n 's/^peer=//p'; }
try() { docker run --rm "$@" 2>/dev/null | seen; }

host_side=$(curl -s -m 5 "http://127.0.0.1:$PORT/" | seen)
same_net=$(try --network "$PROBE_NET" "$CURL_IMAGE" -s -m 5 "http://$PROBE_IP:8000/")
other_net=$(try "$CURL_IMAGE" -s -m 5 "http://$PROBE_IP:8000/")
other_host=$(try "$CURL_IMAGE" -s -m 5 "http://host.docker.internal:$PORT/")

show() { [ -n "$2" ] && echo "  $1 -> the app sees $2" || echo "  $1 -> unreachable"; }
echo "Docker host: $(docker version --format '{{.Server.Os}} engine {{.Server.Version}}' 2>/dev/null), network $SUBNET (gateway $GATEWAY)"
show "from this machine, to the published loopback port        " "$host_side"
show "from a container on the same network                      " "$same_net"
show "from a container on ANOTHER network, to the app's address " "$other_net"
show "from a container on another network, via host.docker.internal" "$other_host"

echo
risky=0
if [ "$host_side" = "$GATEWAY" ]; then
  echo "  A host-side proxy reaches the app as $GATEWAY: that is what TRUSTED_PROXIES would name for a proxy on the host."
fi
if [ -n "$other_net" ] || [ -n "$other_host" ]; then
  risky=1
  echo "  WARNING: containers on other networks can reach the app on this machine."
  if [ "$other_net$other_host" = "$GATEWAY$GATEWAY" ] || [ "$other_net" = "$GATEWAY" ] || [ "$other_host" = "$GATEWAY" ]; then
    echo "  They arrive as $GATEWAY, the same address as the host side: trusting the gateway would trust them too."
  fi
  echo "  Trust only the proxy container's own fixed address (the compose overlays do), never the gateway or the whole subnet."
else
  echo "  Other networks cannot reach the app here: the gateway address stands for the host side only."
fi
echo "  A container on the SAME network always arrives with its own address ($same_net here): naming the whole subnet"
echo "  would believe every container you ever attach to it."

[ "$STRICT" = 1 ] && [ "$risky" = 1 ] && exit 1
exit 0
