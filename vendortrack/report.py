"""Outputs: report.html, review.md (monthly vendor review), actions.csv, findings.csv and renewals.ics."""
from __future__ import annotations

import csv
import html
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from pathlib import Path

from .checks import (AREAS, CRITICAL, INFO, SEV_ORDER, WARNING, Config, Finding, area_status, budget_rows, calendar,
                     fiscal_elapsed, money, renewal_of, sla_months)
from .model import Register

e = html.escape


def summary(reg: Register, findings: list[Finding]) -> dict:
    t = reg.as_of
    total = sum(k.annual_cost or 0 for k in reg.contracts)
    capex = sum(k.annual_cost or 0 for k in reg.contracts if k.cost_type == "CAPEX")
    cal = calendar(reg, 3)
    return {"contracts": len(reg.contracts), "vendors": len({k.vendor for k in reg.contracts}),
            "annual_spend": total, "capex": capex, "opex": total - capex,
            "decisions_90d": len(cal), "decisions_90d_value": sum(r["contract"].annual_cost or 0 for r in cal),
            "savings": sum(f.amount or 0 for f in findings if "saving" in f.tags and f.area == "Licences"),
            "credits": sum(f.amount or 0 for f in findings if f.code == "SLA_MISSED" and f.amount),
            "critical": sum(f.severity == CRITICAL for f in findings),
            "warning": sum(f.severity == WARNING for f in findings),
            "info": sum(f.severity == INFO for f in findings), "as_of": t}


def write_findings(findings: list[Finding], path: Path, cur: str) -> None:
    with open(path, "w", newline="", encoding="utf-8") as fh:
        w = csv.writer(fh)
        w.writerow(["severity", "area", "code", "ref", "subject", "owner", "due", "amount_" + cur, "detail", "next_step"])
        for f in findings:
            w.writerow([f.severity, f.area, f.code, f.contract, f.subject, f.owner, f.due or "",
                        f"{f.amount:.0f}" if f.amount is not None else "", f.detail, f.fix])


def write_actions(findings: list[Finding], path: Path, cur: str) -> int:
    """One row per action, worst and soonest first, with a 'done' column to tick off in the review."""
    rows = [f for f in findings if f.code not in {"CONCENTRATION", "NOTICE_PASSED"}]
    with open(path, "w", newline="", encoding="utf-8") as fh:
        w = csv.writer(fh)
        w.writerow(["priority", "severity", "due", "owner", "subject", "action", "why", "amount_" + cur, "done"])
        for i, f in enumerate(rows, 1):
            w.writerow([i, f.severity, f.due or "", f.owner, f.subject, f.fix, f.detail,
                        f"{f.amount:.0f}" if f.amount is not None else "", ""])
    return len(rows)


def renewals_ics(reg: Register, months: int = 12) -> str:
    """Calendar file with every notice deadline and contract end in the next `months`, with a reminder 14 days before."""
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    lines = ["BEGIN:VCALENDAR", "VERSION:2.0", "PRODID:-//vendortrack//renewals//EN", "CALSCALE:GREGORIAN",
             f"X-WR-CALNAME:IT contract renewals · {_ics(reg.organisation)}"]
    for r in calendar(reg, months):
        k = r["contract"]
        d = r["date"]
        what = "Give notice" if r["kind"] == "Notice deadline" else "Contract ends"
        desc = (f"{r['kind']} for {k.id}. Term ends {r['end']:%Y-%m-%d}. Owner: {k.owner or '?'}. "
                f"Annual value: {money(k.annual_cost, reg.currency)}.")
        lines += ["BEGIN:VEVENT", f"UID:{k.id}-{d:%Y%m%d}@vendortrack", f"DTSTAMP:{stamp}",
                  f"DTSTART;VALUE=DATE:{d:%Y%m%d}", f"DTEND;VALUE=DATE:{d + timedelta(days=1):%Y%m%d}",
                  f"SUMMARY:{_ics(what + ': ' + k.label)}",
                  "DESCRIPTION:" + _ics(desc),
                  "BEGIN:VALARM", "ACTION:DISPLAY", f"DESCRIPTION:{_ics(what + ' in 14 days: ' + k.label)}",
                  "TRIGGER:-P14D", "END:VALARM", "END:VEVENT"]
    lines.append("END:VCALENDAR")
    return "\r\n".join(_fold(x) for x in lines) + "\r\n"


def _ics(s: str) -> str:
    return s.replace("\\", "\\\\").replace(";", "\\;").replace(",", "\\,").replace("\n", "\\n")


def _fold(line: str) -> str:
    out, b = [], line.encode("utf-8")
    while len(b) > 75:
        cut = 75
        while (b[cut] & 0xC0) == 0x80:
            cut -= 1
        out.append(b[:cut].decode("utf-8"))
        b = b" " + b[cut:]
    out.append(b.decode("utf-8"))
    return "\r\n".join(out)


def review_md(reg: Register, findings: list[Finding]) -> str:
    sm, cur = summary(reg, findings), reg.currency
    st = area_status(findings)
    lines = [f"# IT vendor review · {reg.organisation} · {reg.as_of:%Y-%m-%d}", "",
             f"{sm['contracts']} contracts with {sm['vendors']} vendors · {money(sm['annual_spend'], cur)} a year "
             f"({money(sm['opex'], cur)} OPEX, {money(sm['capex'], cur)} CAPEX) · {sm['decisions_90d']} renewal decisions "
             f"in the next 90 days · {sm['critical']} critical, {sm['warning']} warnings.", "",
             "| Area | Status |", "|---|---|"]
    lines += [f"| {a} | {'OK' if st[a] == 'ok' else st[a].upper()} |" for a in AREAS]
    for sev, title in ((CRITICAL, "Act now"), (WARNING, "This month")):
        items = [f for f in findings if f.severity == sev]
        if items:
            lines += ["", f"## {title}", ""]
            for f in items:
                who = f" · {f.owner}" if f.owner else ""
                due = f" · by {f.due:%Y-%m-%d}" if f.due and f.area == "Renewals" else ""
                lines.append(f"- **{f.subject}**{who}{due}: {f.detail}  \n  Next step: {f.fix}")
    lines += ["", "## Renewal calendar (next 12 months)", "", "| Date | Decision | Contract | Owner | Annual value |",
              "|---|---|---|---|---|"]
    for r in calendar(reg, 12):
        k = r["contract"]
        lines.append(f"| {r['date']:%Y-%m-%d} | {r['kind']} | {k.label} | {k.owner or '?'} | {money(k.annual_cost, cur)} |")
    lines += ["", f"## Budget ({fiscal_elapsed(reg):.0%} of the year gone)", "",
              "| Line | Budget | Actual YTD | Forecast | Variance |", "|---|---|---|---|---|"]
    for b in budget_rows(reg):
        lines.append(f"| {b['line']} | {money(b['budget'], cur)} | {money(b['actual'], cur)} | "
                     f"{money(b['forecast'], cur)} | {b['variance']:+.0%} |")
    savings = [f for f in findings if "saving" in f.tags]
    if savings:
        lines += ["", "## Savings and budget headroom", ""]
        lines += [f"- {f.subject}: {f.detail}" for f in savings]
    infos = [f for f in findings if f.severity == INFO and "saving" not in f.tags]
    if infos:
        lines += ["", "## For information", ""] + [f"- {f.subject}: {f.detail}" for f in infos]
    lines += ["", "Work list: `actions.csv` · full list: `findings.csv` · calendar to import: `renewals.ics`.", ""]
    return "\n".join(lines)


CSS = """
:root{--bg:#f6f7f8;--card:#fff;--ink:#1b2128;--muted:#66717c;--line:#e2e5e9;--ok:#1e7a4c;--warn:#a46200;--bad:#b42318;--info:#0b6bcb}
@media (prefers-color-scheme:dark){:root{--bg:#13171b;--card:#1b2127;--ink:#e7eaee;--muted:#9aa5b0;--line:#2b333b;--ok:#4cc38a;--warn:#f0a83a;--bad:#f2685c;--info:#5aa9f0}}
*{box-sizing:border-box}body{margin:0;background:var(--bg);color:var(--ink);font:15px/1.5 system-ui,-apple-system,Segoe UI,sans-serif}
main{max-width:1100px;margin:0 auto;padding:28px 18px 60px}h1{font-size:26px;margin:0 0 4px}h2{font-size:18px;margin:34px 0 12px}
.muted{color:var(--muted)}.tiles{display:grid;grid-template-columns:repeat(auto-fit,minmax(160px,1fr));gap:12px;margin-top:20px}
.tile{background:var(--card);border:1px solid var(--line);border-radius:10px;padding:14px 16px}.tile b{display:block;font-size:26px}
.areas{display:grid;grid-template-columns:repeat(auto-fit,minmax(150px,1fr));gap:10px}
.area{background:var(--card);border:1px solid var(--line);border-left:5px solid var(--ok);border-radius:10px;padding:10px 14px}
.area.critical{border-left-color:var(--bad)}.area.warning{border-left-color:var(--warn)}.area.info{border-left-color:var(--info)}
.area .lbl{display:block;font-size:12px;text-transform:uppercase;letter-spacing:.04em;color:var(--muted);margin-bottom:4px}.area .cnt{display:block;font-size:13px;color:var(--muted);margin-top:4px}
table{width:100%;border-collapse:collapse;background:var(--card);border:1px solid var(--line);border-radius:10px;overflow:hidden}
th,td{text-align:left;padding:8px 10px;border-bottom:1px solid var(--line);vertical-align:top}th{font-size:12px;text-transform:uppercase;letter-spacing:.04em;color:var(--muted)}
tr:last-child td{border-bottom:0}.wrap{overflow-x:auto}.num{text-align:right;font-variant-numeric:tabular-nums;white-space:nowrap}
.pill{display:inline-block;white-space:nowrap;padding:1px 8px;border-radius:99px;font-size:12px;font-weight:600;border:1px solid currentColor}
.critical{color:var(--bad)}.warning{color:var(--warn)}.info{color:var(--info)}.ok{color:var(--ok)}
.area.critical,.area.warning,.area.info,.area.ok{color:inherit}
.bar{position:relative;height:10px;background:var(--line);border-radius:5px;overflow:hidden;min-width:140px}.bar i{display:block;height:100%;background:var(--ok)}
.bar i.warning{background:var(--warn)}.bar i.critical{background:var(--bad)}.bar s{position:absolute;top:-2px;bottom:-2px;width:2px;background:var(--ink)}
.timeline{display:grid;grid-template-columns:repeat(6,minmax(0,1fr));gap:6px}.mo{background:var(--card);border:1px solid var(--line);border-radius:8px;padding:6px;min-height:64px;font-size:12px}
.mo b{display:block;font-size:11px;text-transform:uppercase;letter-spacing:.04em;color:var(--muted);margin-bottom:4px}
.mo span{display:block;padding:2px 5px;margin-bottom:3px;border-radius:5px;background:var(--line);overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
.mo span.n{border-left:3px solid var(--warn)}.mo span.x{border-left:3px solid var(--bad)}
.sla td.c{text-align:center;font-weight:600}td.nw{white-space:nowrap}td.fix{font-size:13px;color:var(--muted);max-width:380px}
footer{margin-top:40px;font-size:13px}@media (max-width:760px){.timeline{grid-template-columns:repeat(4,minmax(0,1fr))}}
.lic .bar{display:inline-block;vertical-align:middle;width:140px;margin-right:8px}
"""


def _pill(s: str) -> str:
    return f"<span class='pill {s}'>{'OK' if s == 'ok' else s.upper()}</span>"


def render_html(reg: Register, findings: list[Finding], title: str, c: Config | None = None) -> str:
    c = c or Config()
    sm, cur, st = summary(reg, findings), reg.currency, area_status(findings)
    h = [f"<!doctype html><html lang='en'><head><meta charset='utf-8'><meta name='viewport' content='width=device-width,initial-scale=1'>"
         f"<title>{e(title)}</title><style>{CSS}</style></head><body><main>",
         f"<h1>{e(title)}</h1><p class='muted'>{e(reg.organisation)} · as of {reg.as_of:%d %b %Y} · "
         f"{sm['contracts']} contracts, {sm['vendors']} vendors</p>", "<div class='tiles'>"]
    tiles = [("Annual spend", money(sm["annual_spend"], cur), f"{money(sm['opex'], cur)} OPEX · {money(sm['capex'], cur)} CAPEX"),
             ("Decisions in 90 days", str(sm["decisions_90d"]), f"{money(sm['decisions_90d_value'], cur)} a year at stake"),
             ("Licence savings", money(sm["savings"], cur), "a year, at the next renewals"),
             ("Service credits", money(sm["credits"], cur), "to claim for missed SLAs"),
             ("Critical", str(sm["critical"]), f"{sm['warning']} warnings · {sm['info']} for information")]
    h += [f"<div class='tile'><span class='muted'>{e(a)}</span><b>{e(b)}</b><span class='muted'>{e(x)}</span></div>"
          for a, b, x in tiles]
    h.append("</div><h2>Status by area</h2><div class='areas'>")
    for a in AREAS:
        n = sum(f.area == a and f.severity != INFO for f in findings)
        h.append(f"<div class='area {st[a]}'><span class='lbl'>{e(a)}</span>{_pill(st[a])}<span class='cnt'>{n} to act on</span></div>")
    h.append("</div>")

    act = [f for f in findings if f.severity == CRITICAL]
    if act:
        h.append("<h2>Act now</h2><div class='wrap'><table><tr><th>Subject</th><th>What</th><th>Next step</th><th>Owner</th></tr>")
        h += [f"<tr><td class='nw'><b>{e(f.subject)}</b><br><span class='muted'>{e(f.area)}</span></td><td>{e(f.detail)}</td>"
              f"<td class='fix'>{e(f.fix)}</td><td class='nw'>{e(f.owner or '?')}</td></tr>" for f in act]
        h.append("</table></div>")

    # 12-month renewal timeline
    h.append("<h2>Renewal calendar · next 12 months</h2><div class='timeline'>")
    by = defaultdict(list)
    for r in calendar(reg, 12):
        by[(r["date"].year, r["date"].month)].append(r)
    y, m = reg.as_of.year, reg.as_of.month
    for _ in range(12):
        items = "".join(f"<span class='{'n' if r['kind'] == 'Notice deadline' else 'x'}' title='{e(r['kind'])} {r['date']:%Y-%m-%d}: "
                        f"{e(r['contract'].label)}'>{r['date']:%d} · {e(r['contract'].vendor)}</span>" for r in by[(y, m)])
        h.append(f"<div class='mo'><b>{datetime(y, m, 1):%b %Y}</b>{items}</div>")
        y, m = (y + 1, 1) if m == 12 else (y, m + 1)
    h.append("</div><p class='muted'>Orange: last day to give notice on a contract that renews by itself. "
             "Red: end of a contract that does not renew by itself.</p>")

    # budget
    h.append(f"<h2>Budget · {fiscal_elapsed(reg):.0%} of the year gone</h2><div class='wrap'><table><tr><th>Line</th>"
             "<th class='num'>Budget</th><th class='num'>Actual YTD</th><th class='num'>Forecast</th><th class='num'>Variance</th>"
             "<th>Spent (line = today)</th></tr>")
    elapsed = fiscal_elapsed(reg)
    for b in budget_rows(reg):
        cls = "critical" if b["variance"] > c.over_budget_critical else "warning" if b["variance"] > c.over_budget_warning else ""
        h.append(f"<tr><td>{e(b['line'])}<br><span class='muted'>{e(b['owner'])}</span></td><td class='num'>{money(b['budget'], cur)}</td>"
                 f"<td class='num'>{money(b['actual'], cur)}</td><td class='num'>{money(b['forecast'], cur)}</td>"
                 f"<td class='num {cls}'>{b['variance']:+.0%}</td><td><div class='bar'><i class='{cls}' style='width:{min(100, b['spent_pct'] * 100):.0f}%'></i>"
                 f"<s style='left:{elapsed * 100:.0f}%'></s></div></td></tr>")
    h.append("</table></div>")

    # licences
    lic = [k for k in reg.contracts if k.licences_purchased and k.licences_used is not None]
    if lic:
        h.append("<h2>Licences</h2><div class='wrap'><table class='lic'><tr><th>Contract</th><th class='num'>Purchased</th>"
                 "<th class='num'>In use</th><th>Use</th><th>Renewal</th></tr>")
        for k in sorted(lic, key=lambda k: k.licences_used / k.licences_purchased):
            u = k.licences_used / k.licences_purchased
            cls = "warning" if u > 1 else "info" if u < c.low_utilisation else ""
            h.append(f"<tr><td>{e(k.label)}</td><td class='num'>{k.licences_purchased}</td><td class='num'>{k.licences_used}</td>"
                     f"<td><div class='bar'><i class='{cls}' style='width:{min(100, u * 100):.0f}%'></i></div> <span class='{cls}'>{u:.0%}</span></td>"
                     f"<td class='nw'>{(renewal_of(k, reg.as_of) or k.end or '')}</td></tr>")
        h.append("</table></div>")

    # SLA grid
    sla_k = [k for k in reg.contracts if k.sla_target is not None]
    if sla_k:
        months = sla_months(reg, 6)
        got = defaultdict(dict)
        for s in reg.sla:
            got[s.contract_id][s.month] = s.achieved
        h.append("<h2>SLA · last 6 months</h2><div class='wrap'><table class='sla'><tr><th>Contract</th><th class='num'>Target</th>"
                 + "".join(f"<th class='num'>{m[2:]}</th>" for m in months) + "</tr>")
        for k in sla_k:
            cells = []
            for m in months:
                v = got[k.id].get(m)
                cells.append("<td class='c muted'>–</td>" if v is None else
                             f"<td class='c {'critical' if v < k.sla_target else 'ok'}'>{v:g}</td>")
            h.append(f"<tr><td>{e(k.label)}</td><td class='num'>{k.sla_target:g}%</td>{''.join(cells)}</tr>")
        h.append("</table></div>")

    # spend by category
    cat = defaultdict(float)
    for k in reg.contracts:
        cat[k.category or "Other"] += k.annual_cost or 0
    top = max(cat.values() or [1])
    h.append("<h2>Spend by category</h2><div class='wrap'><table>")
    for name, v in sorted(cat.items(), key=lambda x: -x[1]):
        h.append(f"<tr><td>{e(name)}</td><td class='num'>{money(v, cur)}</td><td><div class='bar'><i style='width:{v / top * 100:.0f}%'></i></div></td></tr>")
    h.append("</table></div>")

    # all findings
    h.append("<h2>All findings</h2><div class='wrap'><table><tr><th>Severity</th><th>Area</th><th>Subject</th><th>Detail</th><th>Next step</th></tr>")
    h += [f"<tr><td>{_pill(f.severity)}</td><td class='nw'>{e(f.area)}</td><td>{e(f.subject)}</td><td>{e(f.detail)}</td>"
          f"<td class='fix'>{e(f.fix)}</td></tr>" for f in findings]
    h.append("</table></div><footer class='muted'>Generated by vendortrack from the contract register. "
             "Fictional demo data unless you ran it on your own files.</footer></main></body></html>")
    return "".join(h)
