# Potcast

Potcast is a personal podcast radio service. It monitors configured podcast RSS feeds,
keeps the latest playable episode for each podcast, groups podcasts into channels, and
lets simple HTTP commands control playback through Icecast or Raspberry Pi local audio.

The current service runs one configured primary output backend. Feed refreshes keep local
episode files up to date; station commands select the active channel or podcast and send
the selected local episode to the output backend. While the station is playing, Potcast
also watches the active output process and advances to the next playable podcast when an
episode finishes normally. Backend startup failures and unexpected output process exits
are surfaced in `/status` as structured output and playback supervisor errors, and the
station is left idle instead of immediately relaunching the same failing episode.
Operators can use `GET /output/recover` to clear an output error and retry the currently
selected episode once.

## Quick Start

Create a config from `examples/potcast.yaml`, replace the example feed URLs, and run:

```bash
python -m pip install -e ".[dev]"
potcast --config examples/potcast.yaml
```

Use `--log-level DEBUG` when you want more startup and command logging.

The HTTP server exposes:

- `GET /health` for process health and version.
- `GET /status` for station, output, and feed monitor state.
- `GET /play`, `/pause`, `/toggle`, `/stop`, `/next`, and `/previous`.
- `GET /channel/next`, `/channel/previous`, and `/channel/<channel_id>`.
- `GET /podcast/next`, `/podcast/previous`, and `/podcast/<podcast_id>`.
- `GET /output/recover` to retry the selected episode after an output error.
- `GET /volume`, `/volume/<level>`, `/volume/up`, and `/volume/down`.
- `GET /feeds` and `/feeds/refresh` for feed status and manual refresh.

Errors are JSON objects such as:

```json
{
  "ok": false,
  "error": {
    "code": "unknown_channel",
    "message": "Channel not found: bedtime"
  }
}
```

## Docker

For an Icecast-backed local run:

```bash
docker compose up --build
```

Potcast listens on `http://localhost:8080`. The Icecast stream example is
`http://localhost:8000/potcast.mp3` after you replace the sample feed URLs and episodes
have downloaded.

## Configuration

Potcast uses one YAML file. The most important sections are `channels`, `outputs`,
`storage`, `station`, `feeds`, and `server`. See `docs/configuration.md` for the complete
reference.

Each channel has a stable `id`, a display `name`, and one or more podcasts. Podcast IDs
must be unique across the whole config in this first version.

## Deployment

See `docs/deployment.md` for Docker Compose, Icecast, and Raspberry Pi local audio notes.
See `docs/architecture.md` for component boundaries and testing strategy.

## Development

Install development dependencies:

```bash
python -m pip install -e ".[dev]"
```

Run checks:

```bash
pytest
ruff check .
ruff format --check .
mypy potcast
```

The default suite does not require internet access, real podcast feeds, Icecast, audio
devices, `ffmpeg`, `mpv`, or long sleeps.
