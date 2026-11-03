import io
import tarfile
from pathlib import Path

import pytest

from dactopus import config, library
from dactopus.config import ConfigError, Models
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


RELEASE = _archive({"dactopus-data-models-0.10.1/sources/ga4/input.sql": b"-- input"})


def test_release_is_downloaded_once(tmp_path):
    calls = []

    def fetch(url):
        calls.append(url)
        return RELEASE

    models = Models(version="v0.10.1", path=None)
    root = library.resolve(models, cache=tmp_path, fetch=fetch)
    assert root == tmp_path / "v0.10.1"
    assert (root / "sources/ga4/input.sql").read_text() == "-- input"
    assert library.resolve(models, cache=tmp_path, fetch=fetch) == root
    assert calls == [library.ARCHIVE_URL.format(version="v0.10.1")]


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


# The deployment.yml of the packages and unions of v0.10.0, and a package
# without vars.input_database.
FIXTURE_LIBRARY = Path(__file__).parent / "fixtures" / "library"
CHECKOUT = Models(version=None, path=FIXTURE_LIBRARY)


def test_a_package_with_a_time_zone(tmp_path):
    declared = library.deployment(CHECKOUT, FIXTURE_LIBRARY, "shopify")
    assert declared.model == "entities/commerce.yaml"
    assert declared.vars == {
        "input_database": "shopify_input_database",
        "source_id": "shopify_source_id",
        "time_zone": "shopify_timezone",
    }
    assert declared.daily == (
        library.Daily("orders", "order_date", "order_time", gap=True),
        library.Daily("refunds", "refund_date", "refund_time", gap=False),
    )


def test_a_package_without_a_time_zone(tmp_path):
    declared = library.deployment(CHECKOUT, FIXTURE_LIBRARY, "ga4")
    assert declared.model == "entities/web_analytics.yaml"
    assert declared.vars == {
        "input_database": "ga4_input_database",
        "source_id": "ga4_source_id",
    }
    assert [(d.table, d.gap) for d in declared.daily] == [
        ("events", True),
        ("sessions", False),
        ("purchases", False),
    ]


def test_a_package_without_the_file_names_what_to_raise(tmp_path):
    (tmp_path / "sources" / "woocommerce").mkdir(parents=True)
    with pytest.raises(LibraryError) as found:
        library.deployment(Models("v0.5.3", None), tmp_path, "woocommerce")
    assert str(found.value) == (
        "woocommerce: the models library has no sources/woocommerce/deployment.yml; "
        "set models.version to v0.10.0 or later."
    )
    with pytest.raises(LibraryError, match="update the checkout at models.path to v0.10.0"):
        library.deployment(Models(None, tmp_path), tmp_path, "woocommerce")


def test_an_unknown_package_names_the_library_s_packages():
    with pytest.raises(LibraryError) as found:
        library.deployment(Models("v0.10.0", None), FIXTURE_LIBRARY, "shopfy")
    assert str(found.value) == (
        "shopfy is not a package of the models library v0.10.0; its packages: broken, ga4, "
        "sensor_community, shopify, woocommerce."
    )


def test_a_missing_key_names_the_package_and_the_file():
    with pytest.raises(LibraryError) as found:
        library.deployment(CHECKOUT, FIXTURE_LIBRARY, "broken")
    assert str(found.value) == (
        "broken: sources/broken/deployment.yml: vars.input_database: missing; "
        "expected a plain name; report it."
    )


def test_a_package_is_read_once(tmp_path):
    """Every step of a command sees the declarations the first read found."""
    first = _declare(tmp_path, "model: m.yaml\nvars: {input_database: p_db}\ndaily: []\n")
    (tmp_path / "sources" / "p" / "deployment.yml").write_text("model: [\n")
    assert library.deployment(Models(None, tmp_path), tmp_path, "p") is first


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


def _check(tmp_path, old="", new="", root=FIXTURE_LIBRARY):
    """library.check over the sample configuration, edited, and the fixture library."""
    path = tmp_path / "dactopus.yaml"
    path.write_text(config.SAMPLE.replace(old, new))
    library.check(config.load(path, environ={}), root)


def test_declared_vars_pass(tmp_path):
    _check(tmp_path)


def test_input_database_variable_is_reserved(tmp_path):
    with pytest.raises(
        ConfigError,
        match="sources.website.vars: remove ga4_input_database; the runner sets it from "
        "sources.website.input_database or clickhouse.database",
    ):
        _check(tmp_path, "ga4_lookback_days: 3", "ga4_input_database: other")


def test_label_variable_is_reserved(tmp_path):
    with pytest.raises(
        ConfigError,
        match="sources.website.vars: remove ga4_source_id; the runner sets it from the label",
    ):
        _check(tmp_path, "ga4_lookback_days: 3", "ga4_source_id: other")


def test_time_zone_variable_is_reserved(tmp_path):
    with pytest.raises(
        ConfigError,
        match="sources.main_store.vars: remove shopify_timezone; the runner sets it from "
        "sources.main_store.timezone or timezone",
    ):
        _check(
            tmp_path,
            "client_secret_env: SHOPIFY_CLIENT_SECRET\n",
            "client_secret_env: SHOPIFY_CLIENT_SECRET\n    vars: {shopify_timezone: UTC}\n",
        )


def test_a_library_without_unions_is_refused(tmp_path):
    root = tmp_path / "library"
    (root / "sources").mkdir(parents=True)
    with pytest.raises(LibraryError) as found:
        _check(tmp_path, root=root)
    assert str(found.value) == (
        "The models library v0.10.0 has no unions/, which came in v0.9.0 with the labels of "
        "the sources; set models.version to v0.10.0 or later."
    )


def _union_declarations(root, *names):
    for name in names:
        (root / "unions" / name).mkdir(parents=True, exist_ok=True)
        text = (FIXTURE_LIBRARY / "unions" / name / "deployment.yml").read_text()
        (root / "unions" / name / "deployment.yml").write_text(text)


def test_a_package_without_the_label_role_is_refused(tmp_path):
    root = tmp_path / "library"
    _union_declarations(root, "commerce", "web_analytics")
    for package in ("ga4", "shopify"):
        (root / "sources" / package).mkdir(parents=True)
        text = (FIXTURE_LIBRARY / "sources" / package / "deployment.yml").read_text()
        text = "\n".join(line for line in text.splitlines() if "source_id:" not in line)
        (root / "sources" / package / "deployment.yml").write_text(text)
    with pytest.raises(
        LibraryError,
        match="ga4: sources/ga4/deployment.yml maps no variable to the role source_id; set "
        "models.version to v0.10.0 or later",
    ):
        _check(tmp_path, root=root)


def test_a_package_no_union_serves_is_refused(tmp_path):
    root = tmp_path / "library"
    _union_declarations(root, "commerce")
    for package in ("ga4", "shopify"):
        (root / "sources" / package).mkdir(parents=True)
        text = (FIXTURE_LIBRARY / "sources" / package / "deployment.yml").read_text()
        (root / "sources" / package / "deployment.yml").write_text(text)
    with pytest.raises(
        LibraryError,
        match="ga4: no union of the models library serves entities/web_analytics.yaml, so "
        "website could not be served; set models.version to v0.10.0 or later",
    ):
        _check(tmp_path, root=root)


def _sources(tmp_path, *packages):
    path = tmp_path / "dactopus.yaml"
    known = {
        "ga4": {"package": "ga4", "project": "p", "dataset": "a.b"},
        "shopify": {"package": "shopify", "shop": "s"},
        "woocommerce": {"package": "woocommerce"},
    }
    sources = {f"{package}_{i}": dict(known[package]) for i, package in enumerate(packages)}
    path.write_text(
        "clickhouse: {url: 'http://db.local', database: d}\n"
        "timezone: UTC\n"
        f"models: {{path: {FIXTURE_LIBRARY}}}\n"
        f"sources: {sources}\n"
    )
    return config.load(path, environ={})


def test_a_source_belongs_to_the_union_that_serves_its_packages_model(tmp_path):
    cfg = _sources(tmp_path, "shopify", "ga4", "woocommerce", "shopify")
    found = library.unions_of(cfg, FIXTURE_LIBRARY)
    assert {union.name: [s.label for s in members] for union, members in found.items()} == {
        "commerce": ["shopify_0", "woocommerce_2", "shopify_3"],
        "web_analytics": ["ga4_1"],
    }
    commerce = next(union for union in found if union.name == "commerce")
    assert (commerce.model, commerce.databases) == (
        "entities/commerce.yaml",
        "commerce_union_databases",
    )


def test_a_union_without_sources_is_left_out(tmp_path):
    cfg = _sources(tmp_path, "shopify")
    assert [u.name for u in library.unions_of(cfg, FIXTURE_LIBRARY)] == ["commerce"]


@pytest.mark.parametrize(
    ("text", "message"),
    [
        ("vars: {input_databases: d}\n", "model: missing"),
        ("model: m\n", r"vars: missing"),
        ("model: m\nvars: {input_databases: 'a; b'}\n", "input_databases: missing; expected a"),
        ("model: [\n", "not valid YAML"),
    ],
)
def test_a_wrong_union_declaration_is_refused(tmp_path, text, message):
    root = tmp_path / "library"
    (root / "unions" / "u").mkdir(parents=True)
    (root / "unions" / "u" / "deployment.yml").write_text(text)
    with pytest.raises(LibraryError, match=message):
        library.union_deployments(Models(version=None, path=root), root)


@pytest.mark.parametrize("version", ["v0.9.0", "v0.2.1", "v0.1.9"])
def test_a_release_before_the_floor_is_refused(tmp_path, version):
    with pytest.raises(LibraryError) as found:
        _check(tmp_path, "version: v0.10.0", f"version: {version}")
    assert str(found.value) == (
        f"The models library {version} is older than v0.10.0, the first the runner reads; "
        "set models.version to v0.10.0 or later."
    )


def test_a_release_before_the_floor_is_not_downloaded(tmp_path):
    def fetch(url):
        raise AssertionError("no download for a refused release")

    with pytest.raises(LibraryError, match="older than v0.10.0"):
        library.resolve(Models("v0.9.0", None), cache=tmp_path, fetch=fetch)


@pytest.mark.parametrize("version", ["v0.10.0", "v0.10.1", "v0.11.0", "v1.0.0", "v0.100.0"])
def test_a_release_from_the_floor_is_accepted(tmp_path, version):
    # Numbers are compared, not strings: v0.9.0 < v0.10.0 < v0.100.0.
    _check(tmp_path, "version: v0.10.0", f"version: {version}")


def test_a_checkout_is_not_compared_to_the_floor(tmp_path):
    path = tmp_path / "dactopus.yaml"
    path.write_text(config.SAMPLE.replace("version: v0.10.0", f"path: {FIXTURE_LIBRARY}"))
    library.check(config.load(path, environ={}), FIXTURE_LIBRARY)
