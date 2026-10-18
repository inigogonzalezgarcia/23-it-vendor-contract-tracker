"""Checks: renewals and notice deadlines, licences, budget, SLA, supplier risk and data quality."""
from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass, field
from datetime import date, timedelta

from .model import Contract, Register, add_months

CRITICAL, WARNING, INFO = "critical", "warning", "info"
SEV_ORDER = {CRITICAL: 0, WARNING: 1, INFO: 2}
AREAS = ["Renewals", "Licences", "Budget", "SLA", "Supplier risk", "Data quality"]


@dataclass
class Config:
    notice_critical: int = 14       # days left to give notice on an auto-renewing contract
    notice_warning: int = 45
    notice_info: int = 90
    end_critical: int = 30          # days left on a contract that does not renew by itself
    end_warning: int = 90
    end_info: int = 180
    low_utilisation: float = 0.80   # licences used / purchased below this: savings at renewal
    min_spare: int = 5              # ...and at least this many spare licences
    over_budget_critical: float = 0.10
    over_budget_warning: float = 0.03
    under_budget_info: float = 0.15
    sla_months: int = 3             # SLA look-back window
    concentration: float = 0.30     # one vendor's share of total annual spend


@dataclass
class Finding:
    severity: str
    area: str
    code: str
    contract: str          # contract id, budget line or vendor
    subject: str           # "Vendor · service" or budget line
    detail: str
    fix: str
    owner: str = ""
    due: date | None = None
    amount: float | None = None   # money at stake (renewal value, savings, credits, overspend)
    tags: list[str] = field(default_factory=list)


def money(v: float | None, cur: str = "EUR") -> str:
    if v is None:
        return "?"
    sym = {"EUR": "€", "USD": "$", "GBP": "£"}.get(cur, cur + " ")
    return f"{sym}{v:,.0f}" if sym in "€$£" else f"{v:,.0f} {cur}"


def renewal_of(c: Contract, as_of: date) -> date | None:
    """For an auto-renewing contract whose end date has passed: the end date of the term it rolled into."""
    if not (c.auto_renew and c.end and c.end < as_of):
        return None
    end = c.end
    while end < as_of:
        end = add_months(end, c.renewal_months or 12)
    return end


def check_renewals(reg: Register, c: Config) -> list[Finding]:
    out, t, cur = [], reg.as_of, reg.currency
    for k in reg.contracts:
        if not k.end:
            continue
        value = k.annual_cost * (k.renewal_months or 12) / 12 if k.annual_cost else None
        if k.end < t:
            if k.auto_renew:
                new_end = renewal_of(k, t)
                out.append(Finding(WARNING, "Renewals", "AUTO_RENEWED", k.id, k.label,
                                   f"Renewed by itself on {k.end:%Y-%m-%d} for {k.renewal_months} months "
                                   f"(about {money(value, cur)}); the register still shows the old end date.",
                                   f"Confirm the new term with the vendor and set the end date to {new_end:%Y-%m-%d}; "
                                   f"diarise the next notice deadline.", k.owner, new_end, value))
            else:
                out.append(Finding(CRITICAL, "Renewals", "ENDED", k.id, k.label,
                                   f"Ended {(t - k.end).days} days ago ({k.end:%Y-%m-%d}) and does not renew by itself: "
                                   f"the service or its support may already be off.",
                                   "Check with the vendor today: extend, replace or retire the service, and update the register.",
                                   k.owner, k.end, value))
            continue
        if k.auto_renew:
            nb = k.notice_by
            if nb is None:
                continue
            days = (nb - t).days
            if days < 0:
                out.append(Finding(INFO, "Renewals", "NOTICE_PASSED", k.id, k.label,
                                   f"Notice deadline passed on {nb:%Y-%m-%d}: it renews on {k.end:%Y-%m-%d} for "
                                   f"{k.renewal_months} months (about {money(value, cur)}).",
                                   "Plan the change for the next term and diarise the next notice date.", k.owner, k.end, value))
            elif days <= c.notice_info:
                sev = CRITICAL if days <= c.notice_critical else WARNING if days <= c.notice_warning else INFO
                out.append(Finding(sev, "Renewals", "NOTICE_DUE", k.id, k.label,
                                   f"Give notice by {nb:%Y-%m-%d} (in {days} days, {k.notice_days} days' notice) or it "
                                   f"renews on {k.end:%Y-%m-%d} for {k.renewal_months} months (about {money(value, cur)}).",
                                   "Decide renew, renegotiate or exit; ask for a quote and usage data now, and send any "
                                   "notice in writing before the deadline.", k.owner, nb, value))
        else:
            days = (k.end - t).days
            if days <= c.end_info:
                sev = CRITICAL if days <= c.end_critical else WARNING if days <= c.end_warning else INFO
                out.append(Finding(sev, "Renewals", "ENDING", k.id, k.label,
                                   f"Ends on {k.end:%Y-%m-%d} (in {days} days) and does not renew by itself.",
                                   "Renew, replace or retire: get quotes, check the lead time and plan the change before the end date.",
                                   k.owner, k.end, value))
    return out


def check_licences(reg: Register, c: Config) -> list[Finding]:
    out, cur = [], reg.currency
    for k in reg.contracts:
        p, u = k.licences_purchased, k.licences_used
        if not p or u is None:
            continue
        unit = k.unit_cost if k.unit_cost is not None else ((k.annual_cost or 0) / p if p else 0)
        if u > p:
            extra = (u - p) * unit
            out.append(Finding(WARNING, "Licences", "OVER_DEPLOYED", k.id, k.label,
                               f"{u} in use for {p} purchased: {u - p} over (true-up of about {money(extra, cur)} a year).",
                               "Remove leavers and unused assignments first, then buy the difference before the vendor audit "
                               "or the anniversary true-up.", k.owner, k.end, extra))
        elif u / p < c.low_utilisation and p - u >= c.min_spare:
            saving = (p - u) * unit
            out.append(Finding(INFO, "Licences", "UNUSED", k.id, k.label,
                               f"{u} of {p} in use ({u / p:.0%}): {p - u} spare, about {money(saving, cur)} a year.",
                               "Keep a small buffer for joiners and reduce the quantity at the next renewal or true-down window.",
                               k.owner, k.notice_by or k.end, saving, ["saving"]))
    return out


def fiscal_elapsed(reg: Register) -> float:
    fys = reg.fiscal_year_start or date(reg.as_of.year, 1, 1)
    while add_months(fys, 12) <= reg.as_of:
        fys = add_months(fys, 12)
    return max(1, (reg.as_of - fys).days) / (add_months(fys, 12) - fys).days


def budget_rows(reg: Register) -> list[dict]:
    """Per budget line: budget, actual YTD, straight-line forecast, committed contracts, variance."""
    elapsed = fiscal_elapsed(reg)
    committed = defaultdict(float)
    for k in reg.contracts:
        if k.budget_line and k.annual_cost:
            committed[k.budget_line] += k.annual_cost
    rows = []
    for b in reg.budget:
        forecast = b.actual_ytd / elapsed if elapsed else b.actual_ytd
        var = (forecast / b.budget - 1) if b.budget else 0.0
        rows.append({"line": b.line, "owner": b.owner, "budget": b.budget, "actual": b.actual_ytd,
                     "forecast": forecast, "variance": var, "committed": committed.get(b.line, 0.0),
                     "spent_pct": b.actual_ytd / b.budget if b.budget else 0.0})
    return rows


def check_budget(reg: Register, c: Config) -> list[Finding]:
    out, cur = [], reg.currency
    pct = fiscal_elapsed(reg)
    for r in budget_rows(reg):
        if not r["budget"]:
            continue
        over = r["forecast"] - r["budget"]
        if r["variance"] > c.over_budget_critical or r["variance"] > c.over_budget_warning:
            sev = CRITICAL if r["variance"] > c.over_budget_critical else WARNING
            out.append(Finding(sev, "Budget", "OVER_FORECAST", r["line"], r["line"],
                               f"{money(r['actual'], cur)} spent at {pct:.0%} of the year: on this pace it ends at "
                               f"{money(r['forecast'], cur)}, {r['variance']:+.0%} over a budget of {money(r['budget'], cur)}.",
                               "Review the largest items with the owner: delay, renegotiate or move spend, or request a "
                               "budget transfer now rather than at year end.", r["owner"], None, over))
        elif r["variance"] < -c.under_budget_info:
            out.append(Finding(INFO, "Budget", "UNDER_FORECAST", r["line"], r["line"],
                               f"On pace to end at {money(r['forecast'], cur)}, {r['variance']:+.0%} under budget "
                               f"({money(r['budget'], cur)}).",
                               "Confirm whether planned spend is only late (invoices, projects) or the budget can be "
                               "released for next year's plan.", r["owner"], None, -over, ["saving"]))
        if r["committed"] > r["budget"] * (1 + c.over_budget_warning):
            out.append(Finding(WARNING, "Budget", "COMMITTED_OVER", r["line"], r["line"],
                               f"Contracts charged to this line add up to {money(r['committed'], cur)} a year, above the "
                               f"budget of {money(r['budget'], cur)}.",
                               "Check that the contracts' budget lines are right and plan the gap in next year's budget.",
                               r["owner"], None, r["committed"] - r["budget"]))
    return out


def sla_months(reg: Register, n: int) -> list[str]:
    """The last n full months before as_of, oldest first (YYYY-MM)."""
    first = date(reg.as_of.year, reg.as_of.month, 1)
    return [f"{add_months(first, -i):%Y-%m}" for i in range(n, 0, -1)]


def check_sla(reg: Register, c: Config) -> list[Finding]:
    out, cur = [], reg.currency
    window = sla_months(reg, c.sla_months)
    by = defaultdict(dict)
    for s in reg.sla:
        by[s.contract_id][s.month] = s.achieved
    for k in reg.contracts:
        if k.sla_target is None or (k.end and k.end < reg.as_of and not k.auto_renew):
            continue
        got = by.get(k.id, {})
        missing = [m for m in window if m not in got]
        misses = [m for m in window if m in got and got[m] < k.sla_target]
        if misses:
            sev = CRITICAL if len(misses) >= 2 else WARNING
            worst = min(got[m] for m in misses)
            credit = k.monthly_cost * (k.credit_pct or 0) / 100 * len(misses) if k.credit_pct else None
            claim = f" Claim the service credit ({k.credit_pct:g}% of the monthly fee per month, about {money(credit, cur)})." if credit else ""
            out.append(Finding(sev, "SLA", "SLA_MISSED", k.id, k.label,
                               f"Below the {k.sla_target:g}% target in {len(misses)} of the last {len(window)} months "
                               f"({', '.join(misses)}; worst {worst:g}%).",
                               "Raise it at the next service review with the incident list and ask for a written "
                               "improvement plan." + claim, k.owner, None, credit))
        if missing:
            out.append(Finding(INFO, "SLA", "SLA_REPORT_MISSING", k.id, k.label,
                               f"No SLA figure for {', '.join(missing)}.",
                               "Ask the vendor for the monthly service report; the contract target cannot be checked without it.",
                               k.owner))
    return out


def check_supplier_risk(reg: Register, c: Config) -> list[Finding]:
    out, cur = [], reg.currency
    for k in reg.contracts:
        if k.criticality == "high" and k.alternative.strip().lower() in {"", "no", "none", "-"}:
            out.append(Finding(WARNING, "Supplier risk", "NO_EXIT_PLAN", k.id, k.label,
                               "Critical service with a single supplier and no alternative or exit plan noted.",
                               "Write down the exit plan (who could take over, lead time, data return) or add a "
                               "second supplier for the critical part.", k.owner))
    total = sum(k.annual_cost or 0 for k in reg.contracts)
    spend = defaultdict(float)
    for k in reg.contracts:
        spend[k.vendor] += k.annual_cost or 0
    for v, s in sorted(spend.items(), key=lambda x: -x[1]):
        if total and len(spend) >= 3 and s / total > c.concentration:
            out.append(Finding(INFO, "Supplier risk", "CONCENTRATION", v, v,
                               f"{s / total:.0%} of the annual spend ({money(s, cur)}) goes to one vendor.",
                               "Fine if intended; keep it visible in the vendor strategy and use it in the next negotiation.",
                               amount=s))
    return out


def check_data(reg: Register, c: Config) -> list[Finding]:
    out = []
    for k in reg.contracts:
        if not k.owner:
            out.append(Finding(WARNING, "Data quality", "NO_OWNER", k.id, k.label,
                               "No internal owner.", "Name the person who decides on renewal and receives the service reports."))
        if not k.end:
            out.append(Finding(WARNING, "Data quality", "NO_END_DATE", k.id, k.label,
                               "No end date: renewals and notice deadlines cannot be tracked.",
                               "Take the term from the signed contract or the last order form.", k.owner))
        elif k.auto_renew and k.notice_days is None:
            out.append(Finding(WARNING, "Data quality", "NO_NOTICE_PERIOD", k.id, k.label,
                               "Renews by itself but the notice period is missing.",
                               "Read the termination clause and record the notice period in days.", k.owner))
        if k.annual_cost is None:
            out.append(Finding(INFO, "Data quality", "NO_COST", k.id, k.label, "No annual cost recorded.",
                               "Add the annual value from the last invoice or order form.", k.owner))
    return out


def run_all(reg: Register, c: Config | None = None) -> list[Finding]:
    c = c or Config()
    fs = (check_renewals(reg, c) + check_licences(reg, c) + check_budget(reg, c) + check_sla(reg, c)
          + check_supplier_risk(reg, c) + check_data(reg, c))
    return sorted(fs, key=lambda f: (SEV_ORDER[f.severity], AREAS.index(f.area), f.due or date.max, f.subject))


def area_status(findings: list[Finding]) -> dict[str, str]:
    st = {a: "ok" for a in AREAS}
    for f in findings:
        if st[f.area] == "ok" or SEV_ORDER[f.severity] < SEV_ORDER.get(st[f.area], 9):
            st[f.area] = f.severity
    return st


def calendar(reg: Register, months: int = 12) -> list[dict]:
    """Every renewal decision in the next `months`: notice deadlines (auto-renewing) and end dates (the rest)."""
    t, horizon, rows = reg.as_of, add_months(reg.as_of, months), []
    for k in reg.contracts:
        end = renewal_of(k, t) or k.end
        if not end:
            continue
        if k.auto_renew and k.notice_days is not None:
            nb = end - timedelta(days=k.notice_days)
            if t <= nb <= horizon:
                rows.append({"date": nb, "kind": "Notice deadline", "contract": k, "end": end})
        elif not k.auto_renew and t <= end <= horizon:
            rows.append({"date": end, "kind": "Contract end", "contract": k, "end": end})
    return sorted(rows, key=lambda r: r["date"])
