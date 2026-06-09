# Potcast

Potcast is a personal podcast radio service. It monitors configured podcast RSS feeds,
keeps the latest playable episode for each podcast, and runs a continuous station through
outputs such as Icecast or Raspberry Pi local audio.

## Quick Start

Create a config from `examples/potcast.yaml`, replace the example feed URLs, and run:

```bash
python -m pip install -e .
potcast --config examples/potcast.yaml
```

The HTTP server exposes:

- `GET /health` for process health and version.
- `GET /status` for station, output, and feed monitor state.
- `GET /play`, `/pause`, `/stop`, `/next`, and related command endpoints.
- `GET /feeds` and `/feeds/refresh` for feed monitor status and manual refresh.

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
