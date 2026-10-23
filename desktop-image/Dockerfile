FROM docker.io/library/python:3.12-slim-bookworm@sha256:34386ef0cb081344d7ec1c103ba398e6e9f64e9ab3a1509accc92a4e24a07258
RUN apt-get update && apt-get install -y --no-install-recommends tigervnc-standalone-server openbox xdotool maim socat x11-utils xterm firefox-esr fonts-dejavu-core libnss-wrapper imagemagick && dpkg-query -W > /usr/share/open-dots-packages.txt && rm -rf /var/lib/apt/lists/* && mkdir -p /home/agent && chmod 1777 /home/agent
COPY od-desktop od-ready od-shot /usr/local/bin/
RUN chmod 755 /usr/local/bin/od-*
ENV HOME=/home/agent DISPLAY=:1 USER=agent
WORKDIR /workspace
CMD ["od-desktop"]
