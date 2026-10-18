"""Tests for vendortrack. Run: python -m unittest discover tests"""
import csv
import json
import tempfile
import unittest
from datetime import date
from pathlib import Path

from vendortrack import cli, report, simulate
from vendortrack.checks import (Config, budget_rows, calendar, check_budget, check_data, check_licences,
                                check_renewals, check_sla, check_supplier_risk, fiscal_elapsed, renewal_of, run_all)
from vendortrack.model import BudgetLine, Contract, Register, SlaRecord, add_months, load, parse_float

T = date(2026, 10, 18)


def reg(*contracts, budget=(), sla=(), as_of=T):
    return Register(list(contracts), list(budget), list(sla), "Test", "EUR", as_of, date(2026, 1, 1))


def k(**kw):
    base = dict(id="X1", vendor="Acme", service="Thing", owner="Ana", annual_cost=12000, start=date(2025, 1, 1),
                end=date(2027, 12, 31), auto_renew=False, notice_days=30, criticality="medium", alternative="plan")
    base.update(kw)
    return Contract(**base)


def codes(findings):
    return [f.code for f in findings]


class Helpers(unittest.TestCase):
    def test_add_months_clamps_month_end(self):
        self.assertEqual(add_months(date(2026, 1, 31), 1), date(2026, 2, 28))
        self.assertEqual(add_months(date(2027, 11, 30), 3), date(2028, 2, 29))
        self.assertEqual(add_months(date(2026, 10, 1), -10), date(2025, 12, 1))

    def test_parse_float_accepts_spanish_decimal_comma(self):
        self.assertEqual(parse_float("1234,5"), 1234.5)
        self.assertEqual(parse_float("1,234.50"), 1234.5)
        self.assertIsNone(parse_float(""))


class Renewals(unittest.TestCase):
    def test_clean_contract_has_no_findings(self):
        self.assertEqual(run_all(reg(k())), [])

    def test_notice_thresholds_for_auto_renewing_contracts(self):
        # notice_by = end - 30 days; 14 days left -> critical, 15 -> warning, 45 -> warning, 46..90 -> info, 91 -> none
        for left, sev in ((14, "critical"), (15, "warning"), (45, "warning"), (46, "info"), (90, "info"), (91, None)):
            end = date.fromordinal(T.toordinal() + left + 30)
            fs = check_renewals(reg(k(end=end, auto_renew=True)), Config())
            self.assertEqual(fs[0].severity if fs else None, sev, left)
            if fs:
                self.assertEqual(fs[0].code, "NOTICE_DUE")

    def test_notice_passed_is_information_only(self):
        fs = check_renewals(reg(k(end=date(2026, 11, 5), auto_renew=True, notice_days=60)), Config())
        self.assertEqual((fs[0].code, fs[0].severity), ("NOTICE_PASSED", "info"))

    def test_contract_that_does_not_renew(self):
        for left, sev in ((30, "critical"), (31, "warning"), (90, "warning"), (180, "info"), (181, None)):
            fs = check_renewals(reg(k(end=date.fromordinal(T.toordinal() + left))), Config())
            self.assertEqual(fs[0].severity if fs else None, sev, left)

    def test_ended_and_auto_renewed_contracts(self):
        ended = check_renewals(reg(k(end=date(2026, 9, 30))), Config())
        self.assertEqual((ended[0].code, ended[0].severity), ("ENDED", "critical"))
        rolled = k(end=date(2025, 11, 30), auto_renew=True, renewal_months=12)
        fs = check_renewals(reg(rolled), Config())
        self.assertEqual(fs[0].code, "AUTO_RENEWED")
        self.assertEqual(renewal_of(rolled, T), date(2026, 11, 30))
        self.assertEqual(renewal_of(k(end=date(2024, 3, 31), auto_renew=True, renewal_months=24), T), date(2028, 3, 31))

    def test_calendar_uses_notice_dates_and_rolled_terms(self):
        r = reg(k(id="A", end=date(2027, 1, 31), auto_renew=True, notice_days=90),
                k(id="B", end=date(2027, 3, 1)),
                k(id="C", end=date(2025, 12, 31), auto_renew=True, notice_days=30))   # rolled to 2026-12-31
        rows = [(x["contract"].id, x["date"], x["kind"]) for x in calendar(r, 12)]
        self.assertEqual(rows, [("A", date(2026, 11, 2), "Notice deadline"), ("C", date(2026, 12, 1), "Notice deadline"),
                                ("B", date(2027, 3, 1), "Contract end")])


class Licences(unittest.TestCase):
    def test_over_deployed_and_unused(self):
        over = check_licences(reg(k(licences_purchased=100, licences_used=104, unit_cost=50)), Config())
        self.assertEqual((over[0].code, over[0].amount), ("OVER_DEPLOYED", 200))
        spare = check_licences(reg(k(licences_purchased=100, licences_used=70, unit_cost=10)), Config())
        self.assertEqual((spare[0].code, spare[0].amount), ("UNUSED", 300))
        self.assertIn("saving", spare[0].tags)

    def test_small_buffer_is_not_flagged(self):
        self.assertEqual(check_licences(reg(k(licences_purchased=20, licences_used=16)), Config()), [])   # 80%
        self.assertEqual(check_licences(reg(k(licences_purchased=10, licences_used=6)), Config()), [])    # only 4 spare
        unit = check_licences(reg(k(licences_purchased=50, licences_used=30, annual_cost=5000)), Config())
        self.assertEqual(unit[0].amount, 2000)   # unit cost derived from annual cost / purchased


class Budget(unittest.TestCase):
    def test_straight_line_forecast_and_thresholds(self):
        r = reg(budget=[BudgetLine("A", budget=100000, actual_ytd=90000), BudgetLine("B", budget=100000, actual_ytd=83000),
                        BudgetLine("C", budget=100000, actual_ytd=60000), BudgetLine("D", budget=100000, actual_ytd=79000)],
                as_of=date(2026, 10, 19))   # 291 of 365 days
        self.assertAlmostEqual(fiscal_elapsed(r), 291 / 365)
        got = {f.contract: (f.code, f.severity) for f in check_budget(r, Config())}
        self.assertEqual(got["A"], ("OVER_FORECAST", "critical"))
        self.assertEqual(got["B"], ("OVER_FORECAST", "warning"))
        self.assertEqual(got["C"], ("UNDER_FORECAST", "info"))
        self.assertNotIn("D", got)

    def test_committed_contracts_above_budget_and_next_fiscal_year(self):
        r = reg(k(annual_cost=60000, budget_line="Net"), k(id="X2", annual_cost=50000, budget_line="Net"),
                budget=[BudgetLine("Net", budget=100000, actual_ytd=79000)])
        self.assertIn("COMMITTED_OVER", codes(check_budget(r, Config())))
        later = reg(as_of=date(2027, 4, 2))   # fiscal year rolls to 2027-01-01
        self.assertAlmostEqual(fiscal_elapsed(later), 91 / 365)
        self.assertEqual(budget_rows(reg(budget=[BudgetLine("Z", budget=0)]))[0]["variance"], 0.0)


class Sla(unittest.TestCase):
    def test_misses_credits_and_missing_reports(self):
        c = k(sla_target=99.9, credit_pct=10, annual_cost=12000)
        sla = [SlaRecord("X1", "2026-07", 99.95), SlaRecord("X1", "2026-08", 99.5), SlaRecord("X1", "2026-09", 99.7)]
        fs = check_sla(reg(c, sla=sla), Config())
        self.assertEqual((fs[0].code, fs[0].severity, fs[0].amount), ("SLA_MISSED", "critical", 200))   # 1000 * 10% * 2
        one = check_sla(reg(c, sla=sla[:2] + [SlaRecord("X1", "2026-09", 99.99)]), Config())
        self.assertEqual(one[0].severity, "warning")
        missing = check_sla(reg(c, sla=sla[:2]), Config())
        self.assertIn("SLA_REPORT_MISSING", codes(missing))
        self.assertEqual(check_sla(reg(k(sla_target=None)), Config()), [])


class RiskAndData(unittest.TestCase):
    def test_single_supplier_and_concentration(self):
        fs = check_supplier_risk(reg(k(criticality="high", alternative=""), k(id="X2", vendor="Other", annual_cost=1000),
                                     k(id="X3", vendor="Third", annual_cost=1000)), Config())
        self.assertEqual(sorted(codes(fs)), ["CONCENTRATION", "NO_EXIT_PLAN"])
        self.assertEqual(check_supplier_risk(reg(k(criticality="high", alternative="Second ISP")), Config(concentration=1.0)), [])

    def test_data_quality(self):
        fs = check_data(reg(k(owner="", end=None), k(id="X2", auto_renew=True, notice_days=None, annual_cost=None)), Config())
        self.assertEqual(sorted(codes(fs)), ["NO_COST", "NO_END_DATE", "NO_NOTICE_PERIOD", "NO_OWNER"])


class Loading(unittest.TestCase):
    def test_semicolon_csv_with_bom_and_settings(self):
        with tempfile.TemporaryDirectory() as tmp:
            d = Path(tmp)
            (d / "contracts.csv").write_text("﻿ID;Vendor;Service;Annual_Cost;End;Auto_Renew;Notice_Days\n"
                                             "A1;Acme;Wi-Fi;1200,50;2027-01-31;Sí;60\n;;;;;;\n", encoding="utf-8")
            (d / "settings.json").write_text(json.dumps({"organisation": "Org", "as_of": "2026-10-18"}))
            r = load(d)
            self.assertEqual(len(r.contracts), 1)
            self.assertEqual((r.contracts[0].annual_cost, r.contracts[0].auto_renew, r.as_of), (1200.5, True, T))
            self.assertEqual(load(d, date(2027, 1, 1)).as_of, date(2027, 1, 1))
            self.assertEqual(cli.main(["report", str(d / "missing")]), 2)


class EndToEnd(unittest.TestCase):
    def test_demo_fires_every_check_and_writes_all_outputs(self):
        with tempfile.TemporaryDirectory() as tmp:
            data, out = Path(tmp) / "data", Path(tmp) / "out"
            self.assertEqual(cli.main(["demo", "--data", str(data), "--out", str(out)]), 0)
            for name in ("report.html", "review.md", "renewals.ics", "findings.csv", "actions.csv"):
                self.assertTrue((out / name).stat().st_size > 200, name)
            with open(out / "findings.csv", encoding="utf-8") as fh:
                found = {r["code"] for r in csv.DictReader(fh)}
            expected = {"ENDED", "AUTO_RENEWED", "NOTICE_DUE", "NOTICE_PASSED", "ENDING", "OVER_DEPLOYED", "UNUSED",
                        "OVER_FORECAST", "UNDER_FORECAST", "COMMITTED_OVER", "SLA_MISSED", "SLA_REPORT_MISSING",
                        "NO_EXIT_PLAN", "NO_OWNER", "NO_END_DATE", "NO_NOTICE_PERIOD"}
            self.assertEqual(expected - found, set())
            ics = (out / "renewals.ics").read_bytes()
            self.assertTrue(ics.startswith(b"BEGIN:VCALENDAR\r\n") and b"TRIGGER:-P14D" in ics)
            self.assertTrue(all(len(line) <= 75 for line in ics.split(b"\r\n")))
            html = (out / "report.html").read_text(encoding="utf-8")
            self.assertIn("Renewal calendar", html)
            sm = report.summary(load(data), run_all(load(data)))
            self.assertEqual((sm["contracts"], sm["critical"]), (21, 6))


if __name__ == "__main__":
    unittest.main()
