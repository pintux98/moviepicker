# Moviepicker

Pick a movie together. Everyone joins a room from their phone, picks genres and swipes yes or no on movies. Matches go into a quick head-to-head tournament, and the winner is tonight's movie.

**Try it: [moviepicker.pintux.org](https://moviepicker.pintux.org)**

- 2 to 10 players, no accounts: a 6-character room code (or QR code) and a nickname
- English-language feature films from [TMDB](https://www.themoviedb.org/), with a "where to stream" hint for Romania
- Python standard library only, no database, about 20 MB of RAM

## What you need

A TMDB API Read Access Token (free): create an account at themoviedb.org, then Settings → API → "API Read Access Token".

| Variable | Required | Default |
|---|---|---|
| `TMDB_TOKEN` | yes | |
| `PORT` | no | `8000` |

## Run with Coolify

1. **+ New → Public Repository** and paste this repo's URL, branch `main`.
2. Build pack: **Dockerfile**. Ports exposes: `8000`.
3. Set your domain, for example `https://movies.example.com`.
4. **Environment Variables**: add `TMDB_TOKEN` (tick "Is Literal").
5. **Deploy**. Coolify uses the health check from the Dockerfile.

## Run with Docker

```sh
docker build -t moviepicker .
docker run -d --name moviepicker --restart unless-stopped \
  -e TMDB_TOKEN=your_token -p 8000:8000 moviepicker
```

Or with Docker Compose:

```yaml
services:
  moviepicker:
    build: .
    restart: unless-stopped
    ports: ["8000:8000"]
    environment:
      TMDB_TOKEN: ${TMDB_TOKEN}
```

## Run without Docker

Python 3.12 or newer:

```sh
TMDB_TOKEN=your_token python server.py
```

Open http://localhost:8000.

## Behind a reverse proxy

Rate limits (rooms per visitor, wrong room codes) are counted per visitor IP, read from the `CF-Connecting-IP` header and falling back to the connection address.

- **Behind Cloudflare:** works as is.
- **Behind another proxy (nginx, Traefik, Caddy):** have the proxy set `CF-Connecting-IP` to the real client IP and drop any value the client sends. Otherwise every visitor shares one limit.

Rooms live in memory, so a restart closes any open rooms.

## Stats

The home page shows the most picked and the latest picked movies.

- Each finished room adds one pick to its winner, at most once per visitor per 24 hours.
- Each decided card adds its 👍 and 👎 votes.
- Only movie data and counts are stored: no names, rooms or IP addresses.

Stats live in SQLite at `$DATA_DIR/stats.db` (`/data` in the container). Mount a volume there to keep them across deploys (in Coolify: **Persistent Storage**, destination `/data`). If the folder isn't writable, the app keeps running and stats stay in memory.

## Tests

```sh
python -m unittest discover -s tests
```

## License

MIT. Movie data and images come from TMDB: this product uses the TMDB API but is not endorsed or certified by TMDB. QR codes are made with [qrcode-generator](https://github.com/kazuhikoarase/qrcode-generator) (MIT).
