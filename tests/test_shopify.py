import io
import json
import re
import sys
import urllib.error
from pathlib import Path

import pytest

from dactopus import clickhouse as ch
from dactopus import shopify
from dactopus.config import Shopify

# Pages answering shopify.QUERY: seven orders of the development store,
# recorded on 2026-10-21 through the Shopify connector in Claude, whose API
# version is older than 2026-10. Two edits: one email replaced, and #1004's
# displayFulfillmentStatus set to what 2026-10 returns for it,
# FULFILLMENT_NOT_REQUIRED (a live load on the same day). The rest equals
# that live load. Partial and whole refunds, a cancellation, taxes included,
# a custom item, a gift card, a tip refunded without lines, import duties.
PAGES = json.loads((Path(__file__).parent / "fixtures/shopify_orders.json").read_text())


class FakeAPI:
    def __init__(self, pages):
        self.pages = pages
        self.calls = []

    def __call__(self, variables):
        self.calls.append(variables)
        return self.pages[len(self.calls) - 1]


@pytest.fixture
def shopify_input(clickhouse, models_library):
    client, database = clickhouse
    ch.create_input(client, models_library, "shopify", database)
    return client, database


def _table(client, database, name):
    columns = [
        row[0]
        for row in client.query(
            "SELECT name FROM system.columns WHERE database = {db:String} AND table = {t:String}"
            " AND name != 'loaded_at' ORDER BY position",
            parameters={"db": database, "t": name},
        ).result_rows
    ]
    listed = ", ".join(f"`{c}`" for c in columns)
    return client.query(f"SELECT {listed} FROM `{database}`.`{name}` ORDER BY {listed}").result_rows


def test_orders_are_loaded_page_by_page(shopify_input):
    client, database = shopify_input
    api = FakeAPI(PAGES)
    counts = shopify.load(client, api, database)
    assert counts == {"orders": 7, "order_lines": 10, "refunds": 4, "refund_lines": 3}
    assert api.calls == [
        {"cursor": None, "filter": "updated_at:>='2000-01-01T00:00:00Z'"},
        {"cursor": "page-2", "filter": "updated_at:>='2000-01-01T00:00:00Z'"},
    ]
    tip = [row for row in _table(client, database, "orders") if row[1] == "#1017"][0]
    assert tip[0] == 18928835854514


def test_next_load_starts_an_hour_before_the_latest_change(shopify_input):
    client, database = shopify_input
    shopify.load(client, FakeAPI(PAGES), database)
    api = FakeAPI(PAGES[1:])
    shopify.load(client, api, database)
    assert api.calls == [{"cursor": None, "filter": "updated_at:>='2026-10-06T11:18:22Z'"}]
    # The versions delivered again are appended; the package keeps the latest.
    assert len(_table(client, database, "orders")) == 7 + 3


def _recipe_script(library: Path) -> str:
    readme = (library / "README.md").read_text()
    block = readme.split("<summary>export_shopify.py</summary>", 1)[1]
    code = re.search(r"```python\n(.*?)```", block, re.S).group(1)
    return "\n".join(line.removeprefix("   ") for line in code.splitlines())


def test_rows_equal_the_readme_recipe(shopify_input, models_library, tmp_path, monkeypatch):
    """The runner loads what export_shopify.py and its load step load."""
    client, database = shopify_input
    shopify.load(client, FakeAPI(PAGES), database)

    pages = iter(PAGES)
    monkeypatch.setattr(
        "urllib.request.urlopen",
        lambda request, context=None: io.BytesIO(
            json.dumps({"data": {"orders": next(pages)}}).encode()
        ),
    )
    monkeypatch.setenv("SHOPIFY_SHOP", "dactopus-dev")
    monkeypatch.setenv("SHOPIFY_TOKEN", "token")
    monkeypatch.setattr(sys, "argv", ["export_shopify.py", "2000-01-01T00:00:00Z", str(tmp_path)])
    exec(compile(_recipe_script(models_library), "export_shopify.py", "exec"), {})

    recipe = f"{database}_recipe"
    ch.create_input(client, models_library, "shopify", recipe)
    try:
        for name in shopify.TABLES:
            block = (tmp_path / f"{name}.jsonl").read_bytes()
            if block:
                client.raw_insert(f"`{recipe}`.`{name}`", insert_block=block, fmt="JSONEachRow")
        for name in shopify.TABLES:
            assert _table(client, database, name) == _table(client, recipe, name), name
    finally:
        client.command(f"DROP DATABASE IF EXISTS `{recipe}`")


def test_more_lines_than_a_page_holds_is_refused():
    order = json.loads(json.dumps(PAGES[0]["nodes"][0]))
    order["lineItems"]["pageInfo"]["hasNextPage"] = True
    with pytest.raises(shopify.LoadError, match="more than 50 line items"):
        shopify.rows(order)


def test_missing_app_secret_is_named():
    package = Shopify(shop="dactopus-dev", client_id_env="ID", client_secret_env="SECRET", vars={})
    with pytest.raises(shopify.LoadError, match="SECRET is not set"):
        shopify.access_token(package, environ={"ID": "x"})


def test_unreachable_store_says_run_again(monkeypatch):
    def offline(url, data, headers):
        raise urllib.error.URLError("nodename nor servname provided")

    monkeypatch.setattr(shopify, "_post", offline)
    with pytest.raises(
        shopify.LoadError, match="cannot reach dactopus-dev.myshopify.com.*run again"
    ):
        shopify.graphql("dactopus-dev", "token")({"cursor": None, "filter": ""})


# The answer to shopify.ZONE_QUERY on the development store, through the
# Shopify connector in Claude on 2026-10-23, whose API version is older
# than 2026-10; the runner's own 2026-10 request read the same zone that day.
SHOP_ZONE = {"data": {"shop": {"ianaTimezone": "America/New_York"}}}


def test_the_shop_time_zone_passes(monkeypatch):
    sent = []
    monkeypatch.setattr(
        shopify, "_post", lambda url, data, headers: sent.append(json.loads(data)) or SHOP_ZONE
    )
    shopify.check_time_zone("dactopus-dev", "token", "America/New_York")
    assert sent == [{"query": shopify.ZONE_QUERY, "variables": {}}]


@pytest.mark.parametrize(("shop", "store"), [("Etc/UTC", "UTC"), ("Europe/Kiev", "Europe/Kyiv")])
def test_another_name_of_the_shop_time_zone_passes(monkeypatch, shop, store):
    answer = {"data": {"shop": {"ianaTimezone": shop}}}
    monkeypatch.setattr(shopify, "_post", lambda url, data, headers: answer)
    shopify.check_time_zone("dactopus-dev", "token", store)


def test_the_shop_time_zone_is_known_without_the_machine_database(monkeypatch, no_system_zones):
    answer = {"data": {"shop": {"ianaTimezone": "Europe/Kiev"}}}
    monkeypatch.setattr(shopify, "_post", lambda url, data, headers: answer)
    shopify.check_time_zone("dactopus-dev", "token", "Europe/Kyiv")


def test_another_time_zone_than_the_shop_is_refused(monkeypatch):
    monkeypatch.setattr(shopify, "_post", lambda url, data, headers: SHOP_ZONE)
    with pytest.raises(shopify.LoadError) as refused:
        shopify.check_time_zone("dactopus-dev", "token", "Europe/Berlin")
    assert str(refused.value) == (
        "shopify: the time zone of dactopus-dev.myshopify.com is America/New_York, and "
        "store.timezone is Europe/Berlin; set store.timezone to America/New_York, the shop's "
        "(Settings > General in the Shopify admin)."
    )


def test_a_refused_time_zone_query_says_nothing_was_loaded(monkeypatch):
    def refuse(url, data, headers):
        raise urllib.error.HTTPError(url, 403, "Forbidden", {}, None)

    monkeypatch.setattr(shopify, "_post", refuse)
    with pytest.raises(shopify.LoadError) as refused:
        shopify.check_time_zone("dactopus-dev", "token", "America/New_York")
    assert str(refused.value) == (
        "shopify: the Admin API of dactopus-dev.myshopify.com answered 403; check that the app "
        "is installed on the store and run again; nothing has been loaded yet."
    )
