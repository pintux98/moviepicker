FROM python:3.13-alpine
WORKDIR /app
COPY game.py tmdb.py server.py ./
COPY static ./static
ENV PYTHONUNBUFFERED=1 PORT=8000
USER nobody
EXPOSE 8000
HEALTHCHECK --interval=30s --timeout=5s CMD wget -qO- http://127.0.0.1:8000/ >/dev/null || exit 1
CMD ["python", "server.py"]
