import io
import tarfile

import pytest

from dactopus import library
from dactopus.config import Models
from dactopus.library import LibraryError


def _archive(entries: dict[str, bytes], symlink: str | None = None) -> bytes:
    buffer = io.BytesIO()
    with tarfile.open(fileobj=buffer, mode="w:gz") as tar:
        for name, data in entries.items():
            info = tarfile.TarInfo(name)
            info.size = len(data)
            tar.addfile(info, io.BytesIO(data))
        if symlink:
            info = tarfile.TarInfo(symlink)
            info.type = tarfile.SYMTYPE
            info.linkname = "/etc/passwd"
            tar.addfile(info)
    return buffer.getvalue()


RELEASE = _archive({"dactopus-data-models-0.4.1/sources/ga4/input.sql": b"-- input"})


def test_release_is_downloaded_once(tmp_path):
    calls = []

    def fetch(url):
        calls.append(url)
        return RELEASE

    models = Models(version="v0.4.1", path=None)
    root = library.resolve(models, cache=tmp_path, fetch=fetch)
    assert root == tmp_path / "v0.4.1"
    assert (root / "sources/ga4/input.sql").read_text() == "-- input"
    assert library.resolve(models, cache=tmp_path, fetch=fetch) == root
    assert calls == [library.ARCHIVE_URL.format(version="v0.4.1")]


def test_checkout_is_used_as_is(tmp_path):
    def fetch(url):
        raise AssertionError("no download for a path")

    assert library.resolve(Models(version=None, path=tmp_path), fetch=fetch) == tmp_path


def test_archive_with_a_link_is_refused(tmp_path):
    archive = _archive({"top/sources/a": b""}, symlink="top/sources/link")
    with pytest.raises(LibraryError, match="Unexpected entry"):
        library.resolve(Models("v9.9.9", None), cache=tmp_path, fetch=lambda url: archive)
    assert not (tmp_path / "v9.9.9").exists()


def test_archive_without_sources_is_refused(tmp_path):
    archive = _archive({"top/README.md": b""})
    with pytest.raises(LibraryError, match="no sources"):
        library.resolve(Models("v9.9.9", None), cache=tmp_path, fetch=lambda url: archive)


def test_unreadable_archive_is_refused(tmp_path):
    with pytest.raises(LibraryError, match="not a readable"):
        library.resolve(Models("v9.9.9", None), cache=tmp_path, fetch=lambda url: b"<html>")
