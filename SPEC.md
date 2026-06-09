# Potcast Service Specification

## 1. Purpose

Potcast is a lightweight personal podcast radio service designed to run unattended on a
NAS, home server, or Raspberry Pi and deliver podcast audio to a configured output.

The service maintains a curated set of podcast feeds grouped into user-defined channels.
It monitors those feeds, keeps the latest available episode for each podcast, sends the
selected episode from the active channel to the output backend, and exposes simple HTTP
commands for station control.

## 2. Goals

- Run as a Dockerized HTTP service on a NAS or home server.
- Also support running on a Raspberry Pi with local audio output.
- Use Python, with Flask or another small Python HTTP framework.
- Store all user configuration in a single YAML file.
- Organize podcasts into channels.
- Continuously monitor podcast RSS feeds.
- Download the newest episode for each configured podcast.
- Replace the previously downloaded episode from the same podcast when a newer one appears.
- Deliver selected podcast audio through a configured output backend.
- Support Icecast-compatible streaming.
- Support Raspberry Pi local audio output.
- Keep the stream output architecture extensible for future targets such as AirPlay, Chromecast, Bluetooth, or Home Assistant media players.
- Provide HTTP GET endpoints for basic remote control commands.
- Work well with a simple phone shortcut, home automation trigger, browser request, internet radio client, or media player.

## 3. Non-Goals

- Potcast is not a full podcast library manager.
- It does not need user accounts, authentication, ratings, search, subscriptions, or playback history in the first version.
- It does not need a visual web UI in the first version.
- It does not need to store every historical episode of a podcast.
- It does not need cloud synchronization.
- It does not need AirPlay, Chromecast, or Bluetooth output in the first version.

## 4. Runtime Environment

Target environment:

- NAS, home server, mini PC, Raspberry Pi, or any Docker-capable Linux host.
- Python 3.10 or newer.
- Local filesystem storage for configuration, metadata, and downloaded audio.
- HTTP control server bound to the local network, usually `0.0.0.0`.
- Output through Icecast, local audio, or another configured backend.

Recommended deployment:

- Run with Docker Compose for NAS or home-server deployments.
- Run as a native service or Docker container on Raspberry Pi deployments.
- Restart automatically on failure.
- Keep downloaded media and runtime metadata under a dedicated data directory.
- Expose one HTTP port for Potcast control.
- Expose an additional HTTP port when an Icecast stream is enabled.

## 5. Configuration

All user configuration is stored in one YAML file, for example:

```yaml
server:
  host: "0.0.0.0"
  port: 8080

storage:
  data_dir: "/data"
  episodes_dir: "/data/episodes"

station:
  start_on_boot: true
  shuffle_podcasts: true
  shuffle_channels: false
  volume: 70
  sleep_timer_minutes: null

outputs:
  primary: "icecast"
  enabled:
    - "icecast"
  icecast:
    enabled: true
    host: "icecast"
    port: 8000
    source_password: "change-me"
    mount: "/potcast.mp3"
    public_url: "http://nas.local:8000/potcast.mp3"
    name: "Potcast"
    description: "Personal podcast radio"
    genre: "Podcast"
    format: "mp3"
    bitrate_kbps: 128
    sample_rate_hz: 44100
  local_audio:
    enabled: false
    player: "mpv"
    device: "default"
    mixer: "software"

feeds:
  refresh_interval_minutes: 30
  download_timeout_seconds: 60
  user_agent: "Potcast/1.0"

channels:
  - id: "sleep"
    name: "Sleep"
    podcasts:
      - id: "history-extra"
        name: "History Extra"
        feed_url: "https://example.com/history-extra/rss"
      - id: "science-hour"
        name: "Science Hour"
        feed_url: "https://example.com/science-hour/rss"

  - id: "stories"
    name: "Stories"
    podcasts:
      - id: "short-fiction"
        name: "Short Fiction"
        feed_url: "https://example.com/short-fiction/rss"
```

### 5.1 Required Fields

- `channels`: list of configured station channels.
- `channels[].id`: stable unique channel identifier.
- `channels[].name`: human-readable channel name.
- `channels[].podcasts`: list of podcasts in the channel.
- `channels[].podcasts[].id`: stable unique podcast identifier.
- `channels[].podcasts[].name`: human-readable podcast name.
- `channels[].podcasts[].feed_url`: RSS feed URL.
- `outputs.primary`: primary output backend.

### 5.2 Optional Fields

- `server.host`: HTTP bind address. Default: `0.0.0.0`.
- `server.port`: HTTP port. Default: `8080`.
- `storage.data_dir`: directory for metadata and state. Default: `./data`.
- `storage.episodes_dir`: directory for downloaded episodes. Default: `<data_dir>/episodes`.
- `station.start_on_boot`: start broadcasting when the service starts. Default: `false`.
- `station.shuffle_podcasts`: randomize podcast order within a channel. Default: `true`.
- `station.shuffle_channels`: reserved for future randomized channel order. Default: `false`.
- `station.volume`: station gain from 0 to 100. Default: `100`.
- `station.sleep_timer_minutes`: optional automatic pause timer. Default: `null`.
- `outputs.icecast.enabled`: enable Icecast output. Default: `true`.
- `outputs.icecast.host`: Icecast service host. Default: `icecast`.
- `outputs.icecast.port`: Icecast service port. Default: `8000`.
- `outputs.icecast.source_password`: Icecast source password.
- `outputs.icecast.mount`: stream mount path. Default: `/potcast.mp3`.
- `outputs.icecast.public_url`: URL clients should use to listen.
- `outputs.icecast.format`: encoded stream format. Default: `mp3`.
- `outputs.icecast.bitrate_kbps`: stream bitrate. Default: `128`.
- `outputs.icecast.sample_rate_hz`: stream sample rate. Default: `44100`.
- `outputs.local_audio.enabled`: enable local sound output. Default: `false`.
- `outputs.local_audio.player`: local playback command. Default: `mpv`.
- `outputs.local_audio.device`: audio device name. Default: `default`.
- `outputs.local_audio.mixer`: volume strategy. Default: `software`.
- `feeds.refresh_interval_minutes`: feed polling interval. Default: `30`.
- `feeds.download_timeout_seconds`: media download timeout. Default: `60`.
- `feeds.user_agent`: HTTP user agent for feed and media requests. Default: `Potcast/<version>`.

## 6. Data Model

Potcast maintains runtime state separately from the YAML configuration.

Suggested state files:

- `state.json`: current channel, current podcast, station status, last known positions if needed.
- `feeds.json`: latest known episode metadata per podcast.
- `downloads.json`: local media file path and download status per podcast.
- `outputs.json`: output backend status and last connection errors.

The YAML file remains the source of truth for user intent. Runtime metadata can be regenerated from feeds and downloads if needed.

Phase 3 persists these files as UTF-8 JSON objects in `storage.data_dir`:

- `state.json`: `station_status`, `current_channel_id`, `current_podcast_id`, `volume`, `previous_podcast_ids`, and optional `playback_supervisor_error`.
- `feeds.json`: keyed by podcast ID, with feed URL, feed title, latest episode metadata, entry counts, last check timestamp, status, and optional structured error fields.
- `downloads.json`: keyed by podcast ID, with episode identity, media URL/type, local file path, download timestamp, status, and title.

Phase 5 adds a structured in-memory output status model used by output backends:

- `backend`: output backend ID, such as `icecast` or `local_audio`.
- `state`: `stopped`, `idle`, `playing`, `paused`, or `error`.
- `connected`: whether the backend currently has an active output process or connection.
- `current_episode_identity`: the episode identity currently assigned to the backend, when any.
- `volume`: backend output volume from 0 to 100.
- `error`: optional structured error with `code` and `message`.

Phase 6 adds an application station service that coordinates persisted runtime state,
downloaded episode metadata, station selection, and the configured output backend. The
service exposes typed command results with `ok`, optional structured command errors, and
a status object containing:

- `station_state`: `stopped`, `idle`, `playing`, or `paused`.
- `active_channel`: the configured active channel, when any.
- `active_podcast`: the configured active podcast, when any.
- `active_episode`: the downloaded episode currently selected for playback, when any.
- `volume`: persisted station volume from 0 to 100.
- `output`: the current output backend status.
- `playback_supervisor`: lightweight supervisor state with `state` and optional
  structured `last_error`.

Phase 8 adds an in-memory feed monitor status used by `/status`, `/feeds`, and
`/feeds/refresh`:

- `running`: whether a refresh is currently active.
- `last_started_at`: when the latest accepted refresh began.
- `last_finished_at`: when the latest refresh finished.
- `last_result`: the latest refresh outcome, such as `ok` or `failed`.
- `last_error`: optional structured monitor-level error.
- `next_refresh_at`: next scheduled refresh time, when provided by the scheduler.

Downloaded episode media is stored below `storage.episodes_dir` in a per-podcast directory. Final filenames are stable and filesystem-safe, derived from the podcast ID, a hash of the episode identity, and the media extension.

### 6.1 Channel

A channel is an ordered group of podcasts.

Fields:

- `id`
- `name`
- `podcasts`

### 6.2 Podcast

A podcast belongs to one or more channels by configuration. In the first version, duplicate podcast IDs across channels should be rejected to keep state simple.

Fields:

- `id`
- `name`
- `feed_url`
- `latest_episode`
- `local_file`
- `last_checked_at`
- `last_downloaded_at`
- `status`

### 6.3 Episode

Fields:

- `title`
- `guid`
- `published_at`
- `media_url`
- `media_type`
- `duration`
- `local_file`
- `downloaded_at`

Episode identity should prefer RSS `guid`. If `guid` is missing, use the enclosure URL.

## 7. Feed Monitoring

Potcast runs a background feed monitor.

Behavior:

1. Load configured podcasts from YAML.
2. Poll each podcast RSS feed on the configured interval.
3. Parse the feed.
4. Select the newest playable episode.
5. Compare it with the currently known episode for that podcast.
6. If the episode is new, download its audio file.
7. Verify that the downloaded file exists and has non-zero size.
8. Replace the previous local episode file for that podcast.
9. Update runtime metadata.

The replacement should be atomic:

- Download to a temporary file.
- Validate the temporary file.
- Move the file into its final location.
- Remove the previous episode file only after the new file is ready.

### 7.1 Feed Failures

Feed or download failures should not stop the station.

On failure:

- Log the error.
- Mark the podcast as stale or failed in runtime metadata.
- Keep the previous downloaded episode if available.
- Retry on the next refresh cycle.

### 7.2 Supported Media

The first version should support common podcast audio enclosures:

- `audio/mpeg`
- `audio/mp3`
- `audio/mp4`
- `audio/x-m4a`
- `audio/aac`
- `audio/ogg`

Unsupported media should be skipped.

## 8. Station and Outputs

Potcast selects playable downloads from the currently selected channel and sends the
selected local episode to the configured output backend. The packaged implementation
supervises the active output process and advances to the next playable podcast in the
active channel when the current episode process exits normally.

Station sequence:

1. Select active channel.
2. Select active podcast within that channel.
3. Send the downloaded latest episode for that podcast to the configured output.
4. HTTP commands can advance to another podcast in the same channel or switch channels.
5. Output supervision advances automatically when an episode finishes normally while the
   station is playing.

If a podcast does not have a downloaded episode, Potcast should skip it.

If no podcasts in the current channel have playable downloads, Potcast remains idle and
retries when downloads become available. For Icecast, keeping the source connection alive
with silence is preferred future behavior; the current simple backend stops the process
when there is no selected local episode.

Paused and stopped stations do not auto-advance, even if a previously active output
process has ended.

Backend startup failures and unexpected non-zero output process exits are not treated as
episode completion. They update output status with structured errors such as
`backend_start_failed` or `backend_process_failed`, disconnect the backend, and leave the
station idle so the playback supervisor does not repeatedly relaunch the same failing
episode in a tight loop. A later manual command such as `/output/recover`, `/play`,
`/next`, or channel or podcast selection may retry playback.

### 8.1 Output Architecture

Potcast should separate station control from output delivery.

Core responsibilities:

- Feed monitoring.
- Download management.
- Channel and podcast selection.
- Station state.
- HTTP commands.

Output backend responsibilities:

- Convert or play the selected local audio file.
- Deliver audio to the configured destination.
- Report output health and errors.
- Support start, stop, pause, next item, and metadata updates.

The first output backend interface supports `start`, `pause`, `stop`, `play_episode`,
`set_volume`, `status`, and a consumed playback-event check used by the station
supervisor. Subprocess-backed implementations construct commands through injectable
launchers so tests can assert command construction and distinguish normal completion
from failed process exits without launching real external programs.

Supported first-version output backends:

- `icecast`: publishes a network stream to an Icecast server.
- `local_audio`: plays through the host machine's local audio device, useful for Raspberry Pi internal audio or a directly connected speaker.

Future backends may include `airplay`, `chromecast`, `bluetooth`, or `home_assistant`.

### 8.2 Icecast Output

The Icecast output publishes encoded audio to an Icecast server.

Phase 5 provides direct `ffmpeg` command construction for streaming one selected local episode to Icecast. The backend applies configured host, port, source password, mount, stream metadata, format, bitrate, sample rate, and software volume. Process launching is injected so command construction remains testable without Icecast or `ffmpeg`.

Recommended first-version implementation options:

- Potcast controls `ffmpeg` directly and streams to Icecast.
- Potcast generates a playlist or source description consumed by Liquidsoap.
- Potcast delegates most audio continuity logic to Liquidsoap.

Liquidsoap is the preferred long-term streaming engine because it is designed for programmable radio stations, fallbacks, metadata, playlists, silence generation, and resilient Icecast output.

For the MVP, either of these approaches is acceptable:

- **Simple backend:** Python controls `ffmpeg` processes directly.
- **Radio backend:** Python controls station state while Liquidsoap handles continuous streaming.

The implementation should keep the backend interface stable enough that the simple backend can later be replaced with Liquidsoap without changing the HTTP API or YAML channel configuration.

### 8.3 Local Audio Output

The local audio output plays audio through the host machine's default audio device or a configured device.

This backend is useful when Potcast runs on a Raspberry Pi connected directly to speakers, headphones, HDMI audio, or the Pi's internal analog audio output.

Recommended implementation:

- Use `mpv` as the first local audio player backend.
- Keep local audio behind the same output interface as Icecast.
- Apply station volume through software gain when possible.
- Allow the configured audio device to be passed to the player.

Phase 5 provides `mpv` command construction using the configured player command, audio device, mixer strategy, and software volume. Process launching is injected so tests do not require `mpv` or audio hardware.

For Docker-based Raspberry Pi deployments, the container may need access to the host audio device, for example `/dev/snd`, and membership in the appropriate audio group. A native service is acceptable for Raspberry Pi local-audio deployments if that is simpler and more reliable.

### 8.4 Listener Experience

Listeners connect to the configured stream URL, for example:

```text
http://nas.local:8000/potcast.mp3
```

Clients may include:

- VLC.
- Browser audio tab.
- Mobile podcast or radio app.
- Home Assistant media player.
- Smart speaker that can play an HTTP stream.
- Future AirPlay or Chromecast output bridge.

HTTP commands change the station source. Connected listeners should continue listening to the same stream URL.

For local audio output, there may be no listener URL. HTTP commands still control the same station state, but the audio is heard through the configured device.

### 8.5 Selection Rules

When `shuffle_podcasts` is `false`:

- `next_podcast` advances to the next configured podcast in the current channel.
- `previous_podcast` moves to the previous configured podcast.

When `shuffle_podcasts` is `true`:

- Potcast should avoid repeating the same podcast until every playable podcast in the channel has been selected once, where possible.
- `previous_podcast` may use recent playback history.

When `shuffle_channels` is `false`:

- `next_channel` and `previous_channel` follow YAML order.

`shuffle_channels` is accepted in configuration but reserved in the current
implementation. Channel next and previous commands currently follow YAML order.

## 9. HTTP API

The HTTP API is intentionally simple and command-oriented.

All first-version control endpoints use HTTP GET so they can be triggered easily from browsers, bookmarks, shortcuts, NFC tags, or home automation systems.

All responses should be JSON.

### 9.1 Health and Status

`GET /health`

Returns service health.

```json
{
  "ok": true,
  "version": "0.1.0"
}
```

`GET /status`

Returns current station and output state. When a feed monitor is configured, the response
also includes top-level `feed_monitor` timing and status fields.

```json
{
  "ok": true,
  "status": {
    "station_state": "playing",
    "active_channel": {
      "id": "sleep",
      "name": "Sleep",
      "podcasts": []
    },
    "active_podcast": {
      "id": "history-extra",
      "name": "History Extra",
      "feed_url": "https://example.com/history-extra/rss"
    },
    "active_episode": {
      "title": "Episode title",
      "identity": "episode-guid",
      "guid": "episode-guid",
      "published_at": "2026-06-08T10:00:00+00:00",
      "media_url": "https://example.com/history-extra.mp3",
      "media_type": "audio/mpeg",
      "duration": null,
      "local_file": "/data/episodes/history-extra/latest.mp3",
      "downloaded_at": "2026-06-08T20:15:00+00:00"
    },
    "volume": 70,
    "output": {
      "backend": "icecast",
      "state": "playing",
      "connected": true,
      "current_episode_identity": "episode-guid",
      "volume": 70,
      "error": null
    },
    "playback_supervisor": {
      "state": "watching",
      "last_error": null
    }
  }
}
```

When output playback fails, `output.state` is `error`, `connected` is `false`, and
`output.error` contains a structured code and message. Startup failures use
`backend_start_failed`; unexpected subprocess exits use `backend_process_failed`. The
station also persists the same structured error in `state.json` and reports
`playback_supervisor.state` as `blocked` with `last_error` set until a later successful
playback attempt or explicit station stop clears it. This makes the last supervisor stop
reason visible even if the backend object has been recreated.

### 9.2 Station Commands

`GET /play`

Starts or resumes the station output.

The command is idempotent. If the station is already marked `playing`, it returns the
current status without replaying the same episode through the output backend. If no
downloaded episode is playable in the active channel, the station enters `idle`.

`GET /pause`

Pauses the station output. The current subprocess-backed backends terminate the active
process and report `paused`. A future radio-style Icecast backend may keep the source
connected and broadcast silence.

The command is idempotent. Repeated pause commands leave the station paused without
issuing duplicate backend pause calls.

`GET /toggle`

Toggles between active and paused station output.

`GET /stop`

Stops the station output and releases the output backend connection.

The command is idempotent. Repeated stop commands leave the station stopped without
issuing duplicate backend stop calls.

`GET /next`

Moves to the next podcast in the active channel.

`GET /previous`

Moves to the previous podcast in the active channel.

When `next` or `previous` selects a playable downloaded episode, the station service
updates runtime state and sends that episode to the output backend.

### 9.3 Output Commands

`GET /output/recover`

Clears an output backend error and retries the currently selected episode once. If the
output backend is not in `error` and no persisted playback supervisor error is blocked,
the command is idempotent and returns the current status without replaying the episode.
If no selected podcast has a playable downloaded episode, the station remains `idle`.

### 9.4 Channel Commands

`GET /channel/next`

Switches to the next channel and starts or continues streaming from that channel.

`GET /channel/previous`

Switches to the previous channel and starts or continues streaming from that channel.

`GET /channel/<channel_id>`

Switches to a specific channel by ID.

Unknown channels return a structured `unknown_channel` command error. Known channel
changes select the first playable downloaded podcast in that channel, if one exists,
and send it to the output backend.

### 9.5 Podcast Commands

`GET /podcast/next`

Alias for `GET /next`.

`GET /podcast/previous`

Alias for `GET /previous`.

`GET /podcast/<podcast_id>`

Switches to a specific podcast by ID if it belongs to the active channel.

Unknown podcasts in the active channel return a structured `unknown_podcast` command
error. Podcasts without a playable downloaded episode return a structured
`podcast_unavailable` command error.

### 9.6 Volume Commands

`GET /volume`

Returns current station gain and station status.

`GET /volume/<level>`

Sets station gain to an integer from 0 to 100.

Invalid levels return a structured `invalid_volume` command error. Valid levels update
persisted runtime state and the output backend volume.

`GET /volume/up`

Raises volume by a configured step, defaulting to 5.

`GET /volume/down`

Lowers volume by a configured step, defaulting to 5.

### 9.7 Feed Commands

`GET /feeds/refresh`

Starts an immediate feed refresh in the background.

The request should return quickly and not wait for all downloads to complete.
If another refresh is already running, Potcast rejects the overlapping trigger and returns
`accepted: false` with reason `already_running`; the already-running refresh continues.

`GET /feeds`

Returns configured feeds and their current status.

### 9.8 Output Status

The current API exposes output status inside `GET /status` and output recovery through
`GET /output/recover`. Dedicated `/stream` and `/outputs` endpoints are not implemented
yet. Future output-specific endpoints may be added for listener URLs, AirPlay,
Chromecast, Bluetooth, local audio device selection, or Home Assistant targets.

### 9.9 Response Format

Successful command response:

```json
{
  "ok": true,
  "command": "next",
  "status": {
    "station_state": "playing",
    "active_channel": {},
    "active_podcast": {},
    "active_episode": {},
    "volume": 70,
    "output": {}
  }
}
```

Error response:

```json
{
  "ok": false,
  "error": {
    "code": "unknown_channel",
    "message": "Channel not found: bedtime"
  }
}
```

Suggested HTTP status codes:

- `200`: command accepted or status returned.
- `400`: invalid command parameter.
- `404`: unknown channel, podcast, or endpoint.
- `409`: command cannot be performed in current state.
- `500`: unexpected service error.

## 10. Concurrency

Potcast has at least three concurrent responsibilities:

- HTTP request handling.
- Feed monitoring and downloads.
- Station queue supervision.
- Output backend supervision.

The first version can use Python threads:

- Main thread: Flask app.
- Feed monitor thread: periodic RSS checks.
- Future station supervision: advances selected podcast when an episode ends.
- Output subprocesses: `ffmpeg` for Icecast or `mpv` for local audio.

Shared state must be protected with a lock.

Commands should be idempotent where reasonable. For example, calling `/play` while already playing should return success.

## 11. Logging

Potcast should log:

- Service startup and shutdown.
- Configuration load result.
- Feed refresh start and finish.
- Feed parsing errors.
- Episode downloads.
- Episode replacement.
- Station start, pause, stop, and track changes.
- Output connection status.
- Stream encoder failures.
- Local audio player failures.
- HTTP command errors.

Logs should be written to stdout/stderr so Docker can capture them.

## 12. Security

The first version is intended for trusted local networks.

Defaults:

- No authentication.
- Bind to `0.0.0.0` only if configured or accepted by default deployment.
- Document that users should not expose Potcast directly to the public internet.

Future optional security:

- Shared secret token.
- Bind to localhost only.
- Reverse proxy authentication.

## 13. Installation and Operation

Suggested Docker Compose setup:

```yaml
services:
  potcast:
    build: .
    container_name: potcast
    restart: unless-stopped
    ports:
      - "8080:8080"
    volumes:
      - ./config.yaml:/config/potcast.yaml:ro
      - ./data:/data
    depends_on:
      - icecast

  icecast:
    image: moul/icecast
    container_name: potcast-icecast
    restart: unless-stopped
    ports:
      - "8000:8000"
    environment:
      ICECAST_SOURCE_PASSWORD: change-me
      ICECAST_ADMIN_PASSWORD: change-me
      ICECAST_RELAY_PASSWORD: change-me
```

The exact Icecast image can change, but the compose setup should provide:

- Potcast control API on `http://nas.local:8080`.
- Icecast listener stream on `http://nas.local:8000/potcast.mp3`.
- Persistent data volume for downloads and state.
- Read-only mounted YAML configuration.

For Raspberry Pi local-audio deployments, Potcast can run either natively or in Docker.

Native service:

```bash
python -m venv .venv
. .venv/bin/activate
pip install potcast
potcast --config /etc/potcast.yaml
```

If using Docker with local audio, the container must be allowed to access the host audio device. The exact setup depends on the host audio stack, but a typical deployment may need:

- `/dev/snd` mounted into the container.
- The container user added to the host audio group.
- `mpv` or the chosen local player available in the container.

## 14. Suggested Python Modules

Initial structure:

```text
potcast/
  __init__.py
  app.py              # Flask app and route registration
  cli.py              # Command-line entry point
  config.py           # YAML loading and validation
  feeds.py            # RSS polling and episode selection
  downloader.py       # Atomic media downloads
  station.py          # Channel selection and station control
  outputs/
    __init__.py
    base.py           # Output backend interface
    icecast.py        # Icecast output backend
    local_audio.py    # Raspberry Pi or host local audio backend
    liquidsoap.py     # Optional future Liquidsoap backend
  state.py            # Runtime state persistence and locking
  scheduler.py        # Background feed monitor
  models.py           # Dataclasses or Pydantic models
  errors.py           # Service exceptions and API errors
tests/
  unit/
  integration/
docs/
  configuration.md
  deployment.md
  architecture.md
```

Recommended libraries:

- `flask`
- `pyyaml`
- `feedparser`
- `requests` or `httpx`
- `pydantic` or `dataclasses`

External system dependency:

- `ffmpeg`
- `mpv`, for local audio output
- `icecast` server, usually as a separate container
- `liquidsoap`, optional but recommended for a more robust radio-style backend

## 15. Engineering Standards

Potcast should be built as a small but maintainable service. The design should favor clear boundaries, testable business logic, and explicit contracts between components.

### 15.1 Architecture Principles

- Keep domain logic independent from Flask, subprocesses, and filesystem details where practical.
- Use dependency injection for clocks, HTTP clients, storage, feed parsers, randomization, and output backends.
- Keep output backends behind a common interface.
- Keep feed parsing, episode selection, station selection, and command handling testable without real network, audio, Icecast, or player processes.
- Prefer small modules with focused responsibilities over large coordinator objects.
- Model configuration and runtime data with typed structures.
- Make invalid states difficult to represent.
- Avoid global mutable state except for the application composition root.

### 15.2 Testing Requirements

The project must include automated tests for the most important business logic.

Required unit test coverage:

- YAML configuration loading, defaults, and validation errors.
- Channel and podcast ID uniqueness.
- RSS episode identity selection using `guid` and enclosure fallback.
- Newest playable episode selection.
- Unsupported media filtering.
- Atomic download replacement behavior using temporary files.
- Feed failure behavior that preserves the last good episode.
- Sequential and shuffled podcast selection.
- Previous podcast behavior using playback history.
- Channel switching behavior.
- Station command idempotency for play, pause, stop, next, and previous.
- Output backend interface behavior using fake backends.
- Status response construction.

Required integration tests:

- HTTP API command responses using Flask's test client or equivalent.
- Feed refresh using fixture RSS files and a fake downloader.
- State persistence round trip using a temporary data directory.
- Output backend process command construction without launching real audio processes.

Tests must not require internet access, real Icecast, real audio devices, or long-running sleeps. External dependencies should be faked or run behind explicit opt-in integration tests.

### 15.3 Quality Tooling

The project should include:

- `pytest` for tests.
- `ruff` for linting and formatting.
- `mypy` or `pyright` for static type checking.
- Type annotations for public functions and module boundaries.
- A single command for the default verification suite.

Recommended command:

```bash
pytest
ruff check .
ruff format --check .
mypy potcast
```

### 15.4 Documentation Requirements

The repository should include:

- `README.md`: what Potcast is, quick start, basic configuration, and command examples.
- `SPEC.md`: product and service specification.
- `IMPLEMENTATION_PLAN.md`: phased build plan and acceptance gates.
- `AGENTS.md`: instructions for AI agents and contributors working in the repository.
- `docs/configuration.md`: complete YAML reference.
- `docs/deployment.md`: Docker Compose, Icecast, and Raspberry Pi local-audio deployment notes.
- `docs/architecture.md`: component boundaries, state model, output backend interface, and testing approach.

Documentation should be updated in the same change as behavior, configuration, endpoint, or deployment changes.

### 15.5 Contributor Expectations

Every meaningful code change should:

- Keep changes scoped to the relevant component.
- Include or update tests for affected business logic.
- Preserve existing behavior unless the change intentionally updates the spec.
- Update documentation when public behavior changes.
- Avoid network-dependent tests in the default suite.
- Avoid requiring real audio hardware in the default suite.

## 16. MVP Acceptance Criteria

The first usable version is complete when:

- Potcast starts with a valid YAML config.
- Invalid config errors are clear and actionable.
- The HTTP server exposes `/health` and `/status`.
- The feed monitor downloads the latest episode for each configured podcast.
- A new episode replaces the previous local file for the same podcast.
- Station output starts automatically when `start_on_boot` is enabled.
- Potcast can publish an Icecast-compatible stream.
- Potcast can play through local audio when `local_audio` is enabled.
- `/play`, `/pause`, `/stop`, `/next`, `/previous`, `/channel/next`, and `/channel/previous` work.
- `/status` returns station, output, and feed monitor status.
- `/feeds` and `/feeds/refresh` expose feed status and immediate refresh.
- The service keeps playing or idling gracefully when a feed or download fails.
- Logs are useful enough to debug common setup problems.
- The default test suite covers core business logic and passes locally.
- The repository includes `README.md`, `AGENTS.md`, and deployment documentation.

## 17. Future Enhancements

- Lightweight web UI for configuration and status.
- Authentication token for HTTP commands.
- Sleep timer endpoints.
- Per-channel volume.
- Per-channel shuffle settings.
- Episode duration limits for sleep mode.
- Quiet hours.
- Resume position.
- Playback history.
- Manual episode refresh per podcast.
- OPML import.
- AirPlay output.
- Chromecast output.
- Bluetooth output.
- Home Assistant media player output.
- Multiple simultaneous outputs.
- Text-to-speech status announcements.
