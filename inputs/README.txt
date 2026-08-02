DROP THESE 3 FILES HERE EACH RUN
================================

1. Guesty check-out CSV   (has a "CHECK-OUT DATE" column)
2. Guesty check-in CSV    (has a "CHECK-IN DATE" column)
3. Your current Google Sheet, downloaded as CSV (the ForecastNOLA sheet)

Then open reservations.ipynb (in the folder above) and Run All.
It writes forecast_export.csv up in the main folder — import that back
into your Google Sheet.

DO I NEED TO DELETE THE OLD FILES FIRST?
----------------------------------------
No. The notebook automatically uses the NEWEST file of each type, so
leftover older exports are ignored. But clearing this folder between runs
keeps things tidy and avoids confusion — your call.

Notes:
- The filenames don't matter; files are identified by their columns.
- property_to_city.csv stays in the main folder (it's config, not an input).
