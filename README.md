# Potcast

Potcast is a personal podcast radio service. It monitors configured podcast RSS feeds,
keeps the latest playable episode for each podcast, and runs a continuous station through
outputs such as Icecast or Raspberry Pi local audio.

The project is currently in early implementation. See:

- `SPEC.md`
- `IMPLEMENTATION_PLAN.md`
- `AGENTS.md`

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
