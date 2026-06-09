# Deployment

Potcast can run as a local Python process, a Docker container, or a Raspberry Pi service.

## Local Process

```bash
python -m pip install -e .
potcast --config examples/potcast.yaml
```

The CLI loads the YAML config, creates storage directories, composes the feed refresh
service, feed scheduler, playback supervisor, station service, output backend, and Flask
app, then serves the HTTP API.

For local development from the repository, install the package with development tools:

```bash
python -m pip install -e ".[dev]"
```

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
- Keep `outputs.icecast.host` as `icecast` when Potcast connects to the Compose service
  on the Docker network.

After startup, check the control API and feed monitor:

```bash
curl http://localhost:8080/health
curl http://localhost:8080/feeds
curl http://localhost:8080/feeds/refresh
```

`/feeds/refresh` returns `202` when a background refresh is accepted. It returns `200`
with `accepted: false` and reason `already_running` when another refresh is still active.

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

For Docker-based local audio, the container generally needs access to the host sound
device, for example:

```yaml
services:
  potcast:
    devices:
      - /dev/snd:/dev/snd
    group_add:
      - audio
```

Exact audio device names depend on the Pi OS image and audio stack. Use an `mpv` device
name that works on the host, then copy that value into `outputs.local_audio.device`.

## Runtime Storage

Potcast writes runtime metadata under `storage.data_dir`:

- `state.json`: station status, active channel, active podcast, volume, and previous
  podcast history.
- `feeds.json`: feed URL, feed title, latest episode metadata, entry counts, last check
  time, status, and structured feed errors.
- `downloads.json`: downloaded episode identity, media URL and type, local file path,
  download time, status, and title.

Episode media is stored below `storage.episodes_dir` in per-podcast directories. New
downloads are written to a temporary file, validated, moved into place, and only then is
the previous local episode removed.

## Runtime API

Startup should serve:

- `GET /health`
- `GET /status`

Commands are intentionally HTTP GET endpoints for first-version integration with phone
shortcuts, browsers, home automation, and simple media controls.

While the station is playing, Potcast supervises the active `ffmpeg` or `mpv` process and
automatically advances to the next playable podcast in the active channel when the
current episode process exits. Paused and stopped stations do not auto-advance.

Implemented command endpoints:

- `GET /play`, `/pause`, `/toggle`, `/stop`, `/next`, `/previous`
- `GET /channel/next`, `/channel/previous`, `/channel/<channel_id>`
- `GET /podcast/next`, `/podcast/previous`, `/podcast/<podcast_id>`
- `GET /volume`, `/volume/<level>`, `/volume/up`, `/volume/down`
- `GET /feeds`, `/feeds/refresh`

All responses are JSON. Structured command errors use this shape:

```json
{
  "ok": false,
  "error": {
    "code": "invalid_volume",
    "message": "Volume must be between 0 and 100."
  }
}
```

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
