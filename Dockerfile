# python:3.13-alpine, pinned by digest (bump on purpose: docker pull python:3.13-alpine, copy the new digest)
FROM python:3.13-alpine@sha256:1e1f6334529d1fb26a2ef619d7cffb30a7c7d7cc1cc7338ebee283a58d1aeaca
WORKDIR /app
COPY game.py tmdb.py server.py ./
COPY static ./static
ENV PYTHONUNBUFFERED=1 PORT=8000
USER nobody
EXPOSE 8000
HEALTHCHECK --interval=30s --timeout=5s CMD wget -qO- http://127.0.0.1:8000/ >/dev/null || exit 1
CMD ["python", "server.py"]
