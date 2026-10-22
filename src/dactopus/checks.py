"""Checks over the input and the canonical tables, and their findings.

A check is a function of a ClickHouse client and the configuration that
returns findings. Checks register through the entry point group
``dactopus.checks``; the entry point's name is the check's name. The
built-in checks register the same way, in pyproject.toml.
"""

from __future__ import annotations

import json
import re
import statistics
from collections.abc import Callable
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from importlib.metadata import entry_points
from typing import Any, Literal

from clickhouse_connect.driver.client import Client
from clickhouse_connect.driver.exceptions import ClickHouseError

from .build import target
from .config import Config

Severity = Literal["pass", "skip", "warn", "fail", "error"]

# From the least to the most serious.
SEVERITIES: tuple[Severity, ...] = ("pass", "skip", "warn", "fail", "error")

ENTRY_POINT_GROUP = "dactopus.checks"


@dataclass(frozen=True)
class Finding:
    """One result of a check, in the same shape for built-in and plugin checks."""

    check: str
    # The table, column or test the finding is about.
    object: str
    severity: Severity
    observed: str
    expected: str
    # One sentence a store owner can read.
    sentence: str


Check = Callable[[Client, Config], list[Finding]]


def run_all(client: Client, config: Config) -> list[Finding]:
    """Run every registered check; a check that breaks becomes an error finding."""
    findings: list[Finding] = []
    for entry in sorted(entry_points(group=ENTRY_POINT_GROUP), key=lambda e: e.name):
        try:
            check: Check = entry.load()
            findings.extend(check(client, config))
        except Exception as exc:  # noqa: BLE001 - a plugin's fault must not hide the others
            findings.append(
                Finding(
                    check=entry.name,
                    object=entry.value,
                    severity="error",
                    observed=f"{type(exc).__name__}: {exc}",
                    expected="the check runs",
                    sentence=f"The check {entry.name} could not run: {exc}",
                )
            )
    return findings


def failed(findings: list[Finding]) -> bool:
    """Whether the run should exit non-zero: a fail or an error among the findings."""
    return any(finding.severity in ("fail", "error") for finding in findings)


# dbt results: target/run_results.json with manifest.json for what each node is.

_STATUS: dict[str, Severity] = {
    "pass": "pass",
    "success": "pass",
    "warn": "warn",
    "fail": "fail",
    "error": "error",
    "skipped": "skip",
}

_REF = re.compile(r"""ref\(\s*['"]([^'"]+)['"]\s*\)""")

# The status of exposures and saved queries in `dbt build`: nothing ran,
# so there is nothing to report (the source of dbt 1.11.16, 2026-10-22).
_NOT_RUN = "no-op"


def dbt_results(client: Client, config: Config) -> list[Finding]:
    """The models, tests and unit tests of the last dbt run of each package."""
    findings: list[Finding] = []
    for package in config.packages():
        where = target(config, package)
        try:
            results = json.loads((where / "run_results.json").read_text())
            manifest = json.loads((where / "manifest.json").read_text())
        except FileNotFoundError:
            findings.append(
                Finding(
                    check="dbt",
                    object=package,
                    severity="error",
                    observed="no dbt results",
                    expected="a dbt run of the package",
                    sentence=f"The {package} package has not been built yet: run `dactopus run`.",
                )
            )
            continue
        for result in results["results"]:
            if result["status"] != _NOT_RUN:
                findings.append(_dbt_finding(result, manifest))
    return findings


def _dbt_finding(result: dict[str, Any], manifest: dict[str, Any]) -> Finding:
    unique_id = result["unique_id"]
    kind = unique_id.split(".", 1)[0]
    status = result["status"]
    severity = _STATUS.get(status, "error")
    message = result.get("message") or status
    failures = result.get("failures")
    observed = status if failures is None else f"{failures} rows"
    if kind == "model":
        table = manifest["nodes"][unique_id]["name"]
        sentence = {
            "pass": f"{table} was built.",
            "skip": f"{table} was not built because an earlier step failed.",
        }.get(severity, f"{table} could not be built: {message}")
        return Finding("dbt", table, severity, status, "built", sentence)
    if kind == "unit_test":
        unit = manifest["unit_tests"][unique_id]
        where = f"the unit test {unit['name']} of {unit['model']}"
        sentence = {
            "pass": f"{_cap(where)} passes.",
            "skip": f"{_cap(where)} did not run because an earlier step failed.",
            "fail": f"{_cap(where)} fails: the package's logic, not the data; report it.",
        }.get(severity, f"{_cap(where)} could not run: {message}")
        return Finding("dbt", unit["model"], severity, status, "pass", sentence)
    if kind == "test":
        node = manifest["nodes"][unique_id]
        return _test_finding(node, manifest, severity, status, message, failures, observed)
    return Finding("dbt", unique_id, severity, status, "pass", f"{unique_id}: {message}")


def _test_finding(
    node: dict[str, Any],
    manifest: dict[str, Any],
    severity: Severity,
    status: str,
    message: str,
    failures: int | None,
    observed: str,
) -> Finding:
    nodes = manifest["nodes"]
    metadata = node.get("test_metadata")
    column = node.get("column_name")
    n = failures or 0
    if metadata is None:
        # A singular test: a query in the package's tests/ that must return no rows.
        models = [nodes[d]["name"] for d in node["depends_on"]["nodes"] if d.startswith("model.")]
        table = models[0] if models else node["name"]
        what = node["name"].replace("_", " ")
        where = f"the check {what} on {', '.join(models) or table}"
        ok = f"{_cap(where)} passes."
        bad = f"{_n(n, 'row')} of {table} {_verb(n, 'fails', 'fail')} the check {what}."
        obj = table
    else:
        kind = metadata["name"]
        kwargs = metadata.get("kwargs", {})
        attached = node.get("attached_node")
        table = nodes[attached]["name"] if attached else "a table"
        obj = f"{table}.{column}" if column else table
        where = f"the {kind} test on {obj}"
        if kind == "not_null":
            ok = f"{column} is set in every row of {table}."
            bad = f"{column} is missing in {_n(n, 'row')} of {table}."
        elif kind == "unique":
            ok = f"No {column} repeats in {table}."
            bad = (
                f"{_n(n, 'value')} of {column} {_verb(n, 'appears', 'appear')} in more "
                f"than one row of {table}."
            )
        elif kind == "accepted_values":
            values = ", ".join(str(v) for v in kwargs.get("values", []))
            ok = f"Every {column} of {table} is one of {values}."
            bad = (
                f"{_n(n, 'value')} of {column} in {table} {_verb(n, 'is', 'are')} not among "
                f"{values}."
            )
        elif kind == "relationships":
            match = _REF.search(str(kwargs.get("to", "")))
            to = match.group(1) if match else str(kwargs.get("to"))
            field = kwargs.get("field", column)
            ok = f"Every {column} of {table} exists as {field} in {to}."
            bad = (
                f"{_n(n, 'row')} of {table} {_verb(n, 'has', 'have')} no matching {field} in {to}."
            )
        else:
            ok = f"{_cap(where)} passes."
            bad = f"{_n(n, 'row')} {_verb(n, 'fails', 'fail')} {where}."
    sentence = {
        "pass": ok,
        "warn": bad,
        "fail": bad,
        "skip": f"{_cap(where)} did not run because an earlier step failed.",
    }.get(severity, f"{_cap(where)} could not run: {message}")
    return Finding("dbt", obj, severity, observed, "0 rows", sentence)


def _cap(text: str) -> str:
    return text[:1].upper() + text[1:]


def _n(count: int, noun: str) -> str:
    return f"{count:,} {noun}{'' if count == 1 else 's'}"


def _verb(count: int, singular: str, plural: str) -> str:
    return singular if count == 1 else plural


# Input freshness: target/sources.json of `dbt source freshness`; the package
# sets the thresholds in its sources.yml.

_FRESHNESS: dict[str, Severity] = {"pass": "pass", "warn": "warn", "error": "error"}


def freshness(client: Client, config: Config) -> list[Finding]:
    """How old the latest input row of each package is, against the package's thresholds."""
    findings: list[Finding] = []
    for package in config.packages():
        path = target(config, package) / "sources.json"
        try:
            results = json.loads(path.read_text())["results"]
        except FileNotFoundError:
            findings.append(
                Finding(
                    check="freshness",
                    object=package,
                    severity="error",
                    observed="no freshness results",
                    expected="a freshness run of the package",
                    sentence=f"The freshness of the {package} input is unknown: "
                    "run `dactopus run`.",
                )
            )
            continue
        if not results:
            # dbt 1.11.16 writes an empty list when the source table is missing
            # (2026-10-22).
            findings.append(
                Finding(
                    check="freshness",
                    object=package,
                    severity="error",
                    observed="no source table",
                    expected="the input table of the package",
                    sentence=f"The freshness of the {package} input could not be measured: "
                    "its input table is missing; see the dbt output and run `dactopus run`.",
                )
            )
        for result in results:
            findings.append(_freshness_finding(package, result))
    return findings


def _freshness_finding(package: str, result: dict[str, Any]) -> Finding:
    table = result["unique_id"].rsplit(".", 1)[-1]
    status = result["status"]
    severity = _FRESHNESS.get(status, "error")
    criteria = result.get("criteria") or {}
    warn_after = _period(criteria.get("warn_after"))
    error_after = _period(criteria.get("error_after"))
    expected = f"within {warn_after or 'any time'}"
    if error_after:
        expected += f", {error_after} at most"
    if severity == "error" and status != "error":
        return Finding(
            "freshness",
            table,
            "error",
            status,
            expected,
            f"The freshness of {table} ({package}) could not be measured: "
            f"{result.get('error') or status}",
        )
    latest = datetime.fromisoformat(result["max_loaded_at"])
    age = ago(result["max_loaded_at_time_ago_in_s"])
    observed = f"latest {latest:%Y-%m-%d %H:%M} UTC, {age} ago"
    if severity == "pass":
        sentence = (
            f"The {package} input is current: the latest row of {table} is {age} old "
            f"({latest:%Y-%m-%d})."
        )
    elif severity == "warn":
        sentence = (
            f"No new rows of {table} ({package}) for {age}, the latest from "
            f"{latest:%Y-%m-%d}; the package expects them within {warn_after}."
        )
    else:
        sentence = (
            f"No new rows of {table} ({package}) for {age}, the latest from "
            f"{latest:%Y-%m-%d}; the package allows {error_after} at most. "
            "Check that the source still exports and the load runs."
        )
    return Finding("freshness", table, severity, observed, expected, sentence)


def _period(spec: dict[str, Any] | None) -> str | None:
    if not spec or spec.get("count") is None:
        return None
    count, period = spec["count"], spec["period"]
    return f"{count} {period}{'s' if count != 1 else ''}"


def ago(seconds: float) -> str:
    """An age in words: a minute, 5 minutes, 3 hours, 2 days."""
    minutes = seconds / 60
    if minutes < 2:
        return "a minute"
    if minutes < 120:
        return f"{minutes:.0f} minutes"
    hours = minutes / 60
    if hours < 48:
        return f"{hours:.0f} hours"
    return f"{hours / 24:.0f} days"


# Rows per day: the canonical tables counted by their date column, over the
# last 7 days with rows against the 4 weeks before. The first table of a
# package is the one a day without rows is a finding for: a day without
# events or orders is a gap, a day without refunds is not.

DAILY: dict[str, tuple[tuple[str, str], ...]] = {
    "ga4": (("events", "event_date"), ("sessions", "session_date"), ("purchases", "purchase_date")),
    "shopify": (("orders", "order_date"), ("refunds", "refund_date")),
}

WEEK = 7
PAST_WEEKS = 4


@dataclass(frozen=True)
class Volume:
    """Rows per day of one table over its last week, with what past weeks had."""

    package: str
    table: str
    first: date | None
    latest: date | None
    # The last 7 days ending at latest, 0 for a day without rows.
    days: dict[date, int]
    # Per day of the week: the median of the same weekday over the 4 weeks
    # before, None when the table has no history that far.
    typical: dict[date, int | None]
    # Why the table could not be counted, when it could not.
    error: str | None = None

    @property
    def gaps(self) -> list[date]:
        return [day for day, rows in self.days.items() if rows == 0]


def volumes(client: Client, config: Config) -> list[Volume]:
    """Rows per day of each counted table of the configured packages."""
    found: list[Volume] = []
    database = config.clickhouse.database
    for package in config.packages():
        for table, column in DAILY.get(package, ()):
            try:
                found.append(_volume(client, database, package, table, column))
            except ClickHouseError as exc:
                found.append(Volume(package, table, None, None, {}, {}, _first_line(exc)))
    return found


def _first_line(exc: Exception) -> str:
    return str(exc).splitlines()[0]


def _volume(client: Client, database: str, package: str, table: str, column: str) -> Volume:
    qualified = f"`{database}`.`{table}`"
    rows, first, latest = client.query(
        f"SELECT count(), min(`{column}`), max(`{column}`) FROM {qualified}"
    ).result_rows[0]
    if not rows:
        return Volume(package, table, None, None, {}, {})
    span = WEEK * (PAST_WEEKS + 1)
    counted = dict(
        client.query(
            f"SELECT `{column}` AS day, count() FROM {qualified} "
            f"WHERE `{column}` > {{latest:Date}} - INTERVAL {span} DAY GROUP BY day",
            parameters={"latest": latest},
        ).result_rows
    )
    window = [latest - timedelta(days=i) for i in range(WEEK - 1, -1, -1)]
    days = {day: int(counted.get(day, 0)) for day in window if day >= first}
    typical: dict[date, int | None] = {}
    for day in days:
        past = [day - timedelta(weeks=w) for w in range(1, PAST_WEEKS + 1)]
        past = [p for p in past if p >= first]
        typical[day] = int(statistics.median(counted.get(p, 0) for p in past)) if past else None
    return Volume(package, table, first, latest, days, typical)


def rows_per_day(client: Client, config: Config) -> list[Finding]:
    """A day without rows in the last week of a package's first table."""
    findings: list[Finding] = []
    for package in config.packages():
        tables = DAILY.get(package)
        if not tables:
            continue
        table, column = tables[0]
        try:
            volume = _volume(client, config.clickhouse.database, package, table, column)
        except ClickHouseError as exc:
            findings.append(
                Finding(
                    "rows per day",
                    table,
                    "error",
                    _first_line(exc),
                    "rows every day",
                    f"{table} ({package}) could not be counted; build it with `dactopus run`.",
                )
            )
            continue
        if volume.latest is None:
            findings.append(
                Finding(
                    "rows per day",
                    table,
                    "warn",
                    "no rows",
                    "rows every day",
                    f"{table} ({package}) has no rows yet.",
                )
            )
            continue
        gaps = volume.gaps
        first_day = min(volume.days)
        if gaps:
            others = [rows for rows in volume.days.values() if rows]
            spread = (
                f"{min(others):,}"
                if min(others) == max(others)
                else f"{min(others):,} to {max(others):,}"
            )
            findings.append(
                Finding(
                    "rows per day",
                    table,
                    "warn",
                    f"no rows on {', '.join(day.isoformat() for day in gaps)}",
                    "rows every day",
                    f"{table} ({package}) has no rows for {_days(gaps)}; the other days "
                    f"of that week have {spread}.",
                )
            )
        else:
            findings.append(
                Finding(
                    "rows per day",
                    table,
                    "pass",
                    f"rows on every day from {first_day} to {volume.latest}",
                    "rows every day",
                    f"{table} ({package}) has rows on every day from {first_day} "
                    f"to {volume.latest}.",
                )
            )
    return findings


def _days(days: list[date]) -> str:
    if len(days) == 1:
        return days[0].isoformat()
    return ", ".join(day.isoformat() for day in days[:-1]) + f" and {days[-1].isoformat()}"
