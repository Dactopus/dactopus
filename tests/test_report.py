from datetime import UTC, date, datetime, timedelta
from pathlib import Path

import yaml

from dactopus import config, report
from dactopus.checks import Finding, Volume

# The deployment.yml of the packages and unions of dactopus-data-models v0.12.2.
FIXTURE_LIBRARY = Path(__file__).parent / "fixtures" / "library"

NOW = datetime(2026, 10, 22, 7, 30, tzinfo=UTC)


def _config(tmp_path, **sources):
    """A Shopify store main_store, then the sources given."""
    path = tmp_path / "dactopus.yaml"
    path.write_text(
        yaml.safe_dump(
            {
                "clickhouse": {"url": "http://db.local:8123", "database": "canonical"},
                "timezone": "UTC",
                "models": {"path": str(FIXTURE_LIBRARY)},
                "sources": {"main_store": {"package": "shopify", "shop": "s"}, **sources},
            },
            sort_keys=False,
        )
    )
    return config.load(path, environ={})


def _volume(label, table, latest, rows, typical=None, error=None):
    days = {latest - timedelta(days=i): rows for i in range(6, -1, -1)}
    return Volume(
        label,
        table,
        latest - timedelta(days=40),
        latest,
        days,
        dict.fromkeys(days, typical),
        error,
    )


FRESH = Finding(
    "freshness", "orders", "pass", "latest", "within 1 day", "The input is current.", "main_store"
)
OK = Finding(
    "dbt",
    "orders.order_id",
    "pass",
    "0 rows",
    "0 rows",
    "No order_id repeats in orders.",
    "main_store",
)
FAIL = Finding(
    "dbt",
    "orders.order_id",
    "fail",
    "2 rows",
    "0 rows",
    "2 values of order_id repeat.",
    "main_store",
)
WARN = Finding(
    "rows per day",
    "orders",
    "warn",
    "no rows on 2026-10-20",
    "rows every day",
    "No orders on 2026-10-20.",
    "main_store",
)
SKIP = Finding(
    "dbt",
    "refunds",
    "skip",
    "skipped",
    "built",
    "refunds was not built because an earlier step failed.",
    "main_store",
)


def test_three_answers_come_first_when_all_is_well(tmp_path):
    cfg = _config(tmp_path)
    latest = date(2026, 10, 21)
    text = report.render(cfg, [FRESH, OK], [_volume("main_store", "orders", latest, 12, 10)], NOW)
    lines = text.splitlines()
    assert lines[0] == "# Dactopus report"
    assert lines[2] == f"2026-10-22 07:30 UTC, {cfg.path}"
    assert [line for line in lines if line.startswith("## ")] == [
        "## How fresh the data is",
        "## Did every check pass",
        "## Rows per day",
        "## Details",
    ]
    assert "- main_store: The input is current." in lines
    assert "Yes: all 2 checks passed." in lines
    assert "### main_store" in lines
    assert "| Day | orders |" in lines
    assert "| 2026-10-21 Wed | 12 (10) |" in lines
    assert "- pass: orders.order_id: No order_id repeats in orders." in lines


def test_a_single_check_is_said_in_the_singular(tmp_path):
    text = report.render(_config(tmp_path), [OK], [], NOW)
    assert "Yes: the check passed." in text.splitlines()


def test_what_did_not_pass_is_listed_worst_first(tmp_path):
    cfg = _config(tmp_path)
    text = report.render(cfg, [FRESH, OK, WARN, FAIL, SKIP], [], NOW)
    lines = text.splitlines()
    i = lines.index(
        "No: of 5 checks, 1 failed, 1 warned, 1 did not run because an earlier step failed."
    )
    assert lines[i + 2 : i + 4] == [
        "- fail: main_store: 2 values of order_id repeat.",
        "- warn: main_store: No orders on 2026-10-20.",
    ]
    assert "No tables to count." in lines
    assert (
        "- fail: orders.order_id: 2 values of order_id repeat. (observed 2 rows; expected 0 rows)"
        in lines
    )


def test_tables_without_rows_or_missing_are_said_so(tmp_path):
    cfg = _config(tmp_path)
    latest = date(2026, 10, 21)
    volumes = [
        _volume("main_store", "orders", latest, 3),
        Volume("main_store", "refunds", None, None, {}, {}),
        Volume("main_store", "sales", latest, None, {}, {}),
        Volume("website", "events", None, None, {}, {}, "Code: 60. Unknown table"),
    ]
    text = report.render(cfg, [], volumes, NOW)
    assert "refunds has no rows." in text
    assert "sales has rows only for today so far." in text
    assert "events could not be counted: Code: 60. Unknown table" in text
    assert "| 2026-10-21 Wed | 3 |" in text
    assert "No checks ran: reinstall dactopus." in text


def test_the_report_is_written_next_to_dbt_output(tmp_path):
    cfg = _config(tmp_path)
    path = report.write(cfg, "# Dactopus report\n")
    assert path == cfg.workdir / "report.md"
    assert path.read_text() == "# Dactopus report\n"


def test_findings_and_volumes_are_grouped_by_source(tmp_path):
    """In the order of the configuration, the findings of no source last."""
    cfg = _config(tmp_path, website={"package": "ga4", "project": "p", "dataset": "a.b"})
    latest = date(2026, 10, 21)
    plugin = Finding("mine", "installation", "pass", "1", "1", "Fine.")
    website = Finding(
        "freshness", "events", "pass", "latest", "within 2 days", "Current.", "website"
    )
    volumes = [_volume("main_store", "orders", latest, 3), _volume("website", "events", latest, 9)]
    lines = report.render(cfg, [plugin, website, FRESH, OK], volumes, NOW).splitlines()
    details = lines[lines.index("## Details") :]
    assert [line for line in details if line.startswith("### ")] == [
        "### main_store",
        "### website",
        "### The installation",
    ]
    shop = details.index("### main_store")
    assert details[shop + 2 : shop + 5] == [
        "#### freshness",
        "",
        "- pass: orders: The input is current.",
    ]
    assert "- website: Current." in lines
    rows = lines[lines.index("## Rows per day") : lines.index("## Details")]
    assert [line for line in rows if line.startswith("### ")] == ["### main_store", "### website"]


def test_a_source_in_another_zone_than_the_installation_says_so(tmp_path):
    cfg = _config(
        tmp_path,
        europe_store={"package": "woocommerce", "timezone": "Europe/Berlin"},
    )
    latest = date(2026, 10, 21)
    volumes = [
        _volume("main_store", "orders", latest, 3),
        _volume("europe_store", "orders", latest, 4),
    ]
    lines = report.render(cfg, [], volumes, NOW).splitlines()
    at = lines.index("### europe_store")
    assert lines[at + 2] == (
        'Its days are in Europe/Berlin; "today" in an agent\'s question is the date in UTC.'
    )
    assert lines[lines.index("### main_store") + 2].startswith("The last 7 days")


def test_a_package_that_fixes_its_zone_says_so_in_it(tmp_path):
    cfg = _config(tmp_path, network={"package": "sensor_community"})
    cfg_berlin = config.load(_rewrite(cfg, "Europe/Berlin"), environ={})
    latest = date(2026, 10, 21)
    lines = report.render(cfg_berlin, [], [_volume("network", "readings", latest, 4)], NOW)
    at = lines.splitlines().index("### network")
    assert lines.splitlines()[at + 2] == (
        'Its days are in UTC; "today" in an agent\'s question is the date in Europe/Berlin.'
    )
    # In an installation in UTC the note is left out.
    plain = report.render(cfg, [], [_volume("network", "readings", latest, 4)], NOW)
    assert "Its days are in" not in plain


def _rewrite(cfg, zone):
    """The configuration file of cfg with another installation time zone."""
    text = cfg.path.read_text().replace("timezone: UTC", f"timezone: {zone}")
    cfg.path.write_text(text)
    return cfg.path


def test_a_union_is_named_in_words(tmp_path):
    """A union's key (union-commerce) is for programs; the owner reads its name."""
    cfg = _config(tmp_path)
    failed = Finding(
        "dbt", "orders", "fail", "2 rows", "0 rows", "2 orders repeat.", "union-commerce"
    )
    lines = report.render(cfg, [FRESH, OK, failed], [], NOW).splitlines()
    assert "- fail: the commerce union: 2 orders repeat." in lines
    details = lines[lines.index("## Details") :]
    assert [line for line in details if line.startswith("### ")] == [
        "### main_store",
        "### the commerce union",
    ]
