# Deployment

Potcast can run as a local Python process, a Docker container, or a Raspberry Pi service.

## Local Process

```bash
python -m pip install -e .
potcast --config examples/potcast.yaml
```

The CLI loads the YAML config, creates storage directories, composes the feed refresh
service, scheduler, station service, output backend, and Flask app, then serves the HTTP
API.

Useful checks:

```bash
curl http://localhost:8080/health
curl http://localhost:8080/status
```

## Docker Compose With Icecast

The repository includes `compose.yaml` and `Dockerfile`.

```bash
docker compose up --build
```

The Compose example starts:

- `potcast` on `http://localhost:8080`.
- `icecast` on `http://localhost:8000`.
- A named `potcast-data` volume mounted at `/data`.
- `examples/potcast.yaml` mounted read-only at `/config/potcast.yaml`.

Before using it as a real station:

- Replace the placeholder feed URLs in `examples/potcast.yaml`.
- Change all `change-me` Icecast passwords in both `compose.yaml` and the config.
- Set `outputs.icecast.public_url` to the URL listeners should use.

## Docker Image

Build directly:

```bash
docker build -t potcast .
```

Run with mounted config and data:

```bash
docker run --rm \
  -p 8080:8080 \
  -v "$PWD/examples/potcast.yaml:/config/potcast.yaml:ro" \
  -v "$PWD/data:/data" \
  potcast
```

The image includes `ffmpeg` for Icecast output and `mpv` for local audio output.

## Raspberry Pi Local Audio

For host audio, configure:

```yaml
outputs:
  primary: "local_audio"
  enabled:
    - "local_audio"
  local_audio:
    enabled: true
    player: "mpv"
    device: "default"
    mixer: "software"
```

Install runtime tools on the Pi:

```bash
sudo apt-get update
sudo apt-get install -y python3-venv ffmpeg mpv
```

Create a virtual environment, install Potcast, and run:

```bash
potcast --config /etc/potcast.yaml
```

An optional systemd unit is provided at `deploy/systemd/potcast.service`. Adjust the
`User`, `Group`, `WorkingDirectory`, and `ExecStart` paths before installing it.

## Runtime API

Startup should serve:

- `GET /health`
- `GET /status`

Commands are intentionally HTTP GET endpoints for first-version integration with phone
shortcuts, browsers, home automation, and simple media controls.

## Quality Commands

Before merging changes, run:

```bash
pytest
ruff check .
ruff format --check .
mypy potcast
```

The default test suite must not require real network access, live podcast feeds, Icecast,
audio devices, `ffmpeg`, `mpv`, or long sleeps.
