"""Command-line entry point."""

from __future__ import annotations

import argparse
from pathlib import Path

from potcast.config import load_config


def main() -> int:
    parser = argparse.ArgumentParser(description="Run Potcast.")
    parser.add_argument(
        "--config",
        type=Path,
        required=True,
        help="Path to the Potcast YAML config.",
    )
    args = parser.parse_args()

    load_config(args.config)
    return 0
