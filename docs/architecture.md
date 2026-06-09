# Architecture

Potcast keeps business behavior separate from delivery and runtime wiring.

## Layers

- Domain models live in `potcast/models.py`.
- Configuration loading and validation live in `potcast/config.py`.
- Feed parsing lives in `potcast/feeds.py`.
- Feed refresh orchestration lives in `potcast/feed_refresh.py`.
- Atomic media replacement lives in `potcast/downloader.py`.
- JSON runtime persistence lives in `potcast/state.py`.
- Station selection lives in `potcast/station.py`.
- Station commands and output coordination live in `potcast/service.py`.
- Output backends live under `potcast/outputs/`.
- HTTP delivery lives in `potcast/app.py`.
- Runtime composition and lifecycle live in `potcast/runtime.py`.
- CLI argument handling lives in `potcast/cli.py`.

## Runtime Composition

`build_runtime()` is the production composition root. It:

- Loads the YAML config.
- Creates `storage.data_dir` and `storage.episodes_dir`.
- Creates `JsonStateStore`.
- Seeds `state.json` with `station.volume` on first boot.
- Creates the configured primary output backend.
- Creates `StationService`.
- Creates HTTP feed fetch and download dependencies.
- Creates `FeedRefreshService`.
- Creates `PeriodicScheduler` for feed refresh.
- Creates `PeriodicScheduler` for playback supervision.
- Wraps feed status with scheduler `next_refresh_at`.
- Creates the Flask app with injected services.

The CLI calls `build_runtime(...).run()`.

## Boundaries

Flask routes are thin and call injected services. Domain and application services do not
import Flask.

Station selection does not know about subprocesses, Icecast, mpv, HTTP, or the filesystem.

Feed parsing is testable from strings and fixture-like bytes. Download replacement is
testable with temporary directories and injected writers.

Subprocess-backed outputs accept an injected process launcher so tests can assert command
construction without starting `ffmpeg` or `mpv`.

## State

Runtime metadata is stored as UTF-8 JSON under `storage.data_dir`:

- `state.json`: station status, active channel, active podcast, volume, previous history.
- `feeds.json`: feed status and latest episode metadata.
- `downloads.json`: local media file and episode identity per podcast.

Downloaded media is stored below `storage.episodes_dir` in per-podcast directories.
Filenames are stable and filesystem-safe: the podcast ID is sanitized, the episode
identity is hashed, and the media extension is inferred from the enclosure URL or media
type. Replacement writes and validates a temporary file before moving it into place and
removing the previous episode.

## Feed Refresh

`FeedRefreshService` loads configured podcasts, fetches each RSS or Atom feed, parses the
newest playable enclosure, and downloads only when the newest episode identity differs
from the current download metadata. Episode identity prefers RSS `guid` and falls back to
the enclosure URL.

Feed fetch, parse, and download failures update structured feed metadata while preserving
the previous feed episode and downloaded media. Supported media types are `audio/mpeg`,
`audio/mp3`, `audio/mp4`, `audio/x-m4a`, `audio/aac`, and `audio/ogg`.

## Scheduler

`PeriodicScheduler` exposes `run_once()` and `run_due()` so scheduler behavior can be
tested without sleeps. Runtime startup starts one background scheduler for feed refresh
and one lightweight playback supervisor that checks whether the active output episode has
finished. Manual `/feeds/refresh` uses the same feed refresh service and rejects
overlapping refreshes.

The scheduler's background loop runs one refresh immediately after startup, then waits
for the configured interval before the next tick. `/status` and `/feeds` expose feed
monitor status, including `running`, latest start and finish times, latest result or
error, and the scheduler-provided `next_refresh_at`.

The playback supervisor calls `StationService.advance_if_finished()`. That service method
only advances when persisted station state is `playing` and the output backend consumes a
normal completion event. Paused and stopped stations ignore completion checks. If no
podcast in the active channel has a playable download when completion is consumed, the
station moves to `idle`.

Backend startup failures and unexpected process exits are output errors, not completion
events. The backend reports structured errors in `OutputStatus.error`; the station moves
to `idle` and the supervisor does not immediately relaunch the same episode. This keeps
crashing `ffmpeg` or `mpv` processes from causing a tight retry loop.

## HTTP API

`potcast/app.py` serializes dataclasses into JSON and maps command errors to HTTP status
codes:

- `unknown_channel` and `unknown_podcast`: `404`
- `invalid_volume`: `400`
- `podcast_unavailable`: `409`
- other command failures: `500`

The implemented API includes health, status, station commands, channel and podcast
selection, volume commands, feed status, and manual feed refresh. The planned `/stream`
and `/outputs` endpoints are not implemented yet.

## Outputs

The runtime creates the configured `outputs.primary` backend. `outputs.enabled` is
validated for config consistency, but secondary output backends are not started in the
current implementation.

`IcecastOutputBackend` builds an `ffmpeg` command for the selected local episode and
streams it to Icecast. `LocalAudioOutputBackend` builds an `mpv` command for the selected
local episode and audio device. Both backends accept an injected process launcher so unit
tests can assert command construction and process-completion detection without starting
real processes. Exit code `0` is treated as normal episode completion. Non-zero exits are
reported as `backend_process_failed`.

## Adding An Output Backend

New backends should implement `OutputBackend` from `potcast/outputs/base.py`:

```python
def start() -> None: ...
def pause() -> None: ...
def stop() -> None: ...
def play_episode(episode) -> None: ...
def set_volume(volume: int) -> None: ...
def status() -> OutputStatus: ...
def consume_playback_event() -> OutputPlaybackEvent | None: ...
```

Add config validation, a runtime factory branch in `build_output_backend()`, and tests
using fakes or command-construction assertions. Default tests must not require real
devices or external services.

The usual change set for a backend is:

- Add a typed config model in `potcast/models.py`.
- Parse and validate the new config section in `potcast/config.py`.
- Implement the backend under `potcast/outputs/`.
- Add a `build_output_backend()` branch in `potcast/runtime.py`.
- Add unit tests for config validation, command construction or fake behavior, and runtime
  factory selection.
- Update `docs/configuration.md`, `docs/deployment.md`, and this architecture guide.

Avoid coupling the backend to Flask routes or station selection. It should only receive
selected local episodes and volume through the shared output interface.
