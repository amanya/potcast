# AGENTS.md

This file gives guidance to AI agents and contributors working on Potcast.

Potcast is intended to be a maintainable, well-tested personal podcast radio service. Treat the repository like production software, even while it is small.

## Project Intent

Potcast runs a continuous podcast station from configured RSS feeds.

Core responsibilities:

- Load one YAML configuration file.
- Group podcasts into channels.
- Monitor RSS feeds.
- Keep the latest playable episode per podcast.
- Replace older local episode files safely.
- Run a continuous station.
- Send audio to output backends such as Icecast or Raspberry Pi local audio.
- Expose simple HTTP GET commands for station control.

## Current Source of Truth

Read these files before making architectural or behavioral changes:

- `SPEC.md`
- `IMPLEMENTATION_PLAN.md`
- `AGENTS.md`

When behavior changes, update the relevant documentation in the same change.

## Engineering Priorities

Prioritize, in order:

1. Correctness of station behavior and feed handling.
2. Testability without real network, audio hardware, or Icecast.
3. Clear component boundaries.
4. Small, understandable modules.
5. Simple deployment.

Avoid cleverness. Potcast should be easy to come back to after a few months away.

## Architecture Rules

Keep these boundaries:

- Flask routes should be thin.
- Domain logic should not import Flask.
- Station selection should not know about subprocesses, Icecast, mpv, or HTTP.
- Feed parsing should be testable from fixture strings or files.
- Download replacement should be testable with temporary directories.
- Output backends should sit behind a common interface.
- Subprocess command construction should be testable without launching the process.

Use dependency injection for:

- HTTP clients.
- Clocks.
- Randomizers.
- Filesystem paths.
- Feed parsers.
- Downloaders.
- Output backends.

Avoid global mutable state except in the application composition root.

## Testing Expectations

Every meaningful behavior change should include tests.

Default tests must not require:

- Internet access.
- Real podcast feeds.
- Icecast.
- Audio devices.
- `ffmpeg` actually running.
- `mpv` actually running.
- Long sleeps.

Use fakes, fixtures, and temporary directories.

Important business logic that should remain covered:

- Config defaults and validation.
- Duplicate channel and podcast IDs.
- Feed parsing and newest episode selection.
- Episode identity from `guid` or enclosure URL.
- Unsupported media filtering.
- Atomic download replacement.
- Feed failure preserving the previous episode.
- Sequential and shuffled station selection.
- Previous podcast history.
- Channel switching.
- Command idempotency.
- Output backend interface behavior.
- HTTP error responses.

## Quality Commands

Use these as the default verification suite once project tooling exists:

```bash
pytest
ruff check .
ruff format --check .
mypy potcast
```

If a command cannot run because tooling has not been created yet, say that clearly in your final response.

## Documentation Expectations

Keep documentation close to behavior.

Expected docs:

- `README.md`: overview, quick start, basic examples.
- `SPEC.md`: product and service specification.
- `IMPLEMENTATION_PLAN.md`: phased build plan.
- `AGENTS.md`: contributor and AI-agent guidance.
- `docs/configuration.md`: complete YAML reference.
- `docs/deployment.md`: Docker, Icecast, and Raspberry Pi local audio.
- `docs/architecture.md`: boundaries, state, outputs, and tests.

Update docs when changing:

- YAML fields.
- HTTP endpoints.
- Output backend behavior.
- Deployment steps.
- Runtime state format.
- Testing or quality commands.

## Output Backends

The first output backends are:

- `icecast`: network stream output.
- `local_audio`: Raspberry Pi or host audio output.

Future output backends may include:

- AirPlay.
- Chromecast.
- Bluetooth.
- Home Assistant media player.

New backends should implement the shared backend interface and include tests using fakes or command-construction assertions. Do not require real devices in the default test suite.

## Feed and Download Rules

Episode identity:

- Prefer RSS `guid`.
- Fall back to enclosure URL.

Playable media:

- `audio/mpeg`
- `audio/mp3`
- `audio/mp4`
- `audio/x-m4a`
- `audio/aac`
- `audio/ogg`

Replacement must be atomic:

1. Download to a temporary file.
2. Validate the temporary file.
3. Move it to the final path.
4. Remove the old file only after the new one is ready.

On feed or download failure, preserve the last good local episode.

## HTTP API Style

The first version intentionally uses HTTP GET for simple command triggering.

Responses should be JSON.

Errors should be structured:

```json
{
  "ok": false,
  "error": {
    "code": "unknown_channel",
    "message": "Channel not found: bedtime"
  }
}
```

Routes should delegate to services and avoid embedding business logic.

## Change Discipline

When making changes:

- Read the nearby code first.
- Keep edits scoped.
- Prefer existing patterns once the codebase has them.
- Add tests for behavior changes.
- Update docs for public behavior changes.
- Do not add live-network tests to the default suite.
- Do not add real-audio tests to the default suite.
- Do not silently change the spec.

If the spec and implementation disagree, either update the implementation to match the spec or explicitly update the spec and explain why.

## Useful Milestones

Suggested order:

1. Tooling and project skeleton.
2. Config models and validation.
3. Feed parsing.
4. Atomic downloads and state.
5. Station selection.
6. Output backend interface.
7. Station service.
8. HTTP API.
9. Scheduler.
10. Docker and deployment docs.

Keep the test suite useful at every milestone.
