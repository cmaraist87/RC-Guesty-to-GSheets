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
> that tab's checkboxes. Non-month tabs (`guesty_res`, pivot tables) are ignored. A
> month with reservations but **no matching tab is skipped and reported** (create a
> tab named e.g. `Septiembre 2026` to include it). `WORKSHEET_NAME` is therefore not
> used for the monthly workflow.

**Variables** (optional — sensible defaults apply if omitted):
| Name | Default | Meaning |
|------|---------|---------|
| `SYNC_LIVE` | *(unset)* | **Safety toggle.** Scheduled 4 AM runs only write when this is exactly `true`. Until then they run as a dry-run. |
| `WORKSHEET_NAME` | *(unused)* | Ignored in the monthly-tab workflow; tabs are auto-detected by name. |
| `SYNC_LOOKBACK_DAYS` | `1` | include check-outs from N days ago |
| `SYNC_LOOKAHEAD_DAYS` | `180` | include check-ins up to N days ahead |
| `SYNC_STATUSES` | `confirmed,reserved,checkedIn` | reservation statuses to include |

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
- Secrets never live in the repo — only in GitHub Actions secrets. `variables.env`,
  `.guesty_token.json`, and data CSVs are git-ignored.
- The daily job is idempotent: re-running produces the same sheet for the same Guesty data.
