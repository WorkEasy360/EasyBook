"""CSV export for flat, row-list-shaped reports (PHASE 8 spec §24).

Scope, deliberately: reports whose payload is a flat list of dicts (AR/AP
balances, sales/purchases by customer/item, stock summary, trial balance
rows, ...). Multi-section statements (P&L/Balance Sheet/Cash Flow) and the
paginated transaction-detail reports (General Ledger, Journal, Inventory
Movement) are NOT wired to `?format=csv` in this slice — a flattened export
of a nested statement or an unbounded transaction list needs its own shape
decision and, per spec §24, heavy exports belong on a background task before
they are opened up further. XLSX/PDF are not added: no such dependency is
already in this project's requirements.txt (root CLAUDE.md §8).
"""

import csv
import io

from django.http import HttpResponse


def rows_to_csv_response(*, rows: list[dict], fieldnames: list[str], filename: str) -> HttpResponse:
    buffer = io.StringIO()
    writer = csv.DictWriter(buffer, fieldnames=fieldnames, extrasaction="ignore")
    writer.writeheader()
    for row in rows:
        writer.writerow(row)

    response = HttpResponse(buffer.getvalue(), content_type="text/csv")
    response["Content-Disposition"] = f'attachment; filename="{filename}"'
    return response


def wants_csv(request) -> bool:
    # NOT `format` — DRF's DefaultContentNegotiation reserves that query
    # param for its own renderer-selection (`URL_FORMAT_OVERRIDE`) and 404s
    # when asked for a format no configured renderer declares, since this
    # project only registers JSONRenderer (config/settings/base.py). `export`
    # is an unclaimed name that sidesteps DRF's negotiation entirely.
    return request.query_params.get("export", "").lower() == "csv"
