"""Data model and loaders: contracts.csv, budget.csv, sla.csv and settings.json (see docs/data-format.md)."""
from __future__ import annotations

import csv
import json
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path

TRUE = {"yes", "y", "true", "1", "si", "sí"}


def parse_date(value: str | None) -> date | None:
    value = (value or "").strip()
    if not value:
        return None
    try:
        return date.fromisoformat(value[:10])
    except ValueError:
        return None


def parse_float(value: str | None) -> float | None:
    value = (value or "").strip().replace(" ", "")
    if not value:
        return None
    if "," in value and "." not in value:
        value = value.replace(",", ".")
    try:
        return float(value.replace(",", ""))
    except ValueError:
        return None


def parse_int(value: str | None) -> int | None:
    f = parse_float(value)
    return int(f) if f is not None else None


def add_months(d: date, months: int) -> date:
    m = d.month - 1 + months
    y, m = d.year + m // 12, m % 12 + 1
    days = [31, 29 if y % 4 == 0 and (y % 100 or y % 400 == 0) else 28, 31, 30, 31, 30, 31, 31, 30, 31, 30, 31]
    return date(y, m, min(d.day, days[m - 1]))


@dataclass
class Contract:
    id: str
    vendor: str
    service: str
    category: str = ""
    owner: str = ""
    cost_type: str = "OPEX"          # OPEX or CAPEX
    annual_cost: float | None = None
    start: date | None = None
    end: date | None = None
    auto_renew: bool = False
    notice_days: int | None = None
    renewal_months: int = 12
    criticality: str = "medium"      # high, medium, low
    alternative: str = ""            # exit plan or second supplier; empty means none noted
    licences_purchased: int | None = None
    licences_used: int | None = None
    unit_cost: float | None = None
    sla_target: float | None = None  # e.g. 99.9 (% availability or % tickets in time)
    credit_pct: float | None = None  # service credit, % of the monthly fee per month below target
    budget_line: str = ""

    @property
    def label(self) -> str:
        return f"{self.vendor} · {self.service}"

    @property
    def notice_by(self) -> date | None:
        if self.end and self.notice_days is not None:
            from datetime import timedelta
            return self.end - timedelta(days=self.notice_days)
        return None

    @property
    def monthly_cost(self) -> float:
        return (self.annual_cost or 0.0) / 12


@dataclass
class BudgetLine:
    line: str
    owner: str = ""
    budget: float = 0.0
    actual_ytd: float = 0.0


@dataclass
class SlaRecord:
    contract_id: str
    month: str          # YYYY-MM
    achieved: float


@dataclass
class Register:
    contracts: list[Contract]
    budget: list[BudgetLine] = field(default_factory=list)
    sla: list[SlaRecord] = field(default_factory=list)
    organisation: str = "Your organisation"
    currency: str = "EUR"
    as_of: date = field(default_factory=date.today)
    fiscal_year_start: date | None = None

    def contract(self, cid: str) -> Contract | None:
        return next((c for c in self.contracts if c.id == cid), None)


def _rows(path: Path) -> list[dict]:
    if not path.exists():
        return []
    with open(path, newline="", encoding="utf-8-sig") as fh:
        sample = fh.read(2048)
        fh.seek(0)
        delim = ";" if sample.count(";") > sample.count(",") else ","
        return [{(k or "").strip().lower(): (v or "").strip() for k, v in r.items()} for r in csv.DictReader(fh, delimiter=delim)]


def load_contracts(path: Path) -> list[Contract]:
    out = []
    for r in _rows(path):
        if not r.get("vendor"):
            continue
        out.append(Contract(
            id=r.get("id") or f"C{len(out) + 1:03d}", vendor=r["vendor"], service=r.get("service", ""),
            category=r.get("category", ""), owner=r.get("owner", ""),
            cost_type=(r.get("cost_type") or "OPEX").upper(), annual_cost=parse_float(r.get("annual_cost")),
            start=parse_date(r.get("start")), end=parse_date(r.get("end")),
            auto_renew=(r.get("auto_renew", "").lower() in TRUE), notice_days=parse_int(r.get("notice_days")),
            renewal_months=parse_int(r.get("renewal_months")) or 12,
            criticality=(r.get("criticality") or "medium").lower(), alternative=r.get("alternative", ""),
            licences_purchased=parse_int(r.get("licences_purchased")), licences_used=parse_int(r.get("licences_used")),
            unit_cost=parse_float(r.get("unit_cost")), sla_target=parse_float(r.get("sla_target")),
            credit_pct=parse_float(r.get("credit_pct")), budget_line=r.get("budget_line", "")))
    return out


def load_budget(path: Path) -> list[BudgetLine]:
    return [BudgetLine(r["line"], r.get("owner", ""), parse_float(r.get("budget")) or 0.0,
                       parse_float(r.get("actual_ytd")) or 0.0) for r in _rows(path) if r.get("line")]


def load_sla(path: Path) -> list[SlaRecord]:
    out = []
    for r in _rows(path):
        a = parse_float(r.get("achieved"))
        if r.get("contract_id") and r.get("month") and a is not None:
            out.append(SlaRecord(r["contract_id"], r["month"][:7], a))
    return out


def load(data: Path, as_of: date | None = None) -> Register:
    """Read a folder with contracts.csv (required), budget.csv, sla.csv and settings.json (optional)."""
    data = Path(data)
    if not (data / "contracts.csv").exists():
        raise FileNotFoundError(f"{data / 'contracts.csv'} not found")
    s = json.loads((data / "settings.json").read_text(encoding="utf-8")) if (data / "settings.json").exists() else {}
    reg = Register(load_contracts(data / "contracts.csv"), load_budget(data / "budget.csv"), load_sla(data / "sla.csv"),
                   organisation=s.get("organisation", "Your organisation"), currency=s.get("currency", "EUR"))
    reg.as_of = as_of or parse_date(s.get("as_of")) or date.today()
    reg.fiscal_year_start = parse_date(s.get("fiscal_year_start")) or date(reg.as_of.year, 1, 1)
    return reg
