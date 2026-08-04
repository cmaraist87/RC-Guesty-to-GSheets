"""
End-to-end test of the automated pipeline WITHOUT any network:
  synthetic Guesty reservations -> adapter -> processing.process_reservations
  -> sheet_merge.merge_reservations_into_sheet

Run: python test_sync_pipeline.py   (plain asserts, no pytest needed)
"""
import pandas as pd

from guesty_adapter import reservations_to_frames, requested_fields
from processing import process_reservations
from sheet_merge import merge_reservations_into_sheet

# --- Synthetic reservations shaped like the Guesty Open API payload -----------
RESERVATIONS = [
    {   # a plain turnover-eligible stay (standard times)
        "confirmationCode": "HA-ABC123",
        "status": "confirmed",
        "guest": {"fullName": "Jane Doe"},
        "listing": {"nickname": "1201 N Roman V2", "address": {"city": "New Orleans"}},
        "checkInDateLocalized": "2026-08-10",
        "checkOutDateLocalized": "2026-08-14",
    },
    {   # same-day checkout+checkin at one property on 2026-08-14 -> turnover
        "confirmationCode": "HA-DEF456",
        "status": "confirmed",
        "guest": {"fullName": "John Smith"},
        "listing": {"nickname": "1201 N Roman V2", "address": {"city": "New Orleans"}},
        "checkInDateLocalized": "2026-08-14",
        "checkOutDateLocalized": "2026-08-18",
    },
    {   # early checkout via explicit time field -> should yield ECO
        "confirmationCode": "HA-GHI789",
        "status": "confirmed",
        "guest": {"fullName": "Alice Brown"},
        "listing": {"nickname": "422 Gravier 201&202", "address": {"city": "New Orleans"}},
        "checkInDateLocalized": "2026-08-09",
        "checkOutDateLocalized": "2026-08-12",
        "plannedDeparture": "09:30",  # earlier than 11:00 AM standard
    },
]


def test_requested_fields_nonempty():
    rf = requested_fields()
    assert "status" in rf and "confirmationCode" in rf, rf
    print("requested_fields:", rf)


def test_adapter_then_processing_then_merge():
    df_co, df_ci = reservations_to_frames(RESERVATIONS)
    # adapter basics
    assert set(["LISTING", "CHECK-OUT DATE", "CHECK-OUT TIME"]).issubset(df_co.columns)
    assert len(df_co) == 3 and len(df_ci) == 3
    # early-checkout time propagated
    eco = df_co[df_co["CONFIRMATION CODE"] == "HA-GHI789"].iloc[0]
    assert eco["CHECK-OUT TIME"] == "09:30 AM", eco["CHECK-OUT TIME"]

    out = process_reservations(df_co, df_ci)
    # 422 Gravier 201&202 splits into two unit rows -> present in output
    props = set(out["Property"])
    assert "422 Gravier 201" in props and "422 Gravier 202" in props, props
    # turnover detected on 2026-08-14 at 1201 N Roman V2 -> normalized property
    roman = out[(out["Date"] == "2026-08-14") & (out["Property"].str.contains("Roman"))]
    assert (roman["T/O"] == "yes").any(), roman.to_string()
    # early checkout adjustment -- on the CHECK-OUT date row (2026-08-12)
    gravier = out[(out["Confirmation Code"] == "HA-GHI789")
                  & (out["Date"] == "2026-08-12")].iloc[0]
    assert "ECO" in gravier["Adjustments"], gravier["Adjustments"]

    # --- merge against an existing sheet that already has one of these rows ---
    # Build a sheet in the real messy layout (checkbox/spacer columns included),
    # containing the Jane Doe checkout row already ticked.
    sheet_cols = ["City", "Day", "Date", "Confirmation Code", "Guest", ".",
                  "Property", "FALSE", "Check out - Time", "FALSE.1",
                  "Check-in Time", "FALSE.2", "T/O", "Adjustments"]
    existing = pd.DataFrame([{
        "City": "New Orleans", "Day": "Friday", "Date": "2026-08-14",
        "Confirmation Code": "HA-ABC123", "Guest": "Jane Doe", ".": "TRUE",
        "Property": "1201 N Roman V2", "FALSE": "FALSE", "Check out - Time": "11:00 AM",
        "FALSE.1": "TRUE", "Check-in Time": "", "FALSE.2": "FALSE",
        "T/O": "", "Adjustments": "",
    }], columns=sheet_cols).astype(str)

    full, stats = merge_reservations_into_sheet(out, existing)
    print("merge stats:", stats)
    # The Jane Doe checkout row already existed unchanged? It becomes an UPDATE
    # because in `out` that (Property, Date) now also carries a check-in (turnover),
    # so its checkbox tick must carry over.
    assert full.columns.tolist() == sheet_cols, full.columns.tolist()
    assert stats["new"] >= 1
    # Preserved a checkbox tick somewhere (carried or existing verbatim)
    assert (full[["."]].apply(lambda s: s.str.upper() == "TRUE").any().any())
    print("OK: adapter -> processing -> merge end to end")


if __name__ == "__main__":
    test_requested_fields_nonempty()
    test_adapter_then_processing_then_merge()
    print("\nALL TESTS PASSED")
