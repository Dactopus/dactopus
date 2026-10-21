from pathlib import Path

import pyarrow as pa
import pyarrow.ipc as ipc
import pytest
from google.cloud.bigquery_storage import types
from google.cloud.bigquery_storage_v1 import reader

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


class FakeReadClient:
    """The Storage Read API over one table: it compresses only when asked, as BigQuery does."""

    def __init__(self, table: pa.Table):
        self.table = table
        self.requests: list[types.ReadSession] = []
        self.open = False

    def __enter__(self):
        self.open = True
        return self

    def __exit__(self, *exc):
        self.open = False

    def create_read_session(self, parent, read_session, max_stream_count):
        assert self.open
        self.requests.append(read_session)
        schema = self.table.select(list(read_session.read_options.selected_fields)).schema
        return types.ReadSession(
            streams=[types.ReadStream(name="stream-0")],
            arrow_schema=types.ArrowSchema(serialized_schema=schema.serialize().to_pybytes()),
        )

    def read_rows(self, name):
        assert self.open
        session = self.requests[-1]
        codec = session.read_options.arrow_serialization_options.buffer_compression
        lz4 = codec == types.ArrowSerializationOptions.CompressionCodec.LZ4_FRAME
        rows = self.table.select(list(session.read_options.selected_fields))
        sink = pa.BufferOutputStream()
        options = ipc.IpcWriteOptions(compression="lz4" if lz4 else None)
        with ipc.new_stream(sink, rows.schema, options=options) as writer:
            writer.write_table(rows, max_chunksize=1)
        batches = list(ipc.MessageReader.open_stream(sink.getvalue()))[1:]

        class Gapic:
            def read_rows(self, read_stream, offset, **kwargs):
                # A message may carry no rows, only the stream's statistics.
                yield types.ReadRowsResponse(row_count=0)
                for batch in batches:
                    yield types.ReadRowsResponse(
                        row_count=1,
                        arrow_record_batch=types.ArrowRecordBatch(
                            serialized_record_batch=batch.serialize().to_pybytes()
                        ),
                    )

        return reader.ReadRowsStream(Gapic(), name, 0, {})


def _bigquery_export(table: pa.Table) -> tuple[ga4.BigQueryExport, list[FakeReadClient]]:
    """A BigQueryExport over a fake Storage Read API, and the clients it opened."""
    export = object.__new__(ga4.BigQueryExport)
    export._project, export._source_project, export._dataset = "p", "src", "ds"
    export.account = "a test account"
    clients: list[FakeReadClient] = []

    def new_client():
        clients.append(FakeReadClient(table))
        return clients[-1]

    export._read_client = new_client
    return export, clients


COLUMNS = ["event_date", "event_name", "event_params", "ecommerce"]


# The stream's own to_arrow warns that it is deprecated; the export decodes the rows itself.
@pytest.mark.filterwarnings("error::PendingDeprecationWarning")
def test_export_reads_each_day_compressed_with_a_client_of_its_own():
    rows = [_event("20201101", "page_view", "a"), _event("20201101", "purchase", "b", 25.5)]
    table = pa.Table.from_pylist(rows, schema=SCHEMA)
    export, clients = _bigquery_export(table)

    for day in ["20201101", "20201102"]:
        assert export.read(day, COLUMNS).equals(table.select(COLUMNS))
    assert len(clients) == 2 and not any(client.open for client in clients)
    for client in clients:
        (request,) = client.requests
        assert (
            request.read_options.arrow_serialization_options.buffer_compression
            == types.ArrowSerializationOptions.CompressionCodec.LZ4_FRAME
        )


def test_export_reads_a_stream_without_rows_as_an_empty_day():
    export, _ = _bigquery_export(pa.Table.from_pylist([], schema=SCHEMA))
    rows = export.read("20201101", COLUMNS)
    assert rows.num_rows == 0 and rows.column_names == COLUMNS


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
    wrong = GA4(project="p", dataset="a.b", credentials_file=None, vars={"ga4_lookback_days": "x"})
    with pytest.raises(ga4.LoadError, match="whole number of days"):
        ga4.lookback_days(wrong, tmp_path)


def test_gcloud_login_has_the_configured_project_pay(monkeypatch):
    import google.auth
    from google.oauth2.credentials import Credentials

    monkeypatch.setattr(google.auth, "default", lambda scopes: (Credentials("t"), None))
    package = GA4(project="dactopus-ga4-sample", dataset="a.b", credentials_file=None, vars={})
    credentials, account = ga4.credentials_for(package, environ={})
    assert credentials.quota_project_id == "dactopus-ga4-sample"
    assert account == "the gcloud application-default login"


def test_key_from_the_environment_is_named(monkeypatch):
    import google.auth

    class Key:
        service_account_email = "reader@other-project.iam.gserviceaccount.com"

    monkeypatch.setattr(google.auth, "default", lambda scopes: (Key(), "other-project"))
    package = GA4(project="p", dataset="a.b", credentials_file=None, vars={})
    _, account = ga4.credentials_for(package, environ={"GOOGLE_APPLICATION_CREDENTIALS": "k"})
    assert "reader@other-project" in account
    assert "from GOOGLE_APPLICATION_CREDENTIALS; unset it" in account
