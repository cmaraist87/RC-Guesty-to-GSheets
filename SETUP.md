# Automated daily Guesty → Google Sheet sync

Once a day at **4:00 AM America/Chicago (CST/CDT)**, a GitHub Action pulls reservations
from the Guesty Open API, runs them through the existing `processing.py` engine, merges
the result into your Google Sheet (preserving your checkbox ticks), and writes it back —
no manual CSV downloads or paste-over-A1.

```
GitHub Actions cron (4 AM CT)
  → guesty_client.py      OAuth token (cached) + paginated /reservations
  → guesty_adapter.py     reservation JSON → check-out & check-in DataFrames
  → processing.py         your existing engine (UNCHANGED)
  → sheet_merge.py        merge vs current sheet, preserve checkboxes  (ported from reservations.ipynb)
  → sheets_client.py      write back to the live sheet
```

Nothing about the transformation logic changed — `processing.py` is reused as-is.

---

## One-time setup

### 1. Guesty Open API credentials
1. In Guesty: **Integrations → API / Open API** → create an API application.
2. Copy the **Client ID** and **Client Secret**.
   - Note: Guesty allows only **5 token requests per key per 24h**. The client caches the
     token, and the job runs once/day, so this is never a problem — just don't spam runs.

### 2. Google service account (so the job can write the Sheet)
1. In the [Google Cloud Console](https://console.cloud.google.com/): create (or pick) a project.
   - **If you hit `iam.disableServiceAccountKeyCreation`** (an org policy that blocks
     downloadable keys): create the project with **Organization = "No organization"** at
     <https://console.cloud.google.com/projectcreate> — the key-block policy doesn't apply
     there. If "No organization" isn't selectable, your account is org-managed; use Workload
     Identity Federation (keyless) instead and ask for the adapted setup.
2. **APIs & Services → Enable APIs** → enable **Google Sheets API**.
3. **APIs & Services → Credentials → Create credentials → Service account.** Name it e.g.
   `guesty-sheet-sync`. Create it (no roles needed).
4. Open the service account → **Keys → Add key → Create new key → JSON.** Download the file.
5. Copy the service account's **email** (looks like `guesty-sheet-sync@PROJECT.iam.gserviceaccount.com`).
6. Open your Google Sheet → **Share** → paste that email → give it **Editor** → Send.
7. Grab the Sheet's **ID** from its URL:
   `https://docs.google.com/spreadsheets/d/`**`THIS_LONG_ID`**`/edit`.

### 3. Add secrets & variables to the GitHub repo
Repo → **Settings → Secrets and variables → Actions**.

**Secrets** (New repository secret):
| Name | Value |
|------|-------|
| `GUESTY_CLIENT_ID` | Guesty Client ID |
| `GUESTY_CLIENT_SECRET` | Guesty Client Secret |
| `SHEET_ID` | the Sheet ID from step 2.7 |
| `GOOGLE_SA_JSON` | paste the **entire contents** of the downloaded service-account JSON |

> **The workbook uses monthly tabs.** The sync auto-detects tabs named like
> `Julio 2026` / `Agosto 2026` (Spanish or English month + year) and routes each
> reservation to its month's tab, merging into each one independently and preserving
> that tab's checkboxes. Non-month tabs (`guesty_res`, pivot tables) are ignored.
> **Missing month tabs are auto-created** on a live run by duplicating a template tab
> (so checkbox formatting carries over), then filled. On a dry-run they're only
> reported. `WORKSHEET_NAME` is not used for the monthly workflow.

**Variables** (optional — sensible defaults apply if omitted):
| Name | Default | Meaning |
|------|---------|---------|
| `SYNC_LIVE` | *(unset)* | **Safety toggle.** Scheduled 4 AM runs only write when this is exactly `true`. Until then they run as a dry-run. |
| `TEMPLATE_TAB` | latest month tab | Which tab to duplicate when auto-creating a missing month (its checkbox formatting is the template). Set this if you keep a dedicated blank template tab. |
| `WORKSHEET_NAME` | *(unused)* | Ignored in the monthly-tab workflow; tabs are auto-detected by name. |
| `SYNC_LOOKBACK_DAYS` | `1` | include check-outs from N days ago |
| `SYNC_LOOKAHEAD_DAYS` | `180` | include check-ins up to N days ahead |
| `SYNC_STATUSES` | `confirmed,reserved,checkedIn` | reservation statuses to include |
| `SYNC_MARK_CANCELLED` | `1` | strike through rows whose reservation vanished from Guesty. Set `0` to leave them untouched. |
| `SYNC_LISTING_CITIES` | *(off)* | Set `1` to resolve **City** from Guesty's listings (one extra call per run) instead of relying on `property_to_city.csv`. Fail-soft: if the call errors, the run continues on the CSV alone. |
| `SYNC_FIELDS_MODE` | `objects` | Diagnostic. `paths` asks Guesty for dotted field paths (`listing.address.city`). The live API **rejected** this on 2026-08-21 and the run failed, so leave it unset unless you are testing. |

### Where the City column comes from
Priority, highest first: the reservation's own `LISTING'S CITY` → the Guesty listings
lookup (`SYNC_LISTING_CITIES=1`) → `property_to_city.csv` at merge time. The CSV is
meant to cover **exceptions**, not the whole portfolio; if it has grown to hundreds of
rows, the lookup above it has stopped working. Each run logs a **City source** line
saying how many reservations arrived with a city, and lists any listing that has no
city set in Guesty — fix those in Guesty and every downstream system benefits.

### Daily visual diff — highlights and strikethrough
Each run marks what it changed, so the ops team can see the day's delta at a glance:

| Mark | Meaning |
|------|---------|
| **Amber highlight** | the row was written by *this* run (new booking, or a booking whose times/guest changed). Yesterday's highlights are cleared first, so the fill always means "changed today". |
| ~~**Strikethrough**~~ | the reservation is no longer in Guesty — cancelled. The row is **kept in place**, not deleted, so nobody loses context on a job that was already assigned. |
| ~~**Strikethrough**~~ (*moved*) | same mark, but the booking wasn't cancelled — Guesty **reassigned** it to another listing or date. The old slot is just as dead, so it's struck; the new slot appears as a fresh highlighted row. The run report lists these separately under **Moved**, with a `Now at` column naming the listing and date the booking landed on. |

Only `strikethrough` and `backgroundColor` are ever written, so checkbox validation,
borders, fonts and column widths survive untouched. Struck rows stay struck on later
runs and stop matching, so a re-booking of the same property/date lands as a fresh
highlighted row underneath. Delete struck rows by hand whenever you've cleared them.

A **cancellation only counts inside the window the fetch covers**
(`SYNC_LOOKBACK_DAYS` … `SYNC_LOOKAHEAD_DAYS`) — rows outside it are never struck.
As a further guard, if one run would strike more than half of a tab's in-window rows
(and more than 10), it strikes nothing and reports a warning instead: that pattern
means a short Guesty fetch, not a mass cancellation. **Moved rows are exempt from that
guard** in both directions: a reassigned booking proves the fetch did carry that
reservation, so it never inflates the ratio, and it stays struck even when the guard
trips on the rows around it.

### Repairing a tab stuck on the old shifted layout
Tabs written before the column-alignment fix have their data one column left of the
header — the giveaway is a **check-out time sitting in the `Property` column**. Such a
tab can't be merged into (nothing in it matches what the pipeline now produces), so the
sync **skips it** rather than making it worse.

A skipped tab produces no per-tab section in the run Summary, so the run opens with a
⚠️ **warning block above the grand total** naming every skipped tab and how many
reservations went unsynced — without it the Summary would read as though that month
simply had nothing to do. **The totals exclude skipped tabs entirely.**

To fix one: **Actions → Guesty → Sheet daily sync → Run workflow**, tick
**"One-off repair: CLEAR tabs stuck on the old shifted layout and rebuild them from
Guesty"**, and untick **dry_run**. Every affected tab has its data rows (row 2 down)
cleared — header, checkbox validation, widths and borders all survive — and the month is
rebuilt from Guesty. Leave the box **unticked** afterwards; the scheduled 4 AM run never
sees it, so the daily job can't clear a tab on its own.

> This is destructive to manual edits on the affected tab: the rebuilt rows come back
> with checkboxes unticked. Run it with **dry_run ticked first** to see which tabs it
> would touch and how many rows it would clear.

### Checkbox columns — the team keeps editing in the same tabs
`assigned` / `Verified` / `OUT` / `IN` stay in the synced tabs; there is **no separate
working sheet to reconcile**. Ticks survive a run because:

- Only cell **values** are written, so the checkbox data-validation is never touched.
- Values go in as `USER_ENTERED`, so `TRUE`/`FALSE` land as real booleans (a ticked or
  unticked box), not as text that would break the widget.
- When a row is rewritten because its times or guest changed, its existing ticks are
  **carried onto the new row**. If several duplicate rows share one (Property, Date),
  a tick on **any** of them survives the collapse into the single new row.
- A genuinely new row starts with all four boxes unticked.

Which columns count as checkboxes is read from **the tab's own data-validation**, so
renaming them (`Assigned To`, `Cleaned?`, …) or adding a fifth keeps working — nothing
is hardcoded. If that read fails, the sync falls back to recognising the four names above.

**One case does lose ticks, by design:** when Guesty moves a booking to another listing
or date, the old slot is struck and the booking reappears as a fresh, unticked row. The
work has to be re-verified at the new location, so a carried-over `Verified` tick would
hide real work. A pure listing *rename* is treated the same way.

### Safety toggle — going live
By default the automatic daily run **does not write your sheet** — it runs as a dry-run so you
can watch a few mornings safely. When you're confident:

1. Repo → **Settings → Secrets and variables → Actions → Variables → New repository variable**
2. Name `SYNC_LIVE`, value `true`.

From then on the 4 AM run writes the sheet for real. To pause live writes again, set it to
anything else (e.g. `false`) or delete the variable. Manual runs are unaffected — they always
follow the **dry_run** checkbox on the "Run workflow" form.

---

## Verify before going live (important)

The Guesty reservation field names in `guesty_adapter.py` (`FIELD_MAP`) are best-guess
paths. Confirm them against your real data **before** the first live write:

1. Repo → **Actions → “Daily Guesty → Sheet sync” → Run workflow** → leave **dry_run = true**.
2. Open the run log. The **“FIELD_MAP resolution”** block prints, for the first reservation,
   which path matched each field (listing / guest / conf_code / city / check-in / check-out /
   times). If any says **NOT FOUND**, note the correct path from the printed top-level keys and
   edit the matching list in `guesty_adapter.py:FIELD_MAP`, then re-run the dry run.
3. Download the **sync-output** artifact and eyeball `sync_output.csv` — it's exactly what a
   live run would write. When it looks right, run the workflow again with **dry_run = false**
   (or just wait for the 4 AM schedule).

Common things to check:
- **Check-in / check-out times.** If Guesty doesn't expose per-reservation times, the adapter
  falls back to the standard 11:00 AM / 4:00 PM (so no false ECO/LCO/ECI/LCI). If your account
  *does* carry early/late times under a different field, add that path to
  `FIELD_MAP["checkin_time"]` / `["checkout_time"]`.
- **Listing name.** `listing.nickname` must resemble the old CSV `LISTING` values so
  `processing.normalize_property` splits multi-unit names correctly.

---

## Running locally (optional)

```bash
pip install -r requirements-sync.txt

# Offline test with a saved payload (no API, no sheet write):
python sync.py --from-json sample.json --dry-run

# Live fetch, but do NOT write the sheet (needs GUESTY_* env vars; SHEET_ID/GOOGLE_SA_JSON
# optional — without them it merges against an empty sheet):
export GUESTY_CLIENT_ID=... GUESTY_CLIENT_SECRET=...
python sync.py --dry-run

# Full run (also needs SHEET_ID + GOOGLE_SA_JSON):
python sync.py
```

`sync.py` will read `GUESTY_CLIENT_ID` / `GUESTY_CLIENT_SECRET` from the environment, or fall
back to `client_ID` / `SECRET_KEY` in `variables.env` if present. The dry run writes a local
`sync_output.csv` and never touches `sheet_updated.csv` or the live sheet.

---

## Safety notes
- The live write replaces only cell **values**, so checkbox **formatting/validation is
  preserved** (same as the manual paste-over-A1). Rows below the new data are value-cleared,
  which also removes superseded rows the manual paste used to leave behind.
- **The target tab's own header defines the column layout** — including the
  `assigned` / `Verified` / `OUT` / `IN` checkbox columns interleaved with the data.
  This holds even for a tab with no data rows yet; a freshly auto-created month used
  to fall back to the pipeline's 10 columns, which shifted everything from `assigned`
  onward one column left. A tab still carrying that damage is now detected and
  **skipped** with a message — clear its data rows (row 2 down) and re-run to rebuild it.
- Secrets never live in the repo — only in GitHub Actions secrets. `variables.env`,
  `.guesty_token.json`, and data CSVs are git-ignored.
- The daily job is idempotent: re-running produces the same sheet for the same Guesty data.
