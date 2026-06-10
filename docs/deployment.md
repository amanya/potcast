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

## NAS Deployment From GHCR

The repository publishes Docker images to GitHub Container Registry at:

```text
ghcr.io/amanya/potcast
```

The image is built for `linux/amd64` and `linux/arm64`, which covers typical Intel NAS
hosts and Raspberry Pi or ARM home-server hosts. The workflow publishes:

- `latest` from the `main` branch.
- The branch name for branch builds.
- Git tag versions such as `v0.1.0`, `0.1.0`, and `0.1`.

On the NAS, create a deployment directory containing:

```text
compose.yaml
potcast.yaml
```

Use `compose.nas.yaml` from this repository as the starting `compose.yaml`.

Before starting the stack:

- Copy `examples/potcast.yaml` to `potcast.yaml`.
- Replace the example feed URLs with real podcast RSS feeds.
- Change every `change-me` Icecast password in both `compose.yaml` and `potcast.yaml`.
- Set `outputs.icecast.public_url` to the NAS listener URL, for example
  `http://nas.local:8000/potcast.mp3`.
- Set `ICECAST_HOSTNAME` to the NAS hostname or IP address.

Start or update the stack:

```bash
docker compose pull
docker compose up -d
```

Check it:

```bash
curl http://nas.local:8080/health
curl http://nas.local:8080/status
curl http://nas.local:8080/feeds/refresh
```

To pin a specific release instead of tracking `latest`, change the image tag:

```yaml
image: ghcr.io/amanya/potcast:v0.1.0
```

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

- `state.json`: station status, active channel, active podcast, volume, previous podcast
  history, and the last playback supervisor output error when playback is blocked.
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
current episode process exits with code `0`. Paused and stopped stations do not
auto-advance.

If the backend cannot start, `/status` reports `output.state: "error"` with
`backend_start_failed`. If the process exits unexpectedly with a non-zero code, `/status`
reports `backend_process_failed`. In both cases the station is left idle, and `/status`
also reports the same structured `last_error`, persisted in `state.json`. This prevents
repeated relaunches every supervisor tick and keeps the last stop reason visible after a
restart. The runtime schedules one automatic retry after a short delay and exposes
`playback_supervisor.state: "retry_scheduled"`, `next_retry_at`, `retry_attempts`, and
`max_retry_attempts` in `/status`. If the retry policy has no attempts left,
`playback_supervisor.state` becomes `"exhausted"`; after a process restart with only the
persisted error available, it is `"blocked"` until an operator command retries or stops
the station.
The same recovery path writes structured log records for the failure, scheduled retry,
retry attempt, retry success, retry exhaustion, and manual recovery actions. Default CLI
logging prints readable messages; collectors can use record fields such as `error_code`,
`podcast_id`, `episode_identity`, `retry_attempt`, and `next_retry_at`.
After fixing the operator-visible cause, such as a missing command or unreachable output
target, call `GET /output/recover` to clear the backend or persisted supervisor error and
retry the currently selected episode immediately. If that immediate retry fails, Potcast
returns a structured `output_recovery_failed` command error and leaves
`playback_supervisor.state` as `"blocked"` without scheduling another automatic retry.
Fix the visible cause and issue another playback or recovery command when ready.

Implemented command endpoints:

- `GET /play`, `/pause`, `/toggle`, `/stop`, `/next`, `/previous`
- `GET /channel/next`, `/channel/previous`, `/channel/<channel_id>`
- `GET /podcast/next`, `/podcast/previous`, `/podcast/<podcast_id>`
- `GET /output/recover`
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
