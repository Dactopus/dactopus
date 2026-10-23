import io
import tarfile
from pathlib import Path

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


# The deployment.yml of the GA4 and Shopify packages of v0.6.0, and one
# without vars.input_database.
FIXTURE_LIBRARY = Path(__file__).parent / "fixtures" / "library"
CHECKOUT = Models(version=None, path=FIXTURE_LIBRARY)


def test_a_package_with_a_time_zone(tmp_path):
    declared = library.deployment(CHECKOUT, FIXTURE_LIBRARY, "shopify")
    assert declared.model == "entities/commerce.yaml"
    assert declared.vars == {
        "input_database": "shopify_input_database",
        "time_zone": "shopify_timezone",
    }
    assert declared.daily == (
        library.Daily("orders", "order_date", "order_time", gap=True),
        library.Daily("refunds", "refund_date", "refund_time", gap=False),
    )


def test_a_package_without_a_time_zone(tmp_path):
    declared = library.deployment(CHECKOUT, FIXTURE_LIBRARY, "ga4")
    assert declared.model == "entities/web_analytics.yaml"
    assert declared.vars == {"input_database": "ga4_input_database"}
    assert [(d.table, d.gap) for d in declared.daily] == [
        ("events", True),
        ("sessions", False),
        ("purchases", False),
    ]


def test_a_missing_file_names_what_to_raise():
    with pytest.raises(LibraryError) as found:
        library.deployment(Models("v0.5.3", None), FIXTURE_LIBRARY, "woocommerce")
    assert str(found.value) == (
        "woocommerce: the models library has no sources/woocommerce/deployment.yml; "
        "set models.version to v0.6.0 or later."
    )
    with pytest.raises(LibraryError, match="update the checkout at models.path to v0.6.0"):
        library.deployment(CHECKOUT, FIXTURE_LIBRARY, "woocommerce")


def test_a_missing_key_names_the_package_and_the_file():
    with pytest.raises(LibraryError) as found:
        library.deployment(CHECKOUT, FIXTURE_LIBRARY, "broken")
    assert str(found.value) == (
        "broken: sources/broken/deployment.yml: vars.input_database: missing; "
        "expected a plain name; report it."
    )


def _declare(tmp_path, text):
    (tmp_path / "sources" / "p").mkdir(parents=True)
    (tmp_path / "sources" / "p" / "deployment.yml").write_text(text)
    return library.deployment(Models(None, tmp_path), tmp_path, "p")


def test_unknown_keys_and_roles_are_ignored(tmp_path):
    declared = _declare(
        tmp_path,
        "model: m.yaml\nowner: x\nvars: {input_database: p_db, currency: p_currency}\n"
        "daily: [{table: t, date: d, unit: rows}]\n",
    )
    assert declared.daily == (library.Daily("t", "d"),)


@pytest.mark.parametrize(
    ("text", "message"),
    [
        ("vars: {input_database: v}\ndaily: []\n", "model: missing"),
        ("model: m\nvars: {input_database: v}\n", "daily: missing"),
        ("model: m\nvars: {input_database: v}\ndaily: [{date: d}]\n", r"daily\[0\].table"),
        (
            "model: m\nvars: {input_database: v}\ndaily: [{table: 't`; DROP', date: d}]\n",
            r"daily\[0\].table: missing; expected a plain name",
        ),
        ("model: m\nvars: {input_database: v}\ndaily: [{table: t, date: d, gap: 1}]\n", "gap"),
        ("model: [\n", "not valid YAML"),
    ],
)
def test_a_wrong_declaration_is_refused(tmp_path, text, message):
    with pytest.raises(LibraryError, match=message):
        _declare(tmp_path, text)
