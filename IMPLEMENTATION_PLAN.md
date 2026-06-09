# Potcast Implementation Plan

## 1. Development Philosophy

Potcast should be implemented as a small, well-tested service with clear boundaries between domain logic, infrastructure, and delivery mechanisms.

The core rule: business logic must be testable without network access, real podcast feeds, real Icecast, real audio devices, or long-running background services.

## 2. Target Architecture

Potcast should be organized around these layers:

- **Domain:** channels, podcasts, episodes, station state, selection rules, command behavior.
- **Application services:** feed refresh orchestration, download replacement, station control, output coordination.
- **Infrastructure:** RSS HTTP client, media downloader, filesystem state store, subprocess-backed output backends.
- **Delivery:** HTTP API, CLI, Docker runtime.

Dependencies should point inward. Flask routes should call application services. Application services should depend on interfaces that can be replaced with fakes in tests.

## 3. Initial Repository Setup

Create the project structure:

```text
potcast/
  __init__.py
  app.py
  cli.py
  config.py
  downloader.py
  errors.py
  feeds.py
  models.py
  scheduler.py
  station.py
  state.py
  outputs/
    __init__.py
    base.py
    icecast.py
    local_audio.py
tests/
  unit/
  integration/
  fixtures/
docs/
  architecture.md
  configuration.md
  deployment.md
```

Add project tooling:

- `pyproject.toml`
- `pytest`
- `ruff`
- `mypy`
- `PyYAML`
- `feedparser`
- `httpx` or `requests`
- `Flask`
- Python 3.10 or newer.

Acceptance gate:

- `pytest` runs successfully.
- `ruff check .` runs successfully.
- `ruff format --check .` runs successfully.
- `mypy potcast` runs successfully.

## 4. Phase 1: Typed Models and Configuration

Implement:

- Typed models for server, storage, station, outputs, channels, podcasts, episodes, feed metadata, and runtime state.
- YAML loading.
- Defaults.
- Validation.
- Clear configuration errors.

Important decisions:

- Reject duplicate channel IDs.
- Reject duplicate podcast IDs in the first version.
- Require at least one channel.
- Require at least one podcast per channel.
- Require the primary output to be configured and enabled.
- Validate station volume range from 0 to 100.

Tests:

- Valid minimal config loads with defaults.
- Full config loads with explicit values.
- Duplicate channel IDs fail.
- Duplicate podcast IDs fail.
- Missing required fields fail.
- Invalid output backend fails.
- Invalid volume fails.

## 5. Phase 2: Feed Parsing and Episode Selection

Implement:

- RSS parsing from text or bytes.
- Playable enclosure discovery.
- Episode identity selection.
- Newest playable episode selection.
- Feed status result model.

Rules:

- Prefer RSS `guid` for episode identity.
- Fall back to enclosure URL if `guid` is missing.
- Skip entries without playable audio enclosures.
- Skip unsupported media types.
- Preserve enough metadata for status output and future debugging.

Tests:

- Selects newest playable episode.
- Uses `guid` identity when available.
- Falls back to enclosure URL.
- Skips unsupported media.
- Skips entries without enclosures.
- Handles malformed feeds with a structured failure result.

## 6. Phase 3: Storage and Atomic Downloads

Implement:

- Data directory creation.
- Runtime state persistence.
- Feed metadata persistence.
- Download metadata persistence.
- Atomic media replacement.

Download behavior:

1. Download to a temporary file in the same filesystem as the final path.
2. Validate file exists and has non-zero size.
3. Move temporary file to final path.
4. Remove the previous episode only after the new episode is ready.
5. Preserve the previous episode when the new download fails.

Tests:

- State round trip through a temporary directory.
- New episode replaces old episode.
- Failed download preserves old episode.
- Empty download is rejected.
- Final filename is stable and safe.

## 7. Phase 4: Station Selection Logic

Implement:

- Active channel state.
- Active podcast state.
- Sequential podcast navigation.
- Shuffle podcast navigation.
- Previous podcast history.
- Channel navigation.
- Skip unavailable podcasts.

The station selector should not know about Flask, Icecast, mpv, or the filesystem.

Tests:

- Next podcast in sequential mode.
- Previous podcast in sequential mode.
- Shuffle avoids immediate repeats where possible.
- Shuffle eventually covers all playable podcasts before resetting.
- Previous uses history in shuffle mode.
- Channel next and previous follow configured order.
- Empty or unavailable channels idle gracefully.

## 8. Phase 5: Output Backend Interface

Define a common output interface, for example:

```python
class OutputBackend(Protocol):
    def start(self) -> None: ...
    def pause(self) -> None: ...
    def stop(self) -> None: ...
    def play_episode(self, episode: Episode) -> None: ...
    def set_volume(self, volume: int) -> None: ...
    def status(self) -> OutputStatus: ...
    def consume_playback_event(self) -> OutputPlaybackEvent | None: ...
```

Implement:

- `FakeOutputBackend` for tests.
- `IcecastOutputBackend` command construction.
- `LocalAudioOutputBackend` command construction.

The first implementation can construct and manage subprocesses, but tests should verify command construction with fakes rather than launching `ffmpeg`, Icecast, or `mpv`.

Tests:

- Fake backend records start, pause, stop, play, and volume calls.
- Icecast backend builds expected `ffmpeg` command.
- Local audio backend builds expected `mpv` command.
- Backend errors surface as structured output status.
- Normal output process completion is distinguished from unexpected process failure.

## 9. Phase 6: Station Service

Implement the application service that coordinates:

- Current station state.
- Selection logic.
- Output backend.
- Command idempotency.
- Status construction.

Commands:

- `play`
- `pause`
- `toggle`
- `stop`
- `next`
- `previous`
- `next_channel`
- `previous_channel`
- `select_channel`
- `select_podcast`
- `set_volume`

Tests:

- `play` is idempotent.
- `pause` is idempotent.
- `stop` is idempotent.
- `next` changes podcast and calls output backend.
- Channel changes update state and call output backend.
- Status includes active channel, podcast, episode, volume, and output status.

## 10. Phase 7: HTTP API

Implement Flask app factory:

- `create_app(config, services)`

Endpoints:

- `/health`
- `/status`
- `/play`
- `/pause`
- `/toggle`
- `/stop`
- `/next`
- `/previous`
- `/channel/next`
- `/channel/previous`
- `/channel/<channel_id>`
- `/podcast/next`
- `/podcast/previous`
- `/podcast/<podcast_id>`
- `/output/recover`
- `/volume`
- `/volume/<level>`
- `/volume/up`
- `/volume/down`
- `/feeds`
- `/feeds/refresh`

Tests:

- Health returns version.
- Status returns JSON.
- Commands call station service.
- Unknown channel returns structured `404`.
- Invalid volume returns structured `400`.
- Feed refresh returns quickly and does not block on downloads.

Planned but not implemented in this phase:

- `/stream`
- `/outputs`

## 11. Phase 8: Feed Monitor and Scheduler

Implement:

- Feed refresh service.
- Periodic scheduler.
- Immediate refresh trigger.
- Structured feed statuses.
- Safe shutdown.

Behavior:

- Feed refreshes fetch each configured RSS feed, parse the newest playable episode, and
  atomically download only when the newest episode differs from the current download.
- Feed fetch, parse, and download failures update structured status while preserving the
  previous good feed episode metadata and download metadata.
- `/feeds/refresh` starts work in the background and returns before downloads complete.
- Overlapping refresh triggers are rejected with `already_running` while the active
  refresh continues.
- The scheduler exposes one-tick behavior for tests and does not require long sleeps in
  the default suite.

Tests:

- Refresh updates metadata for a new episode.
- Refresh does not replace existing episode on feed failure.
- Scheduler can run one tick in tests without sleeping.
- Immediate refresh is rejected or coalesced if one is already running.

## 12. Phase 9: Runtime Packaging

Implement:

- CLI entry point.
- Dockerfile.
- Docker Compose example.
- Example config file.
- Basic logging setup.
- Optional systemd example for Raspberry Pi local audio.

Acceptance gate:

- Potcast starts with an example config.
- `/health` responds.
- `/status` responds.
- Docker image builds.
- Docker Compose example is documented.

## 13. Phase 10: Documentation

Create or update:

- `README.md`
- `docs/configuration.md`
- `docs/deployment.md`
- `docs/architecture.md`
- `AGENTS.md`

Documentation must explain:

- What Potcast does.
- How channels and podcasts are configured.
- How outputs work.
- How to run with Icecast.
- How to run with Raspberry Pi local audio.
- How to run tests and quality checks.
- How to add a new output backend.

## 14. Regression Strategy

Before merging meaningful changes, run:

```bash
pytest
ruff check .
ruff format --check .
mypy potcast
```

For changes touching Docker, output backends, or deployment:

- Build the Docker image.
- Validate the documented example config.
- Run backend command-construction tests.

For changes touching feed parsing:

- Add or update RSS fixture files.
- Confirm tests do not rely on live podcast feeds.

## 15. Suggested Build Order

1. Project scaffolding and tooling.
2. Models and config validation.
3. Feed parsing.
4. Storage and downloads.
5. Station selection logic.
6. Output backend interface with fake backend.
7. Local audio and Icecast backend command construction.
8. Station service.
9. HTTP API.
10. Feed monitor and scheduler.
11. Docker and deployment examples.
12. Documentation polish.

This order keeps the hard-to-test operational pieces near the end, after the core business logic is already covered.
