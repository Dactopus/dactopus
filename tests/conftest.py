import os
import uuid
import zoneinfo
from urllib.parse import urlsplit

import pytest

from dactopus import config, library
from dactopus.config import Models

# The release the loaders are tested against; its input.sql is the contract.
MODELS = Models(version="v0.8.0", path=None)

# The test server, named as clickhouse.url names it (user, host and port),
# with the password in CLICKHOUSE_PASSWORD, the variable the sample
# configuration names.
SERVER_URL = os.environ.get("DACTOPUS_CLICKHOUSE_URL", "http://127.0.0.1:8123")


@pytest.fixture(scope="session")
def _server():
    import clickhouse_connect
    from clickhouse_connect.driver.exceptions import ClickHouseError

    url = urlsplit(SERVER_URL)
    try:
        return clickhouse_connect.get_client(
            host=url.hostname,
            port=url.port or 8123,
            username=url.username or "default",
            password=os.environ.get("CLICKHOUSE_PASSWORD", ""),
        )
    except ClickHouseError as exc:
        pytest.skip(f"no ClickHouse server: {exc}")


@pytest.fixture
def clickhouse(_server, request):
    """A client and a fresh database, dropped after the test."""
    request.node.add_marker(pytest.mark.integration)
    database = f"dactopus_test_{uuid.uuid4().hex[:8]}"
    yield _server, database
    _server.command(f"DROP DATABASE IF EXISTS `{database}`")


@pytest.fixture
def server_url():
    """The test server's URL, without the password."""
    return SERVER_URL


@pytest.fixture
def sample(monkeypatch):
    """The sample configuration, connected to the test server."""
    url = "url: http://default@127.0.0.1:8123"
    password = "# password_env: CLICKHOUSE_PASSWORD"
    assert url in config.SAMPLE and password in config.SAMPLE
    monkeypatch.setenv("CLICKHOUSE_PASSWORD", os.environ.get("CLICKHOUSE_PASSWORD", ""))
    return config.SAMPLE.replace(url, f"url: {SERVER_URL}").replace(
        password, password.removeprefix("# ")
    )


@pytest.fixture
def no_system_zones():
    """A machine without its own tz database: zone names come from tzdata only."""
    zoneinfo.reset_tzpath(to=[])
    yield
    zoneinfo.reset_tzpath()


@pytest.fixture(scope="session")
def models_library():
    """The models library release, downloaded once into the user's cache."""
    return library.resolve(MODELS)


def pytest_collection_modifyitems(items):
    for item in items:
        if "clickhouse" in getattr(item, "fixturenames", ()):
            item.add_marker(pytest.mark.integration)
