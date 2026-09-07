"""
Build the invoice as a real Google Sheet tab, driven by formulas.

    python make_invoice_sheet.py --sheet <url or id>
    python make_invoice_sheet.py --sheet <url or id> --tab "Invoice RCI-2026-02" --replace

Everything that should be arithmetic IS arithmetic:

  * Amount per line   = Hours x Rate, and blank while the line is blank
  * Subtotal          = SUM over the whole line-item block
  * Discount          = a flat figure or a percentage, chosen from a dropdown
  * Amount due        = Subtotal minus the discount, never below zero

So adding a line, deleting one, or changing an hour figure updates the total on
its own. Twenty-five item rows are laid out and the SUM covers all of them, which
means new lines can be typed straight into the blanks without touching a formula.
Inserting a row inside the block works too -- Sheets widens the SUM for you.

WHICH WORKBOOK
--------------
Use a NEW, separate spreadsheet, not the cleaning schedule: that one is shared
with the team, and an invoice is not their business. Create a blank sheet, share
it with the service account as Editor, and pass its URL here. Its email is printed
if the sheet cannot be opened.
"""
from __future__ import annotations

import argparse
import sys

from sheets_client import open_spreadsheet, service_account_info
from sync import load_config

# The line-item block. Generous on purpose: the SUM covers every row of it, so
# lines can be added by typing into the blanks rather than by editing a formula.
HEAD_ROW = 15
FIRST_ITEM = 16
N_ITEMS = 25
LAST_ITEM = FIRST_ITEM + N_ITEMS - 1          # 40
R_HOURS = LAST_ITEM + 2                       # 42
R_SUB = R_HOURS + 1                           # 43
R_DISC = R_SUB + 1                            # 44
R_DUE = R_DISC + 1                            # 45

ITEMS = [
    ("Discovery, Guesty API integration and data pipeline",
     "Authentication, paginated reservation retrieval, field mapping, and the transformation into schedule rows.", 12, 125),
    ("Scheduling logic",
     "Same-day turnover detection, early and late arrival/departure codes, and multi-unit properties split into one clean per unit.", 6.5, 125),
    ("Google Sheets write layer",
     "Writing without destroying the team's tick boxes or formatting; monthly tab routing and automatic creation of new months.", 7, 125),
    ("Daily change tracking and visual marking",
     "Highlighting of new and amended bookings, permanent strikethrough of cancellations, and per-cell colour coding for turnovers and adjusted times.", 5, 125),
    ("Reliability engineering and automated scheduling",
     "Shared credential caching within the provider's daily limit, once-per-day execution that tolerates late delivery, and safeguards that stop rather than corrupt the sheet on bad data.", 6, 125),
    ("Cloud infrastructure and deployment",
     "Google Cloud project, storage and service-account configuration; scheduled deployment pipeline with manual override and safety toggle.", 5, 125),
    ("Data remediation of existing records",
     "Removal of ~1,600 duplicated rows and 454 out-of-market rows; correction of cancellation marks that had drifted onto live bookings.", 4.5, 125),
    ("Testing, deployment supervision and documentation",
     "142 automated checks; supervised live runs across several mornings; written handover covering operation and the manual controls.", 6.5, 125),
]

FILL = "<< fill in >>"


def cells() -> list[list]:
    """The whole grid, A1 down. Formulas are written as text and entered as formulas."""
    g = [[""] * 5 for _ in range(R_DUE + 12)]

    def put(r, c, v):
        g[r - 1][c] = v

    put(1, 0, "INVOICE")
    put(2, 0, "Software development & systems integration")
    put(1, 3, "Invoice No");  put(1, 4, "RCI-2026-01")
    put(2, 3, "Issued");      put(2, 4, "2026-09-06")
    put(3, 3, "Due");         put(3, 4, "2026-09-21")
    put(4, 3, "Terms");       put(4, 4, "Net 15")

    put(5, 0, "FROM")
    put(6, 0, "Christopher Maraist")
    put(7, 0, "chris.tektonpro@gmail.com")
    put(8, 0, FILL + " street, city, state, ZIP")
    put(9, 0, FILL + " phone")

    put(5, 2, "BILL TO")
    put(6, 2, "Ramos Cleaning, Inc.")
    put(7, 2, FILL + " street, city, state, ZIP")
    put(8, 2, FILL + " accounts payable contact")

    put(11, 0, "PROJECT")
    put(12, 0, "Guesty → Google Sheets automation")
    put(13, 0, "Nightly cleaning-schedule build across five markets · 2026-08-02 → 2026-09-04")

    put(HEAD_ROW, 0, "Services rendered")
    put(HEAD_ROW, 1, "Detail")
    put(HEAD_ROW, 2, "Hours")
    put(HEAD_ROW, 3, "Rate")
    put(HEAD_ROW, 4, "Amount")

    for i in range(N_ITEMS):
        r = FIRST_ITEM + i
        if i < len(ITEMS):
            desc, detail, hours, rate = ITEMS[i]
            put(r, 0, desc); put(r, 1, detail); put(r, 2, hours); put(r, 3, rate)
        # Blank while the line is blank, so the empty rows stay invisible.
        put(r, 4, f'=IF(OR($C{r}="",$D{r}=""),"",$C{r}*$D{r})')

    put(R_HOURS, 3, "Total hours")
    put(R_HOURS, 4, f"=SUM(C{FIRST_ITEM}:C{LAST_ITEM})")
    put(R_SUB, 3, "Subtotal")
    put(R_SUB, 4, f"=SUM(E{FIRST_ITEM}:E{LAST_ITEM})")

    put(R_DISC, 1, "Discount")
    put(R_DISC, 2, "$")          # dropdown: $ or %
    put(R_DISC, 3, 0)            # the figure
    # Negative, so it reads as a deduction and the total is a plain addition.
    put(R_DISC, 4, f'=IF($C{R_DISC}="%",-$E{R_SUB}*$D{R_DISC}/100,-$D{R_DISC})')

    put(R_DUE, 3, "AMOUNT DUE")
    put(R_DUE, 4, f"=MAX(0,$E{R_SUB}+$E{R_DISC})")

    put(R_DUE + 2, 0, "PAYMENT")
    put(R_DUE + 3, 0, "Payable to");  put(R_DUE + 3, 1, "Christopher Maraist")
    put(R_DUE + 4, 0, "Method");      put(R_DUE + 4, 1, FILL + " bank transfer / check / Zelle")
    put(R_DUE + 5, 0, "Reference");   put(R_DUE + 5, 1, "RCI-2026-01")

    put(R_DUE + 7, 0, "NOT INCLUDED")
    put(R_DUE + 8, 0, "Work on the Connecteam job-scheduling integration is under way and is deliberately "
                      "excluded here. It will be billed separately once jobs are live in the Connecteam "
                      "account. Ongoing monitoring and maintenance is also not included.")
    put(R_DUE + 10, 0, "Please include the invoice reference with payment.")
    return g


def formats(sheet_id: int) -> list[dict]:
    """Everything that makes it read like a document rather than a spreadsheet."""
    def rng(r1, r2, c1=0, c2=5):
        return {"sheetId": sheet_id, "startRowIndex": r1 - 1, "endRowIndex": r2,
                "startColumnIndex": c1, "endColumnIndex": c2}

    def fmt(r1, r2, cell, fields, c1=0, c2=5):
        return {"repeatCell": {"range": rng(r1, r2, c1, c2),
                               "cell": {"userEnteredFormat": cell}, "fields": fields}}

    ink = {"red": .07, "green": .09, "blue": .13}
    faint = {"red": .54, "green": .58, "blue": .63}
    accent = {"red": .09, "green": .35, "blue": .30}
    accent_bg = {"red": .91, "green": .95, "blue": .93}
    money = {"type": "CURRENCY", "pattern": '"$"#,##0.00'}

    req = [
        # column widths
        *[{"updateDimensionProperties": {
            "range": {"sheetId": sheet_id, "dimension": "COLUMNS",
                      "startIndex": i, "endIndex": i + 1},
            "properties": {"pixelSize": w}, "fields": "pixelSize"}}
          for i, w in enumerate((300, 340, 70, 80, 110))],

        fmt(1, 1, {"textFormat": {"fontSize": 26, "bold": True, "foregroundColor": ink}},
            "userEnteredFormat.textFormat"),
        fmt(2, 2, {"textFormat": {"fontSize": 10, "foregroundColor": faint}},
            "userEnteredFormat.textFormat"),
        # the small uppercase labels
        *[fmt(r, r, {"textFormat": {"fontSize": 8, "bold": True, "foregroundColor": faint}},
              "userEnteredFormat.textFormat", 0, 1)
          for r in (5, 11, R_DUE + 2, R_DUE + 7)],
        fmt(5, 5, {"textFormat": {"fontSize": 8, "bold": True, "foregroundColor": faint}},
            "userEnteredFormat.textFormat", 2, 3),
        fmt(1, 4, {"textFormat": {"fontSize": 9, "bold": True, "foregroundColor": faint},
                   "horizontalAlignment": "RIGHT"},
            "userEnteredFormat(textFormat,horizontalAlignment)", 3, 4),
        fmt(6, 6, {"textFormat": {"fontSize": 12, "bold": True}}, "userEnteredFormat.textFormat", 0, 1),
        fmt(6, 6, {"textFormat": {"fontSize": 12, "bold": True}}, "userEnteredFormat.textFormat", 2, 3),
        fmt(12, 12, {"textFormat": {"bold": True}}, "userEnteredFormat.textFormat", 0, 1),

        # header row of the table
        fmt(HEAD_ROW, HEAD_ROW,
            {"textFormat": {"fontSize": 9, "bold": True, "foregroundColor": faint},
             "borders": {"bottom": {"style": "SOLID", "color": ink}}},
            "userEnteredFormat(textFormat,borders)"),
        fmt(HEAD_ROW, HEAD_ROW, {"horizontalAlignment": "RIGHT"},
            "userEnteredFormat.horizontalAlignment", 2, 5),

        # item block: wrapped text, money columns, hairline rules
        fmt(FIRST_ITEM, LAST_ITEM, {"wrapStrategy": "WRAP", "verticalAlignment": "TOP"},
            "userEnteredFormat(wrapStrategy,verticalAlignment)"),
        fmt(FIRST_ITEM, LAST_ITEM, {"textFormat": {"fontSize": 9, "foregroundColor": faint},
                                    "wrapStrategy": "WRAP"},
            "userEnteredFormat(textFormat,wrapStrategy)", 1, 2),
        fmt(FIRST_ITEM, LAST_ITEM, {"numberFormat": {"type": "NUMBER", "pattern": "0.##"}},
            "userEnteredFormat.numberFormat", 2, 3),
        fmt(FIRST_ITEM, LAST_ITEM, {"numberFormat": money}, "userEnteredFormat.numberFormat", 3, 5),
        fmt(FIRST_ITEM, LAST_ITEM,
            {"borders": {"bottom": {"style": "SOLID", "color": {"red": .93, "green": .94, "blue": .96}}}},
            "userEnteredFormat.borders"),

        # totals
        fmt(R_HOURS, R_DUE, {"horizontalAlignment": "RIGHT", "textFormat": {"bold": True}},
            "userEnteredFormat(horizontalAlignment,textFormat)", 3, 4),
        fmt(R_HOURS, R_HOURS, {"numberFormat": {"type": "NUMBER", "pattern": "0.##"}},
            "userEnteredFormat.numberFormat", 4, 5),
        fmt(R_SUB, R_DUE, {"numberFormat": money}, "userEnteredFormat.numberFormat", 4, 5),
        fmt(R_DISC, R_DISC, {"numberFormat": money, "horizontalAlignment": "RIGHT"},
            "userEnteredFormat(numberFormat,horizontalAlignment)", 3, 4),
        fmt(R_DISC, R_DISC, {"horizontalAlignment": "CENTER",
                             "textFormat": {"bold": True, "foregroundColor": accent}},
            "userEnteredFormat(horizontalAlignment,textFormat)", 2, 3),
        fmt(R_DISC, R_DISC, {"horizontalAlignment": "RIGHT", "textFormat": {"bold": True}},
            "userEnteredFormat(horizontalAlignment,textFormat)", 1, 2),
        fmt(R_DUE, R_DUE,
            {"backgroundColor": accent_bg,
             "textFormat": {"bold": True, "fontSize": 13, "foregroundColor": accent},
             "borders": {"top": {"style": "SOLID", "color": accent},
                         "bottom": {"style": "SOLID", "color": accent}}},
            "userEnteredFormat(backgroundColor,textFormat,borders)", 3, 5),

        # the notes at the foot
        fmt(R_DUE + 8, R_DUE + 8, {"wrapStrategy": "WRAP",
                                   "textFormat": {"fontSize": 9, "foregroundColor": faint}},
            "userEnteredFormat(wrapStrategy,textFormat)", 0, 5),
        fmt(R_DUE + 10, R_DUE + 10, {"textFormat": {"fontSize": 9, "foregroundColor": faint}},
            "userEnteredFormat.textFormat", 0, 5),

        # $ or % -- a dropdown, so the discount formula can never see junk
        {"setDataValidation": {
            "range": rng(R_DISC, R_DISC, 2, 3),
            "rule": {"condition": {"type": "ONE_OF_LIST",
                                   "values": [{"userEnteredValue": "$"},
                                              {"userEnteredValue": "%"}]},
                     "strict": True, "showCustomUi": True}}},

        # the placeholders, so they cannot be missed
        {"addConditionalFormatRule": {"index": 0, "rule": {
            "ranges": [rng(1, R_DUE + 6, 0, 5)],
            "booleanRule": {
                "condition": {"type": "TEXT_CONTAINS",
                              "values": [{"userEnteredValue": FILL}]},
                "format": {"backgroundColor": {"red": .99, "green": .95, "blue": .87},
                           "textFormat": {"foregroundColor": {"red": .54, "green": .35, "blue": .07}}}}}}},

        {"updateSheetProperties": {
            "properties": {"sheetId": sheet_id,
                           "gridProperties": {"frozenRowCount": HEAD_ROW}},
            "fields": "gridProperties.frozenRowCount"}},
    ]
    return req


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--sheet", help="URL or id of the workbook to build the invoice in. "
                                    "Use a NEW one, not the cleaning schedule.")
    ap.add_argument("--tab", default="Invoice RCI-2026-01")
    ap.add_argument("--replace", action="store_true", help="Overwrite the tab if it exists.")
    args = ap.parse_args(argv)

    cfg = load_config()
    target = args.sheet or cfg["sheet_id"]
    if not target:
        print("ERROR: pass --sheet <url or id>, or set SHEET_ID.", file=sys.stderr)
        return 2
    if not args.sheet:
        print("!! No --sheet given, so this would write into the CLEANING SCHEDULE,")
        print("   which the team can see. Pass --sheet with a separate workbook.")
        return 2

    try:
        ss = open_spreadsheet(target, cfg["sa_json"])
    except Exception as e:  # noqa: BLE001
        print(f"ERROR: {e}", file=sys.stderr)
        try:
            who = service_account_info(cfg["sa_json"]).get("client_email", "")
            print(f"\nShare the workbook with {who} as an Editor, then try again.",
                  file=sys.stderr)
        except Exception:  # noqa: BLE001
            pass
        return 1

    grid = cells()
    existing = {w.title: w for w in ss.worksheets()}
    if args.tab in existing:
        if not args.replace:
            print(f"ERROR: '{args.tab}' already exists. Pass --replace to overwrite, "
                  f"or --tab with another name.", file=sys.stderr)
            return 2
        ws = existing[args.tab]
        ws.clear()
        ws.resize(rows=max(len(grid) + 5, ws.row_count), cols=5)
    else:
        ws = ss.add_worksheet(title=args.tab, rows=len(grid) + 5, cols=5)

    ws.update(range_name="A1", values=grid, value_input_option="USER_ENTERED")
    ss.batch_update({"requests": formats(ws.id)})

    print(f"Built '{args.tab}' in {ss.title}.")
    print(f"  {ss.url}#gid={ws.id}")
    print()
    print("  Line items      rows %d-%d. Type into the blanks to add one; select a"
          % (FIRST_ITEM, LAST_ITEM))
    print("                  row and delete it to remove one. The total follows.")
    print("  Discount        row %d: choose $ or %% in column C, put the figure in D."
          % R_DISC)
    print("  Amount due      row %d, calculated. Do not type over it." % R_DUE)
    print("  Amber cells     the details only you can fill in.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
