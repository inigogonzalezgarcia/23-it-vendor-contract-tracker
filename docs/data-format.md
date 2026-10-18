# Data format

One folder with up to four files. Only `contracts.csv` is required. Comma or semicolon separators both work (the
separator is detected), headers are case-insensitive, a UTF-8 BOM from Excel is fine and decimal commas (`1200,50`)
are accepted. Dates are `YYYY-MM-DD`. Empty cells skip the check that needs them.

## contracts.csv

One row per contract, order form or subscription.

| Column | Example | Meaning |
|---|---|---|
| `id` | `C001` | Your reference. Used to join `sla.csv`. Generated if empty. |
| `vendor` | `Northwind Telecom` | Required. |
| `service` | `SD-WAN and MPLS, 4 sites` | What you buy. |
| `category` | `Connectivity` | Free text, used for the spend chart. |
| `owner` | `Marta Ruiz` | The person who decides on renewal and receives the service reports. |
| `cost_type` | `OPEX` / `CAPEX` | Default OPEX. |
| `annual_cost` | `96000` | Annual value in the register currency. |
| `start`, `end` | `2024-01-01`, `2026-12-31` | Current term. |
| `auto_renew` | `yes` / `no` | Renews by itself unless notice is given (`yes`, `y`, `true`, `1`, `sí`). |
| `notice_days` | `60` | Notice period in days before `end`. |
| `renewal_months` | `12` | Length of the next term when it renews by itself. Default 12. |
| `criticality` | `high` / `medium` / `low` | Default medium. |
| `alternative` | `4G backup router per site` | Exit plan or second supplier. Empty on a `high` contract raises a finding. |
| `licences_purchased`, `licences_used` | `300`, `312` | Seats, lines, devices: anything counted. |
| `unit_cost` | `360` | Annual cost per unit. If empty: `annual_cost / licences_purchased`. |
| `sla_target` | `99.9` | Monthly target in %: availability, tickets in time, response within the agreed time. |
| `credit_pct` | `5` | Service credit in % of the monthly fee for each month below target. |
| `budget_line` | `Network` | Must match a `line` in `budget.csv`. |

## budget.csv

| Column | Example | Meaning |
|---|---|---|
| `line` | `Network` | Budget line. |
| `owner` | `Marta Ruiz` | |
| `budget` | `150000` | Annual budget. |
| `actual_ytd` | `128000` | Spent so far this fiscal year (from finance or the invoices). |

The forecast is a straight line: `actual_ytd / share of the fiscal year gone`. It is a conversation starter for the
review, not an accounting forecast: annual invoices paid in one month distort it, and the report says how far
through the year you are so that is easy to see.

## sla.csv

| Column | Example | Meaning |
|---|---|---|
| `contract_id` | `C010` | `id` in `contracts.csv`. |
| `month` | `2026-09` | Month of the vendor's service report. |
| `achieved` | `94.5` | Result in the same unit as `sla_target`. |

The last three full months before the review date are checked; the report shows six.

## settings.json

```json
{
  "organisation": "Contoso Iberia",
  "currency": "EUR",
  "as_of": "2026-10-18",
  "fiscal_year_start": "2026-01-01"
}
```

All keys are optional. `as_of` is useful to re-run a past review; without it (and without `--as-of`) today is used.
`fiscal_year_start` only needs the month and day of any year: the tool rolls it forward to the current fiscal year.

## Exporting from what you already have

- **A spreadsheet register:** keep your own columns and add the ones above (or rename them), then save as CSV.
- **An ITSM or asset tool with a contracts module:** export contracts to CSV and map the column names.
- **SLA figures:** most vendors send a monthly PDF; one line per contract and month in `sla.csv` is enough.
