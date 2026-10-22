from pathlib import Path
from urllib.parse import unquote, urlsplit

import pytest
import yaml
from dactopus_ossie_clickhouse import load_model

from dactopus import config, serve
from dactopus.cli import main
from dactopus.serve import ServeError


def _config(tmp_path: Path, url: str, packages=("ga4", "shopify"), environ=None) -> config.Config:
    all_packages = {
        "ga4": {"project": "p", "dataset": "bigquery-public-data.ga4_sample"},
        "shopify": {"shop": "dactopus-dev"},
    }
    clickhouse = {"url": url, "input_database": "raw", "database": "canonical"}
    if environ:
        clickhouse["password_env"] = "CLICKHOUSE_PASSWORD"
    path = tmp_path / "dactopus.yaml"
    path.write_text(
        yaml.safe_dump(
            {
                "clickhouse": clickhouse,
                "models": {"version": "v0.5.3"},
                "packages": {name: all_packages[name] for name in packages},
            }
        )
    )
    return config.load(path, environ=environ or {})


def _library(tmp_path: Path, files=None) -> Path:
    root = tmp_path / "library"
    for name in serve.MODEL_FILES.values() if files is None else files:
        (root / name).parent.mkdir(parents=True, exist_ok=True)
        (root / name).write_text("")
    return root


@pytest.mark.parametrize(
    ("packages", "files"),
    [
        (("ga4",), ["entities/web_analytics.yaml"]),
        (("shopify",), ["entities/commerce.yaml"]),
        (("ga4", "shopify"), ["entities/web_analytics.yaml", "entities/commerce.yaml"]),
    ],
)
def test_files_are_the_domains_of_the_packages(tmp_path, packages, files):
    cfg = _config(tmp_path, "http://default@127.0.0.1:8123", packages)
    root = _library(tmp_path)
    assert serve.model_files(cfg, root) == [root / name for name in files]


def test_missing_model_file_names_the_package(tmp_path):
    cfg = _config(tmp_path, "http://default@127.0.0.1:8123")
    root = _library(tmp_path, ["entities/web_analytics.yaml"])
    with pytest.raises(ServeError, match="shopify: the models library has no entities/commerce"):
        serve.model_files(cfg, root)


def test_url_reads_the_canonical_database(tmp_path):
    cfg = _config(tmp_path, "http://loader@db.local:18123")
    assert serve.url(cfg) == "http://loader@db.local:18123/canonical"


def test_url_quotes_the_credentials(tmp_path):
    secret = "p@ss:w/rd?#%"
    cfg = _config(tmp_path, "https://us%20er@db.local", environ={"CLICKHOUSE_PASSWORD": secret})
    parts = urlsplit(serve.url(cfg))
    assert (parts.scheme, parts.hostname, parts.port, parts.path) == (
        "https",
        "db.local",
        8443,
        "/canonical",
    )
    assert (unquote(parts.username), unquote(parts.password)) == ("us er", secret)


def test_url_brackets_an_ipv6_host(tmp_path):
    cfg = _config(tmp_path, "http://default@[::1]:8123")
    assert serve.url(cfg) == "http://default@[::1]:8123/canonical"


def test_tls_follows_the_scheme_on_any_port(tmp_path):
    cfg = _config(tmp_path, "https://default@db.local:9443")
    assert serve.url(cfg) == "https://default@db.local:9443/canonical"


def test_serve_hands_the_files_and_url_to_the_executor(tmp_path, monkeypatch):
    from dactopus_ossie_clickhouse import cli as executor

    cfg = _config(tmp_path, "http://default@127.0.0.1:8123")
    root = _library(tmp_path)
    calls = []
    monkeypatch.setattr(executor, "main", lambda argv: calls.append(argv) or 0)
    assert serve.serve(cfg, root) == 0
    assert calls == [
        [
            "serve",
            str(root / "entities/web_analytics.yaml"),
            str(root / "entities/commerce.yaml"),
            "--url",
            "http://default@127.0.0.1:8123/canonical",
        ]
    ]


def test_serve_error_goes_to_stderr(tmp_path, monkeypatch, capsys):
    path = tmp_path / "dactopus.yaml"
    path.write_text(config.SAMPLE.replace("version: v0.5.3", f"path: {tmp_path}"))
    (tmp_path / "sources").mkdir()
    assert main(["serve", "--config", str(path)]) == 1
    captured = capsys.readouterr()
    assert captured.out == ""
    assert "dactopus serve: ga4: the models library has no" in captured.err


@pytest.mark.parametrize("package", ["ga4", "shopify"])
def test_model_of_each_package_loads(tmp_path, models_library, package):
    cfg = _config(tmp_path, "http://default@127.0.0.1:8123", (package,))
    load_model(*serve.model_files(cfg, models_library))


def test_models_of_both_packages_load_as_one(tmp_path, models_library):
    cfg = _config(tmp_path, "http://default@127.0.0.1:8123")
    load_model(*serve.model_files(cfg, models_library))
