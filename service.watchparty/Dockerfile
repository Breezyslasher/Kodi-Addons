# Kodi Watch Party standalone relay
#
#   docker build -t kodi-watchparty-relay .
#   docker run -d --restart unless-stopped -p 8765:8765 kodi-watchparty-relay
#
# Fixed rooms instead of open mode:
#   docker run -d -p 8765:8765 -e WATCHPARTY_ROOMS=MOVIE,SHOWS kodi-watchparty-relay
FROM python:3.12-alpine

WORKDIR /app
COPY relay_standalone.py .
COPY resources/lib/relay.py resources/lib/

# room playback state survives container restarts (mount /data to also
# survive container re-creation)
RUN mkdir -p /data && chown nobody /data
ENV WATCHPARTY_STATE=/data/state.json

EXPOSE 8765
USER nobody

HEALTHCHECK --interval=30s --timeout=5s \
  CMD wget -qO- http://127.0.0.1:8765/ping || exit 1

ENTRYPOINT ["python3", "relay_standalone.py"]
