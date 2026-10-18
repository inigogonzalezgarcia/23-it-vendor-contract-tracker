"""Command line: `demo` builds everything from a fictional register, `report` runs on your own CSV files."""
from __future__ import annotations

import argparse
import sys
from datetime import date
from pathlib import Path

from . import report, simulate
from .checks import Config, run_all
from .model import load, parse_date


def run(data: Path, out: Path, title: str, c: Config, as_of: date | None = None) -> dict:
    reg = load(data, as_of)
    findings = run_all(reg, c)
    out.mkdir(parents=True, exist_ok=True)
    (out / "report.html").write_text(report.render_html(reg, findings, title, c), encoding="utf-8")
    (out / "review.md").write_text(report.review_md(reg, findings), encoding="utf-8")
    (out / "renewals.ics").write_text(report.renewals_ics(reg), encoding="utf-8", newline="")
    report.write_findings(findings, out / "findings.csv", reg.currency)
    report.write_actions(findings, out / "actions.csv", reg.currency)
    return report.summary(reg, findings)


def _date(value: str) -> date:
    d = parse_date(value)
    if not d:
        raise argparse.ArgumentTypeError(f"not a date (YYYY-MM-DD): {value}")
    return d


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="vendortrack", description=__doc__)
    sub = p.add_subparsers(dest="cmd", required=True)
    d = sub.add_parser("demo", help="write the fictional register to sample_data/ and build the outputs")
    d.add_argument("--data", type=Path, default=Path("sample_data"))
    d.add_argument("--out", type=Path, default=Path("output"))

    r = sub.add_parser("report", help="build the outputs from your own folder of CSV files")
    r.add_argument("data", type=Path, help="folder with contracts.csv (and optionally budget.csv, sla.csv, settings.json)")
    r.add_argument("--out", type=Path, default=Path("output"))
    r.add_argument("--title", default="IT vendor and contract review")
    r.add_argument("--as-of", type=_date, help="review date, YYYY-MM-DD (default: settings.json or today)")
    r.add_argument("--notice-critical", type=int, default=Config.notice_critical)
    r.add_argument("--notice-warning", type=int, default=Config.notice_warning)
    r.add_argument("--end-critical", type=int, default=Config.end_critical)
    r.add_argument("--end-warning", type=int, default=Config.end_warning)
    r.add_argument("--low-utilisation", type=float, default=Config.low_utilisation, help="0.80 = 80%%")
    r.add_argument("--concentration", type=float, default=Config.concentration, help="0.30 = 30%% of spend")
    a = p.parse_args(argv)

    if a.cmd == "demo":
        simulate.write(a.data)
        sm = run(a.data, a.out, "IT vendor and contract review · Contoso Iberia", Config())
    else:
        c = Config(notice_critical=a.notice_critical, notice_warning=a.notice_warning, end_critical=a.end_critical,
                   end_warning=a.end_warning, low_utilisation=a.low_utilisation, concentration=a.concentration)
        try:
            sm = run(a.data, a.out, a.title, c, a.as_of)
        except FileNotFoundError as exc:
            print(f"error: {exc}", file=sys.stderr)
            return 2
    print(f"{sm['contracts']} contracts, {sm['vendors']} vendors · {sm['critical']} critical, {sm['warning']} warnings, "
          f"{sm['info']} info · {sm['decisions_90d']} renewal decisions in 90 days · outputs in {a.out}/")
    return 0
