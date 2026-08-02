# Audit: run_reservations.py

## 1. Function Map

| Name | Lines | What it does | Dependencies |
|---|---|---|---|
| `normalize_property(listing)` | 59–120 | Takes a raw Guesty listing string, strips F2X prefix, expands street abbreviations, removes version/unit markers (V, V1, VII, U), then pattern-matches multi-unit combos (digit ranges, letter ranges, hyphen ranges, building-number ranges). Returns a `list` of one or more normalized address strings. | `re` only — no I/O, no pandas |
| `parse_dt(value)` | 128–134 | Tries two datetime format strings against a combined date+time string and returns a `datetime` object. Raises `ValueError` if neither matches. | `datetime` only — pure |
| `compute_adjustments(co_time, ci_time)` | 139–153 | Given formatted time strings (`"11:00 AM"`), compares against standard checkout (11 AM) and checkin (4 PM) thresholds and returns a comma-joined string of adjustment codes (`ECO`, `LCO`, `ECI`, `LCI`). | `datetime` only — pure |

Everything else (lines 47–56, 156–242) is top-level script glue — no enclosing function.

---

## 2. Filesystem and Environment Coupling

| Line(s) | What happens | Notes |
|---|---|---|
| 6 | `SOURCE_CSV_CHECKOUT = r"374881_2026-06-19_10_17_20_checkout.csv"` | Hardcoded relative path. Resolves against **current working directory** at runtime. Changes with every new Guesty export. |
| 7 | `SOURCE_CSV_CHECKIN = r"280583_2026-06-19_10_15_47_checkin.csv"` | Same issue. |
| 8 | `OUTPUT_CSV = r"guesty_res.csv"` | Hardcoded relative output path. Also doubles as the city-lookup seed (see below). |
| 47 | `pd.read_csv(SOURCE_CSV_CHECKOUT)` | Reads checkout CSV from disk. |
| 48 | `pd.read_csv(SOURCE_CSV_CHECKIN)` | Reads checkin CSV from disk. |
| 157 | `os.path.exists(OUTPUT_CSV)` | Checks whether the output file already exists on disk. |
| 159 | `pd.read_csv(OUTPUT_CSV)` | Reads the **previous run's output** to seed the `city_lookup` dict. This is the only source of city data once the source CSVs provide it — but it also serves as fallback when source CSVs are missing city. |
| 241 | `df_out.to_csv(OUTPUT_CSV, index=False)` | Writes final output in-place, overwriting whatever was at `OUTPUT_CSV`. |

**Working directory assumption:** All three paths are bare filenames with no directory component. The script assumes it is run from the folder that contains the CSVs. On Hugging Face Spaces there is no such folder; uploaded files arrive as temporary paths.

---

## 3. Input and Output Contract

### Checkout CSV (`SOURCE_CSV_CHECKOUT`)
| Column | Required | Format / Notes |
|---|---|---|
| `LISTING` | Yes | Free-text Guesty listing name (e.g. `F2X 1130 Baro VII 3 U V1 / Marketing name`) |
| `CHECK-OUT DATE` | Yes | `YYYY-MM-DD` (e.g. `2026-06-19`) |
| `CHECK-OUT TIME` | Yes | `HH:MM AM/PM` (e.g. `11:00 AM`) or `H:MM` 24-hr (e.g. `11:00`) — both handled by `parse_dt` |
| `LISTING'S CITY` | Optional | City string; used to populate the City column. Empty cells silently skipped. |
| `CONFIRMATION CODE` | Optional | Booking code; silently empty if absent. |
| `GUEST` | Optional | Guest name; silently empty if absent. |

Header row is assumed present. Column names are whitespace-stripped at line 50.

### Checkin CSV (`SOURCE_CSV_CHECKIN`)
Same structure as above except uses `CHECK-IN DATE` and `CHECK-IN TIME` instead of checkout columns. Same optionality rules apply.

### Previous output CSV (`OUTPUT_CSV` — city seed)
| Column | Required | Notes |
|---|---|---|
| `Property` | Yes (if file exists) | Must match normalized property names exactly |
| `City` | Yes (if file exists) | Any non-empty, non-`nan` string is used |

File does not need to exist on first run; the `city_lookup` starts empty and City cells will be blank.

### Output CSV
10 columns in this order:

| Column | Source |
|---|---|
| `City` | `city_lookup` (seeded from source CSVs, fallback from previous output) |
| `Day` | Derived from `Date` via `strftime("%A")` |
| `Date` | Normalized to `YYYY-MM-DD` |
| `Confirmation Code` | From `guest_lookup`; checkin guest takes priority for T/O rows |
| `Guest` | Same |
| `Property` | Output of `normalize_property()` |
| `Check-out Time` | Formatted `%I:%M %p` or empty |
| `Check-in Time` | Formatted `%I:%M %p` or empty |
| `T/O` | `"yes"` if both times present, else `""` |
| `Adjustments` | Output of `compute_adjustments()`; comma-joined codes or `""` |

Sorted by `Date`, then `City`, then `Property`. No index column.

---

## 4. Portability Split

### Pure (no filesystem, safe to wrap unchanged)

| Code | Lines |
|---|---|
| `normalize_property()` | 59–120 |
| `parse_dt()` | 128–134 |
| `_DT_FORMATS` tuple | 123–126 |
| `_CHECKOUT_STD`, `_CHECKIN_STD` constants | 136–137 |
| `compute_adjustments()` | 139–153 |
| `EXCLUDE_PROPERTIES` / `_EXCLUDE_LOWER` | 12–45 |
| Lookup-building loops (lines 179–200) | 179–200 — pure dict/DataFrame ops once DataFrames are in memory |
| Event aggregation and output-row building (lines 202–225) | 202–225 — pure |
| DataFrame construction and sort (lines 228–232) | 228–232 — pure |

### Glue (filesystem coupling — must be replaced for Gradio)

| Code | Lines | What replaces it |
|---|---|---|
| `SOURCE_CSV_CHECKOUT = ...` | 6 | Gradio `gr.File` upload → in-memory path or `BytesIO` |
| `SOURCE_CSV_CHECKIN = ...` | 7 | Same |
| `OUTPUT_CSV = ...` | 8 | Temp file path for download; also needs a strategy for city seed |
| `pd.read_csv(SOURCE_CSV_CHECKOUT)` | 47 | `pd.read_csv(checkout_upload.name)` or equivalent |
| `pd.read_csv(SOURCE_CSV_CHECKIN)` | 48 | Same |
| `os.path.exists(OUTPUT_CSV)` | 157 | Remove or replace with in-memory check |
| `pd.read_csv(OUTPUT_CSV)` (city seed) | 159 | See Risk #3 below |
| `df_out.to_csv(OUTPUT_CSV, index=False)` | 241 | Write to a `tempfile`, return path to Gradio for download |
| `print(...)` statements | 53–56, 165, 169, 234–239, 242 | Optional: route to Gradio status text or drop |

---

## 5. Risks

**Risk 1 — Hardcoded timestamped filenames (lines 6–7)**
The CSV filenames change with every Guesty export. On a hosted app this is moot (user uploads the file), but locally this requires a manual edit every export cycle. Not a Gradio blocker, just operational friction.

**Risk 2 — Working directory dependency (lines 6–8, 47–48, 241)**
All paths are bare filenames. If the script is imported as a module or run from a different directory, all three `read_csv`/`to_csv` calls fail with `FileNotFoundError`. The Gradio wrapper must use the uploaded file object's `.name` attribute (a temp path) rather than these constants.

**Risk 3 — City seed reads from the previous output file (lines 157–169)**
On Hugging Face Spaces, the filesystem is ephemeral — it resets between cold starts and is not shared across users. The `guesty_res.csv` city seed will not persist between sessions. Options:
- Accept that City will be blank on first-ever run and populate on subsequent runs within the same session (current behavior, works if session is warm).
- Add a third optional upload for a "previous output" CSV to carry city data across sessions.
- Hardcode a city reference dict (maps normalized property names to cities) as a static asset bundled with the Space.

This is the most significant behavioral risk for the hosted version.

**Risk 4 — In-place output overwrite (line 241)**
`df_out.to_csv(OUTPUT_CSV, index=False)` overwrites `guesty_res.csv` on every run. On a shared Gradio Space with concurrent users, two simultaneous runs would corrupt each other's output. The fix is to write to a `tempfile.NamedTemporaryFile` scoped to each request.

**Risk 5 — `datetime.strptime` is locale-independent**
`parse_dt` uses explicit format strings with no locale-sensitive tokens (`%A`, `%B`, etc. are not in the input formats). Day names in the output (`strftime("%A")` at line 217) will be in English regardless of server locale. No risk here unless non-English day names are required.

**Risk 6 — No input validation**
If either uploaded CSV is missing a required column (`LISTING`, `CHECK-OUT DATE`, etc.), the script raises a `KeyError` with a Python traceback rather than a user-readable error message. The Gradio wrapper should catch this and surface a clean message.

---

## Open Questions

1. **City persistence strategy** — how should city data survive across sessions on Spaces? Third upload, bundled static file, or accept blank-on-cold-start?
2. **Two-file upload UX** — should checkout and checkin be two separate upload widgets, or a single multi-file upload? (Two separate is clearer for non-technical users.)
3. **Private vs. password-gated** — Hugging Face Spaces "private" restricts to the org; "Spaces Secrets + authentication" adds a login wall. Which is needed?
4. **Output filename** — should the downloaded file be named `guesty_res.csv` always, or include a datestamp?
