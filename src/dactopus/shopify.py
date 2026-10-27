"""Loading Shopify orders and products from the GraphQL Admin API into the
Shopify package's input tables.

The queries and the mapping are those of export_shopify.py in the README of
dactopus-data-models ("Your store"), so the rows are the ones its recipe
loads. Bulk operations cannot read refund lines, so orders come in pages.
A deleted product is gone from the API; the store's events name it, and
the load appends a version with only its id and the time of the deletion.
"""

from __future__ import annotations

import functools
import importlib.resources
import json
import os
import ssl
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
import zoneinfo
from collections.abc import Callable
from decimal import Decimal
from pathlib import Path
from typing import Any
from zoneinfo import available_timezones

import certifi
from clickhouse_connect.driver.client import Client

from .config import Shopify

API_VERSION = "2026-10"
ORDER_TABLES = ("orders", "order_lines", "refunds", "refund_lines")
TABLES = (*ORDER_TABLES, "products")
# The first load reads every order, product and deletion: no store is older.
FIRST_LOAD_SINCE = "2000-01-01T00:00:00Z"
# The first release of the models library whose Shopify input has products.
PRODUCTS_SINCE = "v0.7.0"

MONEY = "{ shopMoney { amount } }"
QUERY = f"""query ($cursor: String, $filter: String) {{
  orders(first: 25, after: $cursor, query: $filter, sortKey: UPDATED_AT) {{
    pageInfo {{ hasNextPage endCursor }}
    nodes {{
      legacyResourceId name createdAt processedAt updatedAt cancelledAt closedAt test
      customer {{ legacyResourceId }} email currencyCode presentmentCurrencyCode
      displayFinancialStatus displayFulfillmentStatus sourceName taxesIncluded
      totalPriceSet {MONEY} totalTaxSet {MONEY} totalRefundedSet {MONEY}
      currentTotalPriceSet {MONEY} totalTipReceivedSet {MONEY}
      originalTotalDutiesSet {MONEY} originalTotalAdditionalFeesSet {MONEY}
      discountCodes shippingAddress {{ countryCodeV2 provinceCode city zip }}
      shippingLines(first: 50) {{ pageInfo {{ hasNextPage }} nodes {{
        discountedPriceSet {MONEY} taxLines {{ priceSet {MONEY} }} }} }}
      lineItems(first: 50) {{ pageInfo {{ hasNextPage }} nodes {{
        id product {{ legacyResourceId }} variant {{ legacyResourceId }}
        sku title variantTitle quantity currentQuantity
        originalUnitPriceSet {MONEY} originalTotalSet {MONEY}
        discountAllocations {{ allocatedAmountSet {MONEY} }}
        taxLines {{ rate priceSet {MONEY} }} taxable requiresShipping isGiftCard }} }}
      refunds {{
        legacyResourceId createdAt updatedAt note totalRefundedSet {MONEY}
        refundLineItems(first: 50) {{ pageInfo {{ hasNextPage }} nodes {{
          id lineItem {{ id }} quantity restockType subtotalSet {MONEY} totalTaxSet {MONEY} }} }} }}
    }}
  }}
}}"""

# Every product, 100 per page: archived ones and drafts come without a
# status filter (the development store on 2026-10-27, tests/fixtures).
PRODUCTS_QUERY = """query ($cursor: String, $filter: String) {
  products(first: 100, after: $cursor, query: $filter, sortKey: UPDATED_AT) {
    pageInfo { hasNextPage endCursor }
    nodes { legacyResourceId title productType vendor status isGiftCard updatedAt }
  }
}"""
# The deletions of products, from the store's events; the filter names the
# subject type and the action. Read on 2026-10-26 (API 2026-10) by an app
# with read_products and no further scope.
DELETIONS_QUERY = """query ($cursor: String, $filter: String) {
  events(first: 100, after: $cursor, query: $filter, sortKey: CREATED_AT) {
    pageInfo { hasNextPage endCursor }
    nodes { createdAt ... on BasicEvent { subjectId } }
  }
}"""
# The query of each connection the load reads, and the scope that reads it.
QUERIES = {
    "orders": (QUERY, "read_orders"),
    "products": (PRODUCTS_QUERY, "read_products"),
    "events": (DELETIONS_QUERY, "read_products"),
}

# The shop's time zone, Settings > General in the admin; read with API
# 2026-10 on 2026-10-23 by an app with read_orders.
ZONE_QUERY = "{ shop { ianaTimezone } }"

# A page of a connection ("orders", "products", "events") for the variables.
Fetch = Callable[[str, dict[str, Any]], dict[str, Any]]
# The data of a query for the variables; the string is the next action when
# the API refuses the query.
Post = Callable[[str, dict[str, Any], str], dict[str, Any]]


class LoadError(Exception):
    """A load cannot go on; the message says which step and what to do."""


@functools.cache
def _tls() -> ssl.SSLContext:
    # Python from python.org on macOS does not see the system's root certificates.
    return ssl.create_default_context(cafile=certifi.where())


def _post(url: str, data: bytes, headers: dict[str, str]) -> dict[str, Any]:
    request = urllib.request.Request(url, data, headers)
    with urllib.request.urlopen(request, context=_tls(), timeout=60) as response:
        return json.load(response)


def access_token(package: Shopify, environ: dict[str, str] | None = None) -> str:
    """A token from the client credentials grant; it lasts 24 hours, longer than a run."""
    environ = os.environ if environ is None else environ
    missing = [
        name for name in (package.client_id_env, package.client_secret_env) if not environ.get(name)
    ]
    if missing:
        raise LoadError(f"shopify: the environment variable {', '.join(missing)} is not set.")
    form = urllib.parse.urlencode(
        {
            "grant_type": "client_credentials",
            "client_id": environ[package.client_id_env],
            "client_secret": environ[package.client_secret_env],
        }
    ).encode()
    url = f"https://{package.shop}.myshopify.com/admin/oauth/access_token"
    try:
        body = _post(url, form, {"Content-Type": "application/x-www-form-urlencoded"})
    except urllib.error.HTTPError as exc:
        raise LoadError(
            f"shopify: {package.shop}.myshopify.com refused the app's client id and "
            f"secret ({exc.code}): check them and that the app is installed on the store."
        ) from None
    except OSError as exc:
        raise LoadError(_unreachable(package.shop, exc)) from None
    return body["access_token"]


def _unreachable(shop: str, exc: OSError) -> str:
    # URLError and timeouts: the network, not the store, failed.
    reason = getattr(exc, "reason", exc)
    return (
        f"shopify: cannot reach {shop}.myshopify.com ({reason}); check the network and run again."
    )


def _graphql(shop: str, token: str) -> Post:
    """The data of a query to the GraphQL Admin API, waiting out throttling.

    ``refused`` is the next action when the API answers with an HTTP error.
    """
    url = f"https://{shop}.myshopify.com/admin/api/{API_VERSION}/graphql.json"
    headers = {"X-Shopify-Access-Token": token, "Content-Type": "application/json"}

    def post(query: str, variables: dict[str, Any], refused: str) -> dict[str, Any]:
        payload = json.dumps({"query": query, "variables": variables}).encode()
        while True:
            try:
                body = _post(url, payload, headers)
            except urllib.error.HTTPError as exc:
                raise LoadError(
                    f"shopify: the Admin API of {shop}.myshopify.com answered {exc.code}; {refused}"
                ) from None
            except OSError as exc:
                raise LoadError(_unreachable(shop, exc)) from None
            errors = body.get("errors", [])
            if any(e.get("extensions", {}).get("code") == "THROTTLED" for e in errors):
                time.sleep(2)
                continue
            if errors:
                raise LoadError(f"shopify: the Admin API returned errors: {errors}")
            return body["data"]

    return post


def graphql(shop: str, token: str) -> Fetch:
    """A page of orders, products or deletion events from the GraphQL Admin API."""
    post = _graphql(shop, token)

    def fetch(field: str, variables: dict[str, Any]) -> dict[str, Any]:
        query, scope = QUERIES[field]
        refused = f"check that the app has {scope}. Run again to resume."
        return post(query, variables, refused)[field]

    return fetch


def check_time_zone(shop: str, token: str, zone: str) -> None:
    """Refuse a store.timezone other than the shop's: the orders' days would be off."""
    refused = (
        "check that the app is installed on the store and run again; nothing has been loaded yet."
    )
    found = _graphql(shop, token)(ZONE_QUERY, {}, refused)["shop"]["ianaTimezone"]
    if not _same_zone(zone, found):
        raise LoadError(
            f"shopify: the time zone of {shop}.myshopify.com is {found}, and store.timezone "
            f"is {zone}; set store.timezone to {found}, the shop's (Settings > General in "
            "the Shopify admin)."
        )


def _same_zone(zone: str, found: str) -> bool:
    """Whether two names are one zone: the same name, or links to the same
    tz data (UTC and Etc/UTC, Europe/Kiev and Europe/Kyiv), whose days are
    the same.

    Both names are read from one tz database: the machine's may predate a
    name that the tzdata package has, and two releases of a zone's data
    differ in bytes.
    """
    if zone == found:
        return True
    if found not in available_timezones():
        return False
    roots = [*map(Path, zoneinfo.TZPATH), importlib.resources.files("tzdata.zoneinfo")]
    for root in roots:
        data = _tzif(root, zone)
        if data is not None and data == _tzif(root, found):
            return True
    return False


def _tzif(root: Any, zone: str) -> bytes | None:
    """The tz data of a zone in one database; a link reads as its target."""
    path = root.joinpath(zone)
    try:
        return path.read_bytes() if path.is_file() else None
    except OSError:
        return None


def _money(v):
    return str(Decimal(v["shopMoney"]["amount"])) if v else "0"


def _total(items, key):
    return str(sum((Decimal(i[key]["shopMoney"]["amount"]) for i in items), Decimal(0)))


def _legacy(gid):
    return int(gid.rsplit("/", 1)[1])


def _ts(v):
    return v and v.replace("T", " ").rstrip("Z")


def _nodes(conn, what):
    if conn["pageInfo"]["hasNextPage"]:
        raise LoadError(
            f"shopify: more than 50 {what} in one order or refund; the runner reads 50."
        )
    return conn["nodes"]


def rows(order: dict[str, Any]) -> dict[str, list[dict[str, Any]]]:
    """The input rows of one order: the order, its lines, refunds and refund lines."""
    tables: dict[str, list[dict[str, Any]]] = {t: [] for t in ORDER_TABLES}
    o = order
    oid, updated = int(o["legacyResourceId"]), _ts(o["updatedAt"])
    ship = _nodes(o["shippingLines"], "shipping lines")
    addr = o["shippingAddress"] or {}
    tables["orders"].append(
        {
            "id": oid,
            "name": o["name"],
            "created_at": _ts(o["createdAt"]),
            "processed_at": _ts(o["processedAt"]),
            "updated_at": updated,
            "cancelled_at": _ts(o["cancelledAt"]),
            "closed_at": _ts(o["closedAt"]),
            "test": o["test"],
            "customer_id": o["customer"] and int(o["customer"]["legacyResourceId"]),
            "email": o["email"],
            "currency_code": o["currencyCode"],
            "presentment_currency_code": o["presentmentCurrencyCode"],
            "display_financial_status": o["displayFinancialStatus"],
            "display_fulfillment_status": o["displayFulfillmentStatus"],
            "source_name": o["sourceName"],
            "taxes_included": o["taxesIncluded"],
            "total_price": _money(o["totalPriceSet"]),
            "shipping_price": _total(ship, "discountedPriceSet"),
            "shipping_tax": _total([t for s in ship for t in s["taxLines"]], "priceSet"),
            "total_tax": _money(o["totalTaxSet"]),
            "total_refunded": _money(o["totalRefundedSet"]),
            "current_total_price": _money(o["currentTotalPriceSet"]),
            "total_tip": _money(o["totalTipReceivedSet"]),
            "total_duties": _money(o["originalTotalDutiesSet"]),
            "total_additional_fees": _money(o["originalTotalAdditionalFeesSet"]),
            "discount_codes": o["discountCodes"],
            "shipping_country_code": addr.get("countryCodeV2"),
            "shipping_province_code": addr.get("provinceCode"),
            "shipping_city": addr.get("city"),
            "shipping_zip": addr.get("zip"),
        }
    )
    for li in _nodes(o["lineItems"], "line items"):
        tables["order_lines"].append(
            {
                "id": _legacy(li["id"]),
                "order_id": oid,
                "order_updated_at": updated,
                "product_id": li["product"] and int(li["product"]["legacyResourceId"]),
                "variant_id": li["variant"] and int(li["variant"]["legacyResourceId"]),
                "sku": li["sku"],
                "title": li["title"],
                "variant_title": li["variantTitle"],
                "quantity": li["quantity"],
                "current_quantity": li["currentQuantity"],
                "original_unit_price": _money(li["originalUnitPriceSet"]),
                "original_total": _money(li["originalTotalSet"]),
                "discount_allocated": _total(li["discountAllocations"], "allocatedAmountSet"),
                "total_tax": _total(li["taxLines"], "priceSet"),
                "tax_rate": str(sum((Decimal(str(t["rate"])) for t in li["taxLines"]), Decimal(0))),
                "taxable": li["taxable"],
                "requires_shipping": li["requiresShipping"],
                "is_gift_card": li["isGiftCard"],
            }
        )
    for r in o["refunds"]:
        rid, refund_updated = int(r["legacyResourceId"]), _ts(r["updatedAt"])
        tables["refunds"].append(
            {
                "id": rid,
                "order_id": oid,
                "created_at": _ts(r["createdAt"]),
                "updated_at": refund_updated,
                "note": r["note"],
                "total_refunded": _money(r["totalRefundedSet"]),
            }
        )
        for rl in _nodes(r["refundLineItems"], "refund lines"):
            tables["refund_lines"].append(
                {
                    "id": _legacy(rl["id"]),
                    "refund_id": rid,
                    "refund_updated_at": refund_updated,
                    "line_item_id": _legacy(rl["lineItem"]["id"]),
                    "quantity": rl["quantity"],
                    "subtotal": _money(rl["subtotalSet"]),
                    "total_tax": _money(rl["totalTaxSet"]),
                    "restock_type": rl["restockType"],
                }
            )
    return tables


def product_row(product: dict[str, Any]) -> dict[str, Any]:
    """The input row of one product, as it is now."""
    p = product
    return {
        "id": int(p["legacyResourceId"]),
        "title": p["title"],
        "product_type": p["productType"],
        "vendor": p["vendor"],
        "status": p["status"],
        "is_gift_card": p["isGiftCard"],
        "updated_at": _ts(p["updatedAt"]),
    }


def deletion_row(event: dict[str, Any]) -> dict[str, Any]:
    """The version of a deleted product: its id and the time of the deletion.

    The package reads nothing else of it; the other columns get their
    defaults, as in the recipe.
    """
    at = _ts(event["createdAt"])
    return {"id": _legacy(event["subjectId"]), "updated_at": at, "deleted_at": at}


def since(client: Client, database: str, table: str) -> str:
    """An hour before the latest change loaded into a table, or the first load's start."""
    return client.query(
        f"SELECT if(count() = 0, {{first:String}}, formatDateTime("
        f"max(updated_at) - INTERVAL 1 HOUR, '%Y-%m-%dT%H:%i:%SZ')) FROM `{database}`.`{table}`",
        parameters={"first": FIRST_LOAD_SINCE},
    ).result_rows[0][0]


def _pages(fetch: Fetch, field: str, filter: str):
    page = {"pageInfo": {"hasNextPage": True, "endCursor": None}}
    while page["pageInfo"]["hasNextPage"]:
        page = fetch(field, {"cursor": page["pageInfo"]["endCursor"], "filter": filter})
        yield page["nodes"]


def _require_products(client: Client, database: str) -> None:
    """Refuse a models library whose Shopify input has no products, before any load."""
    if not client.command(f"EXISTS TABLE `{database}`.products"):
        raise LoadError(
            f"shopify: the input of the models library has no products table; set "
            f"models.version, or the checkout at models.path, to {PRODUCTS_SINCE} or later; "
            "nothing has been loaded yet."
        )


def _insert(client: Client, database: str, table: str, rows: list[dict[str, Any]]) -> None:
    block = "".join(json.dumps(row) + "\n" for row in rows).encode()
    client.raw_insert(f"`{database}`.`{table}`", insert_block=block, fmt="JSONEachRow")


def load(client: Client, fetch: Fetch, database: str) -> dict[str, int]:
    """Append the orders and products updated since the last load, page by page.

    A page's lines and refunds go in before its orders, so the latest
    order loaded, which the next load starts from, always has them. The
    products come after the orders, then the deletions of products since
    the same time: a deleted product's updated_at is the deletion's time,
    so the next load starts from it too.
    Returns the number of rows appended to each table.
    """
    _require_products(client, database)
    counts = dict.fromkeys(TABLES, 0)
    start = since(client, database, "orders")
    for orders in _pages(fetch, "orders", f"updated_at:>='{start}'"):
        tables: dict[str, list[dict[str, Any]]] = {t: [] for t in ORDER_TABLES}
        for order in orders:
            for name, found in rows(order).items():
                tables[name].extend(found)
        for name in ("order_lines", "refund_lines", "refunds", "orders"):
            if tables[name]:
                _insert(client, database, name, tables[name])
                counts[name] += len(tables[name])
        print(f"shopify: {len(orders)} orders since {start}", file=sys.stderr)
    start = since(client, database, "products")
    for products in _pages(fetch, "products", f"updated_at:>='{start}'"):
        if products:
            _insert(client, database, "products", [product_row(p) for p in products])
            counts["products"] += len(products)
        print(f"shopify: {len(products)} products since {start}", file=sys.stderr)
    deleted = f"subject_type:PRODUCT AND action:destroy AND created_at:>='{start}'"
    for events in _pages(fetch, "events", deleted):
        if events:
            _insert(client, database, "products", [deletion_row(e) for e in events])
            counts["products"] += len(events)
        print(f"shopify: {len(events)} deleted products since {start}", file=sys.stderr)
    return counts
