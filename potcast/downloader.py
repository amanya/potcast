"""Atomic episode media download and replacement."""

from __future__ import annotations

import hashlib
import os
import re
import tempfile
from collections.abc import Callable
from contextlib import suppress
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlparse

from potcast.errors import DownloadError
from potcast.models import DownloadMetadata, Episode

DownloadWriter = Callable[[str, Path], None]
Clock = Callable[[], datetime]

_SAFE_SEGMENT_PATTERN = re.compile(r"[^a-zA-Z0-9._-]+")
_MEDIA_TYPE_EXTENSIONS = {
    "audio/mpeg": ".mp3",
    "audio/mp3": ".mp3",
    "audio/mp4": ".m4a",
    "audio/x-m4a": ".m4a",
    "audio/aac": ".aac",
    "audio/ogg": ".ogg",
}


class AtomicEpisodeDownloader:
    """Download episode media to a temp file before replacing the active file."""

    def __init__(
        self,
        episodes_dir: Path,
        writer: DownloadWriter,
        *,
        clock: Clock | None = None,
    ) -> None:
        self.episodes_dir = episodes_dir
        self._writer = writer
        self._clock = clock or _utc_now

    def replace_episode(
        self,
        podcast_id: str,
        episode: Episode,
        previous: DownloadMetadata | None = None,
    ) -> DownloadMetadata:
        final_path = final_episode_path(self.episodes_dir, podcast_id, episode)
        final_path.parent.mkdir(parents=True, exist_ok=True)
        temp_path = _temporary_path(final_path)

        try:
            self._writer(episode.media_url, temp_path)
            _validate_download(temp_path)
            os.replace(temp_path, final_path)
            _remove_previous(previous, final_path)
        except Exception as exc:
            _remove_temp(temp_path)
            if isinstance(exc, DownloadError):
                raise
            raise DownloadError(f"Could not download episode for podcast: {podcast_id}") from exc

        return DownloadMetadata(
            podcast_id=podcast_id,
            episode_identity=episode.identity,
            media_url=episode.media_url,
            media_type=episode.media_type,
            local_file=final_path,
            downloaded_at=self._clock(),
            title=episode.title,
        )


def final_episode_path(episodes_dir: Path, podcast_id: str, episode: Episode) -> Path:
    """Return a stable, filesystem-safe media path for a podcast episode."""

    podcast_segment = _safe_segment(podcast_id)
    identity_digest = hashlib.sha256(episode.identity.encode("utf-8")).hexdigest()[:16]
    extension = _episode_extension(episode)
    return episodes_dir / podcast_segment / f"{podcast_segment}-{identity_digest}{extension}"


def _temporary_path(final_path: Path) -> Path:
    fd, raw_path = tempfile.mkstemp(
        dir=final_path.parent,
        prefix=f".{final_path.name}.",
        suffix=".tmp",
    )
    os.close(fd)
    path = Path(raw_path)
    path.unlink()
    return path


def _validate_download(path: Path) -> None:
    if not path.exists():
        raise DownloadError("Downloaded episode file was not created.")
    if path.stat().st_size == 0:
        raise DownloadError("Downloaded episode file is empty.")


def _remove_previous(previous: DownloadMetadata | None, final_path: Path) -> None:
    if previous is None or previous.local_file == final_path:
        return
    try:
        previous.local_file.unlink(missing_ok=True)
    except OSError as exc:
        message = f"Could not remove previous episode file: {previous.local_file}"
        raise DownloadError(message) from exc


def _remove_temp(path: Path) -> None:
    with suppress(OSError):
        path.unlink(missing_ok=True)


def _safe_segment(value: str) -> str:
    safe = _SAFE_SEGMENT_PATTERN.sub("-", value.strip()).strip(".-_").lower()
    return safe or "podcast"


def _episode_extension(episode: Episode) -> str:
    parsed = urlparse(episode.media_url)
    suffix = Path(parsed.path).suffix.lower()
    if suffix in {".mp3", ".m4a", ".mp4", ".aac", ".ogg"}:
        return ".m4a" if suffix == ".mp4" and episode.media_type == "audio/mp4" else suffix
    return _MEDIA_TYPE_EXTENSIONS.get(episode.media_type, ".audio")


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)
