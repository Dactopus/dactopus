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
# Pages answering shopify.PRODUCTS_QUERY and shopify.DELETIONS_QUERY: the
# eighteen products of the development store and the one deletion in its
# events (a draft created and deleted on 2026-10-26), recorded on 2026-10-27
# through the same connector; the recorded page of 18 is split in two.
CATALOG = json.loads((Path(__file__).parent / "fixtures/shopify_products.json").read_text())
ALL = {"orders": PAGES, **CATALOG}


class FakeAPI:
    """Pages per connection, served in order; every call is recorded."""

    def __init__(self, pages):
        self.pages = {field: list(found) for field, found in pages.items()}
        self.calls = []

    def __call__(self, field, variables):
        self.calls.append((field, variables))
        return self.pages[field].pop(0)


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
    api = FakeAPI(ALL)
    counts = shopify.load(client, api, database)
    assert counts == {
        "orders": 7,
        "order_lines": 10,
        "refunds": 4,
        "refund_lines": 3,
        "products": 18 + 1,
    }
    first = "updated_at:>='2000-01-01T00:00:00Z'"
    assert api.calls == [
        ("orders", {"cursor": None, "filter": first}),
        ("orders", {"cursor": "page-2", "filter": first}),
        ("products", {"cursor": None, "filter": first}),
        ("products", {"cursor": "products-2", "filter": first}),
        (
            "events",
            {
                "cursor": None,
                "filter": "subject_type:PRODUCT AND action:destroy AND "
                "created_at:>='2000-01-01T00:00:00Z'",
            },
        ),
    ]
    tip = [row for row in _table(client, database, "orders") if row[1] == "#1017"][0]
    assert tip[0] == 18928835854514
    deleted = [row for row in _table(client, database, "products") if row[0] == 15441296064690]
    # id, title, product_type, vendor, status, is_gift_card, updated_at, deleted_at
    assert len(deleted) == 1
    assert deleted[0][1:6] == ("", "", "", "", False)
    assert deleted[0][6] == deleted[0][7]


def test_next_load_starts_an_hour_before_the_latest_change(shopify_input):
    client, database = shopify_input
    shopify.load(client, FakeAPI(ALL), database)
    changed = {
        "orders": PAGES[1:],
        "products": [
            {"pageInfo": {"hasNextPage": False}, "nodes": CATALOG["products"][1]["nodes"][-3:]}
        ],
        "events": CATALOG["events"],
    }
    api = FakeAPI(changed)
    shopify.load(client, api, database)
    # The orders from an hour before the latest order's change; the products
    # and the deletions from an hour before the latest product's change,
    # which here is later than the deletion.
    assert api.calls == [
        ("orders", {"cursor": None, "filter": "updated_at:>='2026-10-06T11:18:22Z'"}),
        ("products", {"cursor": None, "filter": "updated_at:>='2026-10-26T11:41:14Z'"}),
        (
            "events",
            {
                "cursor": None,
                "filter": "subject_type:PRODUCT AND action:destroy AND "
                "created_at:>='2026-10-26T11:41:14Z'",
            },
        ),
    ]
    # The versions delivered again are appended; the package keeps the latest.
    assert len(_table(client, database, "orders")) == 7 + 3
    assert len(_table(client, database, "products")) == 19 + 4


def test_a_library_without_products_is_refused_before_loading(shopify_input):
    """v0.6.x has deployment.yml, which the runner checks, but no products input."""
    client, database = shopify_input
    client.command(f"DROP TABLE `{database}`.products")
    api = FakeAPI(ALL)
    with pytest.raises(shopify.LoadError) as refused:
        shopify.load(client, api, database)
    assert str(refused.value) == (
        "shopify: the input of the models library has no products table; set models.version, "
        "or the checkout at models.path, to v0.7.0 or later; nothing has been loaded yet."
    )
    assert api.calls == []
    assert _table(client, database, "orders") == []


def _recipe_script(library: Path) -> str:
    readme = (library / "README.md").read_text()
    block = readme.split("<summary>export_shopify.py</summary>", 1)[1]
    code = re.search(r"```python\n(.*?)```", block, re.S).group(1)
    return "\n".join(line.removeprefix("   ") for line in code.splitlines())


def test_rows_equal_the_readme_recipe(shopify_input, models_library, tmp_path, monkeypatch):
    """The runner loads what export_shopify.py and its load step load."""
    client, database = shopify_input
    shopify.load(client, FakeAPI(ALL), database)

    # The recipe reads the orders, then the products, then the deletions.
    pages = iter(
        [(field, page) for field in ("orders", "products", "events") for page in ALL[field]]
    )

    def answer(request, context=None):
        field, page = next(pages)
        assert f"{field}(" in json.loads(request.data)["query"]
        return io.BytesIO(json.dumps({"data": {field: page}}).encode())

    monkeypatch.setattr("urllib.request.urlopen", answer)
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


def test_product_rows_follow_the_contract():
    product = CATALOG["products"][1]["nodes"][1]  # the gift card
    assert shopify.product_row(product) == {
        "id": 15408877666482,
        "title": "Gift Card",
        "product_type": "giftcard",
        "vendor": "Snowboard Vendor",
        "status": "ACTIVE",
        "is_gift_card": True,
        "updated_at": "2026-10-06 03:59:37",
    }
    assert shopify.deletion_row(CATALOG["events"][0]["nodes"][0]) == {
        "id": 15441296064690,
        "updated_at": "2026-10-26 08:57:04",
        "deleted_at": "2026-10-26 08:57:04",
    }


def test_a_refused_products_query_names_the_scope(monkeypatch):
    def refuse(url, data, headers):
        raise urllib.error.HTTPError(url, 403, "Forbidden", {}, None)

    monkeypatch.setattr(shopify, "_post", refuse)
    fetch = shopify.graphql("dactopus-dev", "token")
    with pytest.raises(
        shopify.LoadError, match="answered 403; check that the app has read_products"
    ):
        fetch("products", {"cursor": None, "filter": ""})
    with pytest.raises(shopify.LoadError, match="check that the app has read_orders"):
        fetch("orders", {"cursor": None, "filter": ""})


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
        shopify.graphql("dactopus-dev", "token")("orders", {"cursor": None, "filter": ""})


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
