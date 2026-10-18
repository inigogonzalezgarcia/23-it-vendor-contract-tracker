# Monthly vendor review: runbook

A 30-minute meeting once a month with the IT lead, the contract owners and, every quarter, someone from finance or
procurement.

## Before the meeting (15 minutes)

1. Update `budget.csv` with the actual spend to date from finance.
2. Add last month's SLA figures to `sla.csv` from the vendors' service reports.
3. Add any new contract or order form to `contracts.csv` and fix the dates of anything that renewed.
4. Run the report:
   ```bash
   python -m vendortrack report data/ --out output
   ```
5. Send `output/review.md` to the attendees and import `output/renewals.ics` into the team calendar. Each event has a
   stable ID per contract and date, so calendar apps that support updates replace last month's events instead of
   adding duplicates.

## In the meeting

1. **Act now** (critical): one owner and one date for each line. Notice deadlines inside two weeks come first: a
   missed deadline usually means another full term.
2. **This month** (warnings): renewal decisions in the next 45 days, licences over the purchased quantity, budget
   lines over forecast, SLA misses.
3. **Savings and budget headroom**: decide which unused licences to reduce at the next renewal and keep a small
   buffer for joiners.
4. **Renewal calendar**: look three months ahead and start the quotes for anything with a long lead time
   (connectivity, hardware, data centre).
5. Tick the `done` column in `actions.csv` and keep the file with the minutes.

## Renewal decision checklist

For each contract that comes up for renewal:

- **Usage:** how many seats, lines or devices are really used? (`licences_used`)
- **Service:** SLA results for the last twelve months, major incidents, how the vendor handled them.
- **Price:** market price, multi-year discount, price-increase cap, payment terms.
- **Terms:** notice period and renewal length for the next term, service credits, data return and exit assistance.
- **Alternatives:** at least one other quote for anything critical or above your procurement threshold.
- **Decision:** renew as is, renegotiate, replace or retire, and who sends the written notice before the deadline.

## Giving notice

- Send it in writing to the address in the contract's notice clause (often not your account manager).
- Keep the acknowledgement with the contract.
- Update `end`, `auto_renew` and `notice_days` in the register the same day.
