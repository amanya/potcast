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
- Creates `PeriodicScheduler`.
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

Downloaded media is stored below `storage.episodes_dir`.

## Scheduler

`PeriodicScheduler` exposes `run_once()` and `run_due()` so scheduler behavior can be
tested without sleeps. Runtime startup starts the background scheduler thread. Manual
`/feeds/refresh` uses the same feed refresh service and rejects overlapping refreshes.

## Adding An Output Backend

New backends should implement `OutputBackend` from `potcast/outputs/base.py`:

```python
def start() -> None: ...
def pause() -> None: ...
def stop() -> None: ...
def play_episode(episode) -> None: ...
def set_volume(volume: int) -> None: ...
def status() -> OutputStatus: ...
```

Add config validation, a runtime factory branch in `build_output_backend()`, and tests
using fakes or command-construction assertions. Default tests must not require real
devices or external services.
