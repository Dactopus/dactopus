"""The models library: a release downloaded into the cache, or a checkout."""

from __future__ import annotations

import io
import os
import tarfile
import tempfile
import urllib.error
import urllib.request
from collections.abc import Callable
from pathlib import Path

from .config import Models

ARCHIVE_URL = "https://github.com/Dactopus/dactopus-data-models/archive/refs/tags/{version}.tar.gz"

Fetch = Callable[[str], bytes]


class LibraryError(Exception):
    """The models library cannot be found or downloaded."""


def cache_dir(environ: dict[str, str] | None = None) -> Path:
    environ = os.environ if environ is None else environ
    base = environ.get("XDG_CACHE_HOME") or Path.home() / ".cache"
    return Path(base) / "dactopus" / "dactopus-data-models"


def _download(url: str) -> bytes:
    with urllib.request.urlopen(url, timeout=60) as response:
        return response.read()


def resolve(models: Models, cache: Path | None = None, fetch: Fetch = _download) -> Path:
    """The root of the library: models.path, or the release in the cache.

    A release is downloaded once: a published tag never moves.
    """
    if models.path is not None:
        return models.path
    cache = cache_dir() if cache is None else cache
    root = cache / models.version
    if (root / "sources").is_dir():
        return root
    url = ARCHIVE_URL.format(version=models.version)
    try:
        archive = fetch(url)
    except urllib.error.HTTPError as exc:
        raise LibraryError(
            f"Downloading the models library {models.version} failed ({exc.code}): "
            "check models.version against the releases of dactopus-data-models."
        ) from None
    except OSError as exc:
        raise LibraryError(
            f"Downloading the models library {models.version} from {url} failed: {exc}."
        ) from None
    cache.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(dir=cache) as tmp:
        top = _extract(archive, Path(tmp))
        if not (top / "sources").is_dir():
            raise LibraryError(f"The archive of {models.version} has no sources/ directory.")
        try:
            top.rename(root)
        except OSError:
            # Another run extracted the same release first.
            if not (root / "sources").is_dir():
                raise
    return root


def _extract(archive: bytes, into: Path) -> Path:
    """Extract a GitHub archive and return its single top directory."""
    try:
        tar = tarfile.open(fileobj=io.BytesIO(archive), mode="r:gz")
        members = tar.getmembers()
    except (tarfile.TarError, OSError, EOFError):
        raise LibraryError("The models archive is not a readable .tar.gz; run again.") from None
    with tar:
        for member in members:
            target = (into / member.name).resolve()
            if not target.is_relative_to(into.resolve()) or not (member.isfile() or member.isdir()):
                raise LibraryError(f"Unexpected entry {member.name!r} in the models archive.")
        # The data filter exists from Python 3.11.4 and is the default in 3.14.
        safe = {"filter": "data"} if hasattr(tarfile, "data_filter") else {}
        tar.extractall(into, members=members, **safe)
    tops = [path for path in into.iterdir() if path.is_dir()]
    if len(tops) != 1:
        raise LibraryError("The models archive does not hold a single directory.")
    return tops[0]
