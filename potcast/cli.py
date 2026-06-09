"""Command-line entry point."""

from __future__ import annotations

import argparse
from pathlib import Path

from potcast.runtime import build_runtime, configure_logging


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Run Potcast.")
    parser.add_argument(
        "--config",
        type=Path,
        required=True,
        help="Path to the Potcast YAML config.",
    )
    parser.add_argument(
        "--log-level",
        default="INFO",
        help="Logging level, such as DEBUG, INFO, WARNING, or ERROR.",
    )
    args = parser.parse_args(argv)

    configure_logging(args.log_level)
    build_runtime(args.config).run()
    return 0
