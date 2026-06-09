"""HTTP app composition placeholder."""

from __future__ import annotations

from flask import Flask


def create_app() -> Flask:
    app = Flask(__name__)

    @app.get("/health")
    def health() -> dict[str, object]:
        return {"ok": True, "version": "0.1.0"}

    return app
