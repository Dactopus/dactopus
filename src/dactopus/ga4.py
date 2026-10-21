"""Loading the GA4 BigQuery export into the GA4 package's input table."""

from __future__ import annotations

import os
import re
import sys
from datetime import date, timedelta
from functools import partial
from pathlib import Path
from typing import Protocol

import pyarrow as pa
import yaml
from clickhouse_connect.driver.client import Client

from .config import GA4

TABLE = "events"
_DAILY = re.compile(r"events_(\d{8})")
_SCOPES = ["https://www.googleapis.com/auth/bigquery"]
_API = "https://bigquery.googleapis.com/bigquery/v2"


class LoadError(Exception):
    """A load cannot go on; the message says which step and what to do."""


class Export(Protocol):
    """The daily tables of a GA4 export."""

    def days(self) -> list[str]:
        """The dates (YYYYMMDD) of the daily tables, in order."""

    def columns(self, day: str) -> set[str]:
        """The top-level columns of a daily table."""

    def read(self, day: str, columns: list[str]) -> pa.Table:
        """The rows of a daily table, with the given top-level columns."""


def lookback_days(package: GA4, library: Path) -> int:
    """ga4_lookback_days from the configuration, else the package's default."""
    if "ga4_lookback_days" in package.vars:
        try:
            return int(package.vars["ga4_lookback_days"])
        except (TypeError, ValueError):
            raise LoadError(
                "ga4: packages.ga4.vars.ga4_lookback_days: expected a whole number of days."
            ) from None
    project = yaml.safe_load((library / "sources/ga4/dbt_project.yml").read_text())
    return int(project["vars"]["ga4_lookback_days"])


def load(client: Client, export: Export, database: str, lookback: int) -> int:
    """Load the days not yet loaded and reread the last lookback days.

    A reread day replaces what the input holds for it; the days go in
    order, so a load stopped halfway is picked up by the next one.
    Returns the number of rows inserted.
    """
    # query, not command: command parses the result, and 20201101 would become a number.
    latest = client.query(f"SELECT max(event_date) FROM `{database}`.`{TABLE}`").result_rows[0][0]
    days = export.days()
    if latest:
        start = date.fromisoformat(latest) - timedelta(days=lookback)
        days = [day for day in days if date.fromisoformat(day) >= start]
    wanted = [
        row[0]
        for row in client.query(
            "SELECT name FROM system.columns WHERE database = {db:String} AND table = {t:String}"
            " ORDER BY position",
            parameters={"db": database, "t": TABLE},
        ).result_rows
    ]
    inserted = 0
    absent_reported: set[str] = set()
    for day in days:
        available = export.columns(day)
        columns = [name for name in wanted if name in available]
        absent = set(wanted) - available - absent_reported
        if absent:
            # Older exports lack session_traffic_source_last_click, for one.
            print(
                f"ga4: the export has no {', '.join(sorted(absent))} from {day}; "
                "the input gets the column's default (NULL).",
                file=sys.stderr,
            )
            absent_reported |= absent
        rows = export.read(day, columns)
        client.command(
            f"DELETE FROM `{database}`.`{TABLE}` WHERE event_date = {{day:String}}",
            parameters={"day": day},
        )
        if rows.num_rows:
            client.insert_arrow(TABLE, rows, database=database)
        inserted += rows.num_rows
        print(f"ga4: {day}: {rows.num_rows} rows", file=sys.stderr)
    return inserted


def credentials_for(package: GA4, environ: dict[str, str] | None = None):
    """The Google credentials of a load and who they are, in words.

    A key named in the configuration wins; otherwise Application Default
    Credentials, where GOOGLE_APPLICATION_CREDENTIALS wins over the gcloud
    login. A gcloud login has the configured project pay for the reads.
    """
    import google.auth
    from google.auth.exceptions import DefaultCredentialsError
    from google.oauth2 import credentials as user_credentials
    from google.oauth2 import service_account

    environ = os.environ if environ is None else environ
    if package.credentials_file is not None:
        try:
            credentials = service_account.Credentials.from_service_account_file(
                str(package.credentials_file), scopes=_SCOPES
            )
        except (OSError, ValueError) as exc:
            raise LoadError(
                f"ga4: cannot read the service account key "
                f"packages.ga4.credentials_file ({package.credentials_file}): {exc}"
            ) from None
        return credentials, (
            f"service account {credentials.service_account_email} "
            "from packages.ga4.credentials_file"
        )
    try:
        credentials, _ = google.auth.default(scopes=_SCOPES)
    except DefaultCredentialsError:
        raise LoadError(
            "ga4: no Google credentials. Run `gcloud auth application-default "
            "login`, or name a service account key in packages.ga4.credentials_file."
        ) from None
    if isinstance(credentials, user_credentials.Credentials):
        return credentials.with_quota_project(package.project), (
            "the gcloud application-default login"
        )
    email = getattr(credentials, "service_account_email", "an unnamed account")
    if environ.get("GOOGLE_APPLICATION_CREDENTIALS"):
        return credentials, (
            f"service account {email} from GOOGLE_APPLICATION_CREDENTIALS; unset it "
            "to use the gcloud login, or name a key in packages.ga4.credentials_file"
        )
    return credentials, f"{email}, the default credentials of this machine"


class BigQueryExport:
    """A GA4 export in BigQuery, read with the Storage Read API.

    The read sessions run in the configured project, which pays for them.
    """

    def __init__(self, package: GA4, environ: dict[str, str] | None = None):
        from google.auth.transport.requests import AuthorizedSession
        from google.cloud.bigquery_storage import BigQueryReadClient

        credentials, self.account = credentials_for(package, environ)
        print(f"ga4: reading as {self.account}", file=sys.stderr)
        self._project = package.project
        self._source_project, self._dataset = package.dataset.split(".")
        self._rest = AuthorizedSession(credentials)
        self._read_client = partial(BigQueryReadClient, credentials=credentials)

    def _get(self, path: str, **params: str) -> dict:
        from requests import RequestException

        url = f"{_API}/projects/{self._source_project}/datasets/{self._dataset}{path}"
        try:
            response = self._rest.get(url, params=params, timeout=60)
        except RequestException as exc:
            raise LoadError(
                f"ga4: cannot reach BigQuery ({exc}); check the network and run again."
            ) from None
        if not response.ok:
            try:
                reason = response.json()["error"]["message"]
            except (ValueError, KeyError, TypeError):
                reason = response.reason
            raise LoadError(
                f"ga4: BigQuery refused to read {self._source_project}.{self._dataset} "
                f"as {self.account} ({response.status_code}: {reason})."
            )
        return response.json()

    def days(self) -> list[str]:
        days, token = [], None
        while True:
            paging = {"pageToken": token} if token else {}
            page = self._get("/tables", maxResults="1000", **paging)
            for table in page.get("tables", []):
                match = _DAILY.fullmatch(table["tableReference"]["tableId"])
                if match:
                    days.append(match.group(1))
            token = page.get("nextPageToken")
            if not token:
                return sorted(days)

    def columns(self, day: str) -> set[str]:
        table = self._get(f"/tables/events_{day}", fields="schema/fields/name")
        return {field["name"] for field in table["schema"]["fields"]}

    def read(self, day: str, columns: list[str]) -> pa.Table:
        from google.api_core.exceptions import GoogleAPICallError
        from google.cloud.bigquery_storage import types

        # Uncompressed, a day of the sample read four to ten times slower over
        # the network; google-cloud-bigquery asks for LZ4 too.
        session = types.ReadSession(
            table=(f"projects/{self._source_project}/datasets/{self._dataset}/tables/events_{day}"),
            data_format=types.DataFormat.ARROW,
            read_options=types.ReadSession.TableReadOptions(
                selected_fields=columns,
                arrow_serialization_options=types.ArrowSerializationOptions(
                    buffer_compression=types.ArrowSerializationOptions.CompressionCodec.LZ4_FRAME
                ),
            ),
        )
        # A client reused over many days read them slower and slower (2 s a day
        # at first, 8 s by the twelfth); google-cloud-bigquery opens one per read too.
        with self._read_client() as client:
            try:
                session = client.create_read_session(
                    parent=f"projects/{self._project}", read_session=session, max_stream_count=1
                )
            except GoogleAPICallError as exc:
                raise LoadError(
                    f"ga4: BigQuery refused a read session in project {self._project} as "
                    f"{self.account} ({exc.code}: {exc.message})."
                ) from None
            if not session.streams:
                return pa.table({})
            # Decoded here: the stream's own to_arrow is deprecated in favour of
            # pandas-gbq, which brings pandas and google-cloud-bigquery.
            schema = pa.ipc.read_schema(pa.py_buffer(session.arrow_schema.serialized_schema))
            try:
                batches = [
                    pa.ipc.read_record_batch(
                        pa.py_buffer(message.arrow_record_batch.serialized_record_batch), schema
                    )
                    for message in client.read_rows(session.streams[0].name)
                ]
            except GoogleAPICallError as exc:
                raise LoadError(
                    f"ga4: reading events_{day} failed ({exc.code}: {exc.message}); run again "
                    "to resume from this day."
                ) from None
        return pa.Table.from_batches(batches, schema)
