"""The report a store owner reads: three answers first, details below."""

from __future__ import annotations

from datetime import date, datetime
from pathlib import Path

from .checks import PAST_WEEKS, SEVERITIES, Finding, Volume
from .config import Config

FILE = "report.md"

_NAMES = {"ga4": "GA4", "shopify": "Shopify"}


def render(config: Config, findings: list[Finding], volumes: list[Volume], now: datetime) -> str:
    lines = ["# Dactopus report", "", f"{now:%Y-%m-%d %H:%M} UTC, {config.path}", ""]
    lines += _freshness(findings)
    lines += _checks(findings)
    lines += _volumes(volumes)
    lines += _details(findings)
    return "\n".join(lines).rstrip() + "\n"


def write(config: Config, text: str) -> Path:
    config.workdir.mkdir(exist_ok=True)
    path = config.workdir / FILE
    path.write_text(text)
    return path


def _freshness(findings: list[Finding]) -> list[str]:
    lines = ["## How fresh the data is", ""]
    fresh = [f for f in findings if f.check == "freshness"]
    if not fresh:
        lines.append("Unknown: no freshness results.")
    for finding in fresh:
        lines.append(f"- {_mark(finding.severity)}{finding.sentence}")
    return [*lines, ""]


def _checks(findings: list[Finding]) -> list[str]:
    lines = ["## Did every check pass", ""]
    counted = {s: sum(1 for f in findings if f.severity == s) for s in SEVERITIES}
    total = len(findings)
    bad = [f for f in findings if f.severity in ("warn", "fail", "error")]
    if not findings:
        lines.append("No checks ran: reinstall dactopus.")
    elif not bad and not counted["skip"]:
        lines.append("Yes: the check passed." if total == 1 else f"Yes: all {total} checks passed.")
    else:
        parts = []
        if counted["fail"]:
            parts.append(f"{counted['fail']} failed")
        if counted["error"]:
            parts.append(f"{counted['error']} with an error")
        if counted["warn"]:
            parts.append(f"{counted['warn']} warned")
        if counted["skip"]:
            parts.append(f"{counted['skip']} did not run because an earlier step failed")
        lines.append(f"No: of {total} checks, {', '.join(parts)}.")
        lines.append("")
        for finding in sorted(bad, key=lambda f: -SEVERITIES.index(f.severity)):
            lines.append(f"- {_mark(finding.severity)}{finding.sentence}")
    return [*lines, ""]


def _volumes(volumes: list[Volume]) -> list[str]:
    lines = ["## Rows per day", ""]
    if not volumes:
        lines.append("No tables to count.")
    for package in dict.fromkeys(v.package for v in volumes):
        own = [v for v in volumes if v.package == package]
        lines.append(f"### {_NAMES.get(package, package)}")
        lines.append("")
        with_rows = [v for v in own if v.latest is not None]
        for empty in (v for v in own if v.latest is None):
            if empty.error:
                lines.append(f"{empty.table} could not be counted: {empty.error}")
            elif empty.first is not None:
                lines.append(f"{empty.table} has rows only for today so far.")
            else:
                lines.append(f"{empty.table} has no rows.")
        if not with_rows:
            lines.append("")
            continue
        if any(v.latest is None for v in own):
            lines.append("")
        days = sorted({day for v in with_rows for day in v.days})
        lines.append(
            "The last 7 days that are over, with the median of the same weekday over the "
            f"{PAST_WEEKS} weeks before in parentheses."
        )
        lines.append("")
        lines.append("| Day | " + " | ".join(v.table for v in with_rows) + " |")
        lines.append("| --- | " + " | ".join("---:" for _ in with_rows) + " |")
        for day in days:
            cells = [_cell(v, day) for v in with_rows]
            lines.append(f"| {day:%Y-%m-%d %a} | " + " | ".join(cells) + " |")
        lines.append("")
    return lines


def _cell(volume: Volume, day: date) -> str:
    if day not in volume.days:
        return ""
    rows = f"{volume.days[day]:,}"
    typical = volume.typical.get(day)
    return rows if typical is None else f"{rows} ({typical:,})"


def _details(findings: list[Finding]) -> list[str]:
    lines = ["## Details", ""]
    if not findings:
        lines.append("No checks ran.")
    for check in dict.fromkeys(f.check for f in findings):
        lines.append(f"### {check}")
        lines.append("")
        for finding in (f for f in findings if f.check == check):
            line = f"- {finding.severity}: {finding.object}: {finding.sentence}"
            if finding.severity != "pass":
                line += f" (observed {finding.observed}; expected {finding.expected})"
            lines.append(line)
        lines.append("")
    return lines


def _mark(severity: str) -> str:
    return "" if severity == "pass" else f"{severity}: "
