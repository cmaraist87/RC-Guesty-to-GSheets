"""
End-to-end test of the automated pipeline WITHOUT any network:
  synthetic Guesty reservations -> adapter -> processing.process_reservations
  -> sheet_merge.merge_reservations_into_sheet

Run: python test_sync_pipeline.py   (plain asserts, no pytest needed)
"""
import pandas as pd

from guesty_adapter import reservations_to_frames, requested_fields
from processing import process_reservations
from sheet_merge import ShiftedLayoutError, merge_reservations_into_sheet

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

    full, stats, changes = merge_reservations_into_sheet(out, existing)
    print("merge stats:", stats)
    assert set(changes.keys()) == {"new", "updated", "removed", "cancelled", "moved",
                                   "missing_city_properties", "row_flags",
                                   "kept_positions"}
    assert len(changes["new"]) == stats["new"]
    assert all({"Date", "Property", "Guest", "Confirmation Code", "T/O"} <= set(r)
               for r in changes["new"])
    # The Jane Doe checkout row already existed unchanged? It becomes an UPDATE
    # because in `out` that (Property, Date) now also carries a check-in (turnover),
    # so its checkbox tick must carry over.
    assert full.columns.tolist() == sheet_cols, full.columns.tolist()
    assert stats["new"] >= 1
    # Preserved a checkbox tick somewhere (carried or existing verbatim)
    assert (full[["."]].apply(lambda s: s.str.upper() == "TRUE").any().any())
    assert len(changes["row_flags"]) == len(full)
    print("OK: adapter -> processing -> merge end to end")


# The live sheet's real layout: four checkbox columns interleaved with the data.
LIVE_HEADER = ["City", "Day", "Date", "Confirmation Code", "Guest", "assigned",
               "Property", "Verified", "Check out - Time", "OUT", "Check-in Time",
               "IN", "T/O", "Adjustments", "Unnamed: 14", "Unnamed: 15"]

CANDIDATE = {
    "City": "", "Day": "Sunday", "Date": "2026-11-01",
    "Confirmation Code": "HM2HTCNFT5", "Guest": "James Cull",
    "Property": "1022 Mandeville", "Check-out Time": "11:00 AM",
    "Check-in Time": "", "T/O": "", "Adjustments": "",
}


def test_empty_tab_keeps_the_sheets_column_layout():
    """A freshly auto-created month tab has a header but zero data rows. Its layout
    must still drive the output -- otherwise every column from `assigned` on shifts."""
    empty_tab = pd.DataFrame(columns=LIVE_HEADER)
    full, stats, changes = merge_reservations_into_sheet(
        pd.DataFrame([CANDIDATE]), empty_tab)

    assert list(full.columns) == LIVE_HEADER, list(full.columns)
    row = full.iloc[0]
    assert row["Property"] == "1022 Mandeville", row.to_dict()
    assert row["Check out - Time"] == "11:00 AM", row.to_dict()
    assert row["Guest"] == "James Cull"
    assert row["City"] == "New Orleans"  # resolved from property_to_city.csv
    # Checkbox columns are recognised by header name when there's no data to sample.
    for cb in ("assigned", "Verified", "OUT", "IN"):
        assert row[cb] == "FALSE", f"{cb} should default to an unticked checkbox"
    assert row["Unnamed: 14"] == "" and row["Unnamed: 15"] == ""
    assert changes["row_flags"] == ["new"], changes["row_flags"]
    print("OK: empty tab keeps the sheet's 16-column layout, checkboxes default FALSE")


def test_cancelled_rows_are_struck_not_deleted():
    existing = pd.DataFrame([
        # still live -> untouched
        dict(zip(LIVE_HEADER, ["New Orleans", "Sunday", "2026-11-01", "HM2HTCNFT5",
                               "James Cull", "TRUE", "1022 Mandeville", "FALSE",
                               "11:00 AM", "FALSE", "", "FALSE", "", "", "", ""])),
        # gone from Guesty, inside the window -> cancelled
        dict(zip(LIVE_HEADER, ["Savannah", "Sunday", "2026-11-01", "HMGONE0001",
                               "Ghost Booking", "FALSE", "6 Lake", "FALSE",
                               "11:00 AM", "FALSE", "", "FALSE", "", "", "", ""])),
        # outside the coverage window -> must NOT be struck
        dict(zip(LIVE_HEADER, ["Austin", "Wednesday", "2027-06-02", "HMFUTURE01",
                               "Far Future", "FALSE", "6504 Porter A", "FALSE",
                               "11:00 AM", "FALSE", "", "FALSE", "", "", "", ""])),
    ], columns=LIVE_HEADER).astype(str)

    window = ("2026-10-01", "2026-12-31")
    full, stats, changes = merge_reservations_into_sheet(
        pd.DataFrame([CANDIDATE]), existing, cancel_window=window)

    assert stats["cancelled"] == 1, stats
    assert stats["unchanged"] == 1, stats
    assert changes["cancelled"][0]["Confirmation Code"] == "HMGONE0001"
    # Struck rows stay in the sheet; nothing is deleted.
    assert len(full) == 3, full.to_string()
    assert changes["row_flags"] == ["", "cancelled", ""], changes["row_flags"]

    # Second run: the row is already struck, so it isn't re-reported, and its ticks
    # no longer match a re-booking of the same property/date.
    full2, stats2, changes2 = merge_reservations_into_sheet(
        pd.DataFrame([CANDIDATE]), existing, cancel_window=window, struck_rows={1})
    assert stats2["cancelled"] == 0, stats2
    assert changes2["row_flags"] == ["", "struck", ""], changes2["row_flags"]
    print("OK: cancellations struck once, kept in place, window-bounded")


def test_rebooking_over_a_struck_row_lands_as_new():
    struck = pd.DataFrame([
        dict(zip(LIVE_HEADER, ["Savannah", "Sunday", "2026-11-01", "HMOLD00001",
                               "Cancelled Guest", "FALSE", "6 Lake", "FALSE",
                               "11:00 AM", "FALSE", "", "FALSE", "", "", "", ""])),
    ], columns=LIVE_HEADER).astype(str)
    rebooked = dict(CANDIDATE, Property="6 Lake", Guest="New Guest",
                    **{"Confirmation Code": "HMNEW00001"})

    full, stats, changes = merge_reservations_into_sheet(
        pd.DataFrame([rebooked]), struck,
        cancel_window=("2026-10-01", "2026-12-31"), struck_rows={0})

    assert stats["new"] == 1 and stats["updated"] == 0, stats
    assert changes["row_flags"] == ["struck", "new"], changes["row_flags"]
    assert full.iloc[1]["Guest"] == "New Guest"
    print("OK: re-booking a struck property/date appends a new highlighted row")


def test_cancel_guard_blocks_a_short_fetch():
    """If the Guesty fetch comes back short, a whole month looks cancelled. Don't
    strike it -- report the anomaly and leave the tab alone."""
    rows = [dict(zip(LIVE_HEADER,
                     ["New Orleans", "Sunday", "2026-11-01", f"HM{i:08d}",
                      f"Guest {i}", "FALSE", f"{i} Somewhere", "FALSE",
                      "11:00 AM", "FALSE", "", "FALSE", "", "", "", ""]))
            for i in range(30)]
    existing = pd.DataFrame(rows, columns=LIVE_HEADER).astype(str)

    full, stats, changes = merge_reservations_into_sheet(
        pd.DataFrame([CANDIDATE]), existing, cancel_window=("2026-10-01", "2026-12-31"))

    assert stats["cancelled"] == 0, stats
    assert stats["cancel_guard_tripped"], stats
    assert set(changes["row_flags"]) == {"", "new"}, set(changes["row_flags"])
    print("OK: cancel guard blocks a mass strike -> " + stats["cancel_guard_tripped"])


def _row(**over):
    """One sheet row in the live 16-column layout."""
    base = dict(zip(LIVE_HEADER,
                    ["New Orleans", "Sunday", "2026-11-01", "HM00000000", "Guest",
                     "FALSE", "Somewhere", "FALSE", "11:00 AM", "FALSE", "",
                     "FALSE", "", "", "", ""]))
    base.update(over)
    return base


def test_reassignment_is_reported_as_moved_not_cancelled():
    """Guesty reassigns a booking to another listing. The old slot is still struck,
    but calling it a cancellation misleads whoever reads the tab."""
    existing = pd.DataFrame([
        _row(**{"Date": "2026-11-12", "Confirmation Code": "HMMOVE0001",
                "Guest": "Elizabeth Quam", "Property": "3930 Burgundy"}),
        _row(**{"Date": "2026-11-01", "Confirmation Code": "HMGONE0001",
                "Guest": "Ghost Booking", "Property": "6 Lake"}),
    ], columns=LIVE_HEADER).astype(str)

    # The same code now sits at another listing -- twice, as any stay produces a
    # check-out and a check-in row. 11-09 is nearer the struck 11-12 than 11-20.
    moved_out = dict(CANDIDATE, Property="1405 Carondelet B", Guest="Elizabeth Quam",
                     Date="2026-11-09", **{"Confirmation Code": "HMMOVE0001"})
    moved_in = dict(moved_out, Date="2026-11-20")

    full, stats, changes = merge_reservations_into_sheet(
        pd.DataFrame([CANDIDATE, moved_out, moved_in]), existing,
        cancel_window=("2026-10-01", "2026-12-31"))

    assert stats["moved"] == 1, stats
    assert stats["cancelled"] == 1, stats
    # Only the vanished booking is called a cancellation.
    assert [r["Confirmation Code"] for r in changes["cancelled"]] == ["HMGONE0001"]
    mv = changes["moved"][0]
    assert mv["Confirmation Code"] == "HMMOVE0001", mv
    assert mv["Property"] == "3930 Burgundy", mv
    assert "1405 Carondelet B" in mv["Now at"] and "2026-11-09" in mv["Now at"], mv
    # Both old slots are struck -- a move is just as dead as a cancellation.
    assert changes["row_flags"][:2] == ["moved", "cancelled"], changes["row_flags"]
    assert len(full) == 5, full.to_string()  # 2 kept + 3 appended
    print("OK: reassignment reported as moved (with its destination), still struck")


def test_cancel_guard_counts_cancellations_but_not_moves():
    """The guard catches a SHORT FETCH. A moved row proves its reservation did
    arrive, so it must neither inflate the ratio nor be spared when the guard trips."""
    existing = pd.DataFrame(
        # 5 reassigned + 25 genuinely vanished = 30 in-window rows.
        [_row(**{"Confirmation Code": f"HMMOVED{i:03d}", "Property": f"{i} Old Place"})
         for i in range(5)]
        + [_row(**{"Confirmation Code": f"HMGONE{i:04d}", "Property": f"{i} Somewhere"})
           for i in range(25)],
        columns=LIVE_HEADER).astype(str)
    candidates = pd.DataFrame(
        [dict(CANDIDATE, Property=f"{i} New Place",
              **{"Confirmation Code": f"HMMOVED{i:03d}"}) for i in range(5)])

    _, stats, changes = merge_reservations_into_sheet(
        candidates, existing, cancel_window=("2026-10-01", "2026-12-31"))

    # 25 of 30 would be struck as cancelled -> a short fetch, so none of them are.
    assert stats["cancelled"] == 0, stats
    assert stats["cancel_guard_tripped"], stats
    # The 5 confirmed reassignments survive the guard and stay struck.
    assert stats["moved"] == 5, stats
    assert changes["row_flags"].count("moved") == 5, changes["row_flags"]
    assert "reassignment" in stats["cancel_guard_tripped"], stats["cancel_guard_tripped"]
    print("OK: guard blocks the mass strike, confirmed moves still struck")


def test_moves_alone_never_trip_the_guard():
    """A whole month of reassignments is not a short fetch -- every code came back."""
    existing = pd.DataFrame(
        [_row(**{"Confirmation Code": f"HMMOVED{i:03d}", "Property": f"{i} Old Place"})
         for i in range(30)], columns=LIVE_HEADER).astype(str)
    candidates = pd.DataFrame(
        [dict(CANDIDATE, Property=f"{i} New Place",
              **{"Confirmation Code": f"HMMOVED{i:03d}"}) for i in range(30)])

    _, stats, _ = merge_reservations_into_sheet(
        candidates, existing, cancel_window=("2026-10-01", "2026-12-31"))

    assert not stats["cancel_guard_tripped"], stats
    assert stats["moved"] == 30 and stats["cancelled"] == 0, stats
    print("OK: 30 moves and zero cancellations leave the guard untripped")


def test_upstream_city_survives_the_merge():
    """Guesty's listing.address.city is authoritative and covers properties no CSV
    knows about. The merge used to overwrite City from property_to_city.csv, which
    threw that away and then reported the property as missing a city."""
    # A property deliberately absent from property_to_city.csv.
    cand = dict(CANDIDATE, Property="2407 Hyde A", City="Scottsdale")
    full, stats, changes = merge_reservations_into_sheet(
        pd.DataFrame([cand]), pd.DataFrame(columns=LIVE_HEADER))

    assert full.iloc[0]["City"] == "Scottsdale", full.iloc[0].to_dict()
    assert stats["missing_city"] == 0, stats
    assert changes["missing_city_properties"] == [], changes["missing_city_properties"]

    # A blank City still falls back to the CSV, as before.
    blank = dict(CANDIDATE, Property="1022 Mandeville", City="")
    full2, _, _ = merge_reservations_into_sheet(
        pd.DataFrame([blank]), pd.DataFrame(columns=LIVE_HEADER))
    assert full2.iloc[0]["City"] == "New Orleans", full2.iloc[0].to_dict()

    # ...and a property in NEITHER is still reported, so real gaps stay visible.
    gap = dict(CANDIDATE, Property="9020 Blackjack 16", City="")
    _, stats3, changes3 = merge_reservations_into_sheet(
        pd.DataFrame([gap]), pd.DataFrame(columns=LIVE_HEADER))
    assert stats3["missing_city"] == 1, stats3
    assert changes3["missing_city_properties"] == ["9020 Blackjack 16"]
    print("OK: upstream City wins, CSV fills blanks, real gaps still reported")


def test_duplicate_rows_do_not_lose_a_tick():
    """One (Property, Date) slot can hold several rows -- Agosto 2026 had ~75 dupes.
    All of them are dropped for the single re-written row, so a tick set on ANY of
    them has to survive; carrying only the first silently loses the team's work."""
    dupes = pd.DataFrame([
        # The tick sits on the SECOND copy, and on a different column of the third.
        _row(**{"Property": "1022 Mandeville", "Confirmation Code": "HM2HTCNFT5",
                "Guest": "James Cull", "Check out - Time": "09:00 AM"}),
        _row(**{"Property": "1022 Mandeville", "Confirmation Code": "HM2HTCNFT5",
                "Guest": "James Cull", "Check out - Time": "09:00 AM",
                "assigned": "TRUE"}),
        _row(**{"Property": "1022 Mandeville", "Confirmation Code": "HM2HTCNFT5",
                "Guest": "James Cull", "Check out - Time": "09:00 AM",
                "OUT": "TRUE"}),
    ], columns=LIVE_HEADER).astype(str)

    # Same slot, but a changed check-out time -> an "updated" row supersedes all three.
    full, stats, changes = merge_reservations_into_sheet(
        pd.DataFrame([CANDIDATE]), dupes)

    assert stats["updated"] == 1 and stats["removed"] == 3, stats
    assert len(full) == 1, full.to_string()
    row = full.iloc[0]
    assert row["assigned"] == "TRUE", "a tick on the 2nd duplicate must survive"
    assert row["OUT"] == "TRUE", "a tick on the 3rd duplicate must survive too"
    assert row["Verified"] == "FALSE" and row["IN"] == "FALSE", row.to_dict()
    print("OK: ticks are ORed across duplicate rows, not taken from the first only")


def test_validated_checkbox_columns_beat_the_header_guess():
    """When the tab reports which columns carry checkbox validation, that wins over
    the hardcoded name list -- so oddly-named checkbox columns still work."""
    header = ["City", "Day", "Date", "Confirmation Code", "Guest", "Assigned To",
              "Property", "Verified By", "Check out - Time", "Cleaned?",
              "Check-in Time", "Keys Returned", "T/O", "Adjustments", "", ""]
    empty = pd.DataFrame(columns=header)
    odd = {"Assigned To", "Verified By", "Cleaned?", "Keys Returned"}

    # Without the hint, none of these names are in CHECKBOX_HEADER_NAMES.
    guessed = merge_reservations_into_sheet(pd.DataFrame([CANDIDATE]), empty)[0].iloc[0]
    assert all(guessed[c] == "" for c in odd), guessed.to_dict()

    # With it, every one of them is written as an unticked checkbox.
    told = merge_reservations_into_sheet(
        pd.DataFrame([CANDIDATE]), empty,
        validated_checkboxes=frozenset(odd))[0].iloc[0]
    assert all(told[c] == "FALSE" for c in odd), told.to_dict()
    # A pipeline data column is never stolen, even if the sheet marks it validated.
    still_data = merge_reservations_into_sheet(
        pd.DataFrame([CANDIDATE]), empty,
        validated_checkboxes=frozenset(odd | {"Property"}))[0].iloc[0]
    assert still_data["Property"] == "1022 Mandeville", still_data.to_dict()
    print("OK: the tab's own checkbox validation overrides the header-name guess")


def test_shifted_layout_raises_a_typed_error():
    """The pre-fix layout slid every column left of `Property`, so check-out times
    landed in it. The caller needs to tell this apart from any other bad layout."""
    shifted = pd.DataFrame([
        _row(Property="11:00 AM"), _row(Property="04:00 PM"),
    ], columns=LIVE_HEADER).astype(str)

    try:
        merge_reservations_into_sheet(pd.DataFrame([CANDIDATE]), shifted)
    except ShiftedLayoutError as e:
        assert isinstance(e, ValueError), "callers still catching ValueError must work"
        assert "Property column holds times" in str(e), e
    else:
        raise AssertionError("a shifted tab must not merge")

    # A tab whose Property column holds real addresses is left alone.
    ok = pd.DataFrame([_row(Property="1022 Mandeville")], columns=LIVE_HEADER).astype(str)
    merge_reservations_into_sheet(pd.DataFrame([CANDIDATE]), ok)
    print("OK: shifted layout raises ShiftedLayoutError (a ValueError subclass)")


def test_canonical_property_spelling_is_not_a_cancellation():
    existing = pd.DataFrame([
        dict(zip(LIVE_HEADER, ["Savannah", "Sunday", "2026-11-01", "HMDUFFY001",
                               "Sean Finlay", "FALSE", "105 E Duffy1&2&CH", "FALSE",
                               "11:00 AM", "FALSE", "", "FALSE", "", "", "", ""])),
    ], columns=LIVE_HEADER).astype(str)
    # Same unit, different spelling of the same name.
    cand = dict(CANDIDATE, Property="105 Duffy 1&2 CH", Guest="Sean Finlay",
                **{"Confirmation Code": "HMDUFFY001"})

    _, stats, changes = merge_reservations_into_sheet(
        pd.DataFrame([cand]), existing, cancel_window=("2026-10-01", "2026-12-31"))

    assert stats["cancelled"] == 0, changes["cancelled"]
    print("OK: cosmetic property-name drift is not read as a cancellation")


if __name__ == "__main__":
    test_requested_fields_nonempty()
    test_adapter_then_processing_then_merge()
    test_empty_tab_keeps_the_sheets_column_layout()
    test_cancelled_rows_are_struck_not_deleted()
    test_rebooking_over_a_struck_row_lands_as_new()
    test_cancel_guard_blocks_a_short_fetch()
    test_reassignment_is_reported_as_moved_not_cancelled()
    test_cancel_guard_counts_cancellations_but_not_moves()
    test_moves_alone_never_trip_the_guard()
    test_upstream_city_survives_the_merge()
    test_duplicate_rows_do_not_lose_a_tick()
    test_validated_checkbox_columns_beat_the_header_guess()
    test_shifted_layout_raises_a_typed_error()
    test_canonical_property_spelling_is_not_a_cancellation()
    print("\nALL TESTS PASSED")
