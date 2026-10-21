from pathlib import Path

import pyarrow as pa
import pyarrow.ipc as ipc
import pytest

from dactopus import clickhouse as ch
from dactopus import ga4
from dactopus.config import GA4

# The Arrow schema the BigQuery export returns: every top-level column, the
# package's and others, as read from ga4_obfuscated_sample_ecommerce on
# 2026-10-21. The rows in these tests are made up.
SCHEMA = ipc.open_file(Path(__file__).parent / "fixtures/ga4_export_schema.arrow").schema


def _event(day: str, name: str, user: str, revenue: float | None = None) -> dict:
    return {
        "event_date": day,
        "event_timestamp": 1_604_188_800_000_000,
        "event_name": name,
        "event_params": [
            {"key": "ga_session_id", "value": {"int_value": 42}},
            {"key": "page_title", "value": {"string_value": "Home"}},
        ],
        "user_pseudo_id": user,
        "device": {"category": "mobile", "operating_system": "iOS"},
        "geo": {"country": "Canada", "city": "Toronto"},
        "ecommerce": {"purchase_revenue": revenue, "transaction_id": "T1" if revenue else None},
        "platform": "WEB",
    }


class FakeExport:
    def __init__(self, days: dict[str, list[dict]]):
        self.tables = {day: pa.Table.from_pylist(rows, schema=SCHEMA) for day, rows in days.items()}
        self.reads: list[str] = []

    def days(self):
        return sorted(self.tables)

    def columns(self, day):
        return set(self.tables[day].column_names)

    def read(self, day, columns):
        self.reads.append(day)
        return self.tables[day].select(columns)


def _events(client, database):
    return client.query(
        f"SELECT event_date, event_name, user_pseudo_id, event_params, device, geo, ecommerce, "
        f"session_traffic_source_last_click FROM `{database}`.events "
        f"ORDER BY event_date, event_name, user_pseudo_id"
    ).result_rows


@pytest.fixture
def ga4_input(clickhouse, models_library):
    client, database = clickhouse
    ch.create_input(client, models_library, "ga4", database)
    return client, database


def test_export_is_loaded_into_the_input(ga4_input, capsys):
    client, database = ga4_input
    export = FakeExport(
        {
            "20201101": [_event("20201101", "page_view", "a")],
            "20201102": [_event("20201102", "purchase", "b", 25.5)],
        }
    )
    assert ga4.load(client, export, database, lookback=3) == 2
    rows = _events(client, database)
    assert [row[:3] for row in rows] == [
        ("20201101", "page_view", "a"),
        ("20201102", "purchase", "b"),
    ]
    params, device, geo, ecommerce, last_click = rows[1][3:]
    assert params[0] == {
        "key": "ga_session_id",
        "value": {"string_value": None, "int_value": 42, "float_value": None, "double_value": None},
    }
    assert device == {"category": "mobile"}
    assert geo == {"country": "Canada"}
    assert ecommerce["purchase_revenue"] == 25.5 and ecommerce["transaction_id"] == "T1"
    # The sample is older than this column: the input gets NULLs.
    assert last_click == {
        "cross_channel_campaign": {"source": None, "medium": None, "campaign_name": None}
    }
    assert "no session_traffic_source_last_click" in capsys.readouterr().err


def test_next_load_rereads_the_lookback_and_replaces_it(ga4_input):
    client, database = ga4_input
    days = {f"2020110{d}": [_event(f"2020110{d}", "page_view", "a")] for d in range(1, 6)}
    ga4.load(client, FakeExport(days), database, lookback=3)
    before = _events(client, database)

    # The export changed the last days: one more event on the 4th.
    days["20201104"].append(_event("20201104", "purchase", "b", 10.0))
    days["20201106"] = [_event("20201106", "page_view", "c")]
    export = FakeExport(days)
    ga4.load(client, export, database, lookback=2)
    assert export.reads == ["20201103", "20201104", "20201105", "20201106"]
    after = _events(client, database)
    assert len(after) == len(before) + 2

    # A load over an unchanged export changes nothing.
    ga4.load(client, FakeExport(days), database, lookback=2)
    assert _events(client, database) == after


def test_lookback_from_configuration_or_package(tmp_path):
    (tmp_path / "sources/ga4").mkdir(parents=True)
    (tmp_path / "sources/ga4/dbt_project.yml").write_text("vars:\n  ga4_lookback_days: 3\n")
    configured = GA4(
        project="p", dataset="a.b", credentials_file=None, vars={"ga4_lookback_days": 5}
    )
    assert ga4.lookback_days(configured, tmp_path) == 5
    default = GA4(project="p", dataset="a.b", credentials_file=None, vars={})
    assert ga4.lookback_days(default, tmp_path) == 3
