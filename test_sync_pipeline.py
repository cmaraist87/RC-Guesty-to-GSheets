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
                                   "out_of_scope", "missing_city_properties",
                                   "row_flags", "kept_positions"}
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


def test_billing_listings_never_become_properties():
    """'Billing …' listings are accounting placeholders, not places anyone cleans.
    The live catalogue is full of them and at least one is ACTIVE in New Orleans, so
    it would pass the city filter and appear in the schedule as a job."""
    from processing import normalize_property

    assert normalize_property("Billing 3223 Canal MU V1") == []
    assert normalize_property("billing 109 Warren VD") == [], "must be case-insensitive"
    # Stripping the prefix instead would be worse: the row would merge into the real
    # property and invent a turnover out of a billing record.
    assert normalize_property("3223 Canal") == ["3223 Canal"]
    # A genuine property that merely starts with those letters is untouched.
    assert normalize_property("Billings Bridge 4") == ["Billings Bridge 4"]

    # End to end: a reservation on a billing listing produces no schedule row at all.
    out = process_reservations(*reservations_to_frames([{
        "confirmationCode": "HMBILL01", "status": "confirmed",
        "guest": {"fullName": "Ledger Entry"},
        "listing": {"nickname": "Billing 3223 Canal MU V1",
                    "address": {"city": "New Orleans"}},
        "checkInDateLocalized": "2026-08-10", "checkOutDateLocalized": "2026-08-14"}]))
    assert out.empty, out.to_string()
    print("OK: billing placeholders map to nothing and produce no schedule rows")


def test_listing_id_is_carried_through_the_adapter():
    """Nicknames are neither unique nor stable, so anything reasoning about 'which
    listing changed' needs the id. Nothing captured it before."""
    co, ci = reservations_to_frames([
        {"confirmationCode": "HM1", "guest": {"fullName": "G"},
         "listing": {"_id": "LID-abc", "nickname": "1022 Mandeville",
                     "address": {"city": "New Orleans"}},
         "checkInDateLocalized": "2026-08-10", "checkOutDateLocalized": "2026-08-14"},
        # listingId as a bare scalar rather than a populated object.
        {"confirmationCode": "HM2", "guest": {"fullName": "H"},
         "listingId": "LID-bare", "listing": {"nickname": "6 Lake",
                                              "address": {"city": "Savannah"}},
         "checkInDateLocalized": "2026-08-11", "checkOutDateLocalized": "2026-08-15"},
    ])
    assert "LISTING ID" in co.columns and "LISTING ID" in ci.columns
    assert list(co["LISTING ID"]) == ["LID-abc", "LID-bare"], co.to_string()
    # Appended last, so the manual CSV exports still line up positionally.
    assert co.columns[-1] == "LISTING ID"
    # A payload with no id at all degrades to blank rather than failing.
    co2, _ = reservations_to_frames([{
        "confirmationCode": "HM3", "guest": {"fullName": "I"},
        "listing": {"nickname": "6 Lake"},
        "checkInDateLocalized": "2026-08-11", "checkOutDateLocalized": "2026-08-15"}])
    assert list(co2["LISTING ID"]) == [""], co2.to_string()
    print("OK: listing id is captured from both payload shapes, blank when absent")


def test_turnover_row_keeps_both_confirmation_codes():
    """On a turnover date a check-out and a check-in share one (Property, Date).
    A single guest_lookup meant the check-in pass overwrote the check-out pass, so
    the DEPARTING reservation's code vanished from the one row a cleaner works --
    and a cancellation of that stay could never be found by code."""
    res = [
        {"confirmationCode": "CODE-OUTGOING", "status": "confirmed",
         "guest": {"fullName": "Departing Guest"},
         "listing": {"nickname": "1022 Mandeville", "address": {"city": "New Orleans"}},
         "checkInDateLocalized": "2026-08-10", "checkOutDateLocalized": "2026-08-14"},
        {"confirmationCode": "CODE-INCOMING", "status": "confirmed",
         "guest": {"fullName": "Arriving Guest"},
         "listing": {"nickname": "1022 Mandeville", "address": {"city": "New Orleans"}},
         "checkInDateLocalized": "2026-08-14", "checkOutDateLocalized": "2026-08-18"},
    ]
    out = process_reservations(*reservations_to_frames(res))
    by_date = {r["Date"]: r for _, r in out.iterrows()}

    to = by_date["2026-08-14"]
    assert to["T/O"] == "yes", to.to_dict()
    # Both sides of the handover are now addressable on the turnover row.
    assert to["Out Code"] == "CODE-OUTGOING", to.to_dict()
    assert to["In Code"] == "CODE-INCOMING", to.to_dict()
    # ...and the pre-existing column keeps its old meaning exactly: check-in wins.
    assert to["Confirmation Code"] == "CODE-INCOMING", to.to_dict()
    assert to["Guest"] == "Arriving Guest", to.to_dict()

    # A check-in-only day carries no departing code, and vice versa.
    arrive = by_date["2026-08-10"]
    assert arrive["Out Code"] == "" and arrive["In Code"] == "CODE-OUTGOING", arrive.to_dict()
    depart = by_date["2026-08-18"]
    assert depart["Out Code"] == "CODE-INCOMING" and depart["In Code"] == "", depart.to_dict()

    # The departing stay is now findable by code on the turnover row -- the whole point.
    hits = out[(out["Out Code"] == "CODE-OUTGOING") | (out["In Code"] == "CODE-OUTGOING")]
    assert "2026-08-14" in set(hits["Date"]), hits.to_string()
    print("OK: turnover row carries both the departing and arriving codes")


def test_operator_columns_survive_a_rewrite():
    """A column the sync does not own must not be blanked when its row is rewritten
    as 'updated'. Shift IDs written during the day were being erased by the next
    nightly run, silently, and only on rows that changed."""
    header = LIVE_HEADER + ["Shift ID", "Shift Synced", "Crew Notes"]
    existing = pd.DataFrame([
        dict(zip(header, ["New Orleans", "Sunday", "2026-11-01", "HM2HTCNFT5",
                          "James Cull", "TRUE", "1022 Mandeville", "FALSE",
                          "09:00 AM", "FALSE", "", "FALSE", "", "", "", "",
                          "CT-SHIFT-991", "2026-11-01T10:04:00Z", "gate code 4417"])),
    ], columns=header).astype(str)

    # Same slot, changed check-out time -> the row is rewritten as "updated".
    full, stats, changes = merge_reservations_into_sheet(
        pd.DataFrame([CANDIDATE]), existing)

    assert stats["updated"] == 1, stats
    row = full.iloc[0]
    assert row["Shift ID"] == "CT-SHIFT-991", row.to_dict()
    assert row["Shift Synced"] == "2026-11-01T10:04:00Z", row.to_dict()
    assert row["Crew Notes"] == "gate code 4417", row.to_dict()
    # The pipeline still owns its own columns -- the new time did land.
    assert row["Check out - Time"] == "11:00 AM", row.to_dict()
    # And the checkbox tick still carries, as before.
    assert row["assigned"] == "TRUE", row.to_dict()

    # A genuinely new row gets blanks, not inherited operator values.
    fresh = merge_reservations_into_sheet(
        pd.DataFrame([dict(CANDIDATE, Property="6 Lake", City="Savannah")]),
        pd.DataFrame(columns=header))[0].iloc[0]
    assert fresh["Shift ID"] == "" and fresh["Crew Notes"] == "", fresh.to_dict()
    print("OK: operator columns carry across a rewrite; new rows start empty")


def test_operator_column_carries_from_a_duplicate_row():
    """Duplicate rows share one slot and are all superseded together. A Shift ID set
    on the second copy must survive, exactly as a checkbox tick does."""
    header = LIVE_HEADER + ["Shift ID"]
    base = ["New Orleans", "Sunday", "2026-11-01", "HM2HTCNFT5", "James Cull",
            "FALSE", "1022 Mandeville", "FALSE", "09:00 AM", "FALSE", "", "FALSE",
            "", "", "", ""]
    existing = pd.DataFrame([
        dict(zip(header, base + [""])),               # first copy: no shift
        dict(zip(header, base + ["CT-SHIFT-772"])),   # second copy holds it
    ], columns=header).astype(str)

    full, stats, _ = merge_reservations_into_sheet(
        pd.DataFrame([CANDIDATE]), existing)

    assert stats["removed"] == 2, stats
    assert len(full) == 1, full.to_string()
    assert full.iloc[0]["Shift ID"] == "CT-SHIFT-772", full.iloc[0].to_dict()
    print("OK: operator value on a duplicate row survives the collapse")


def test_city_filter_keeps_only_covered_markets():
    """Guesty carries listings this team does not service. Those rows are not a data
    gap to be filled -- they must never reach a tab."""
    import sync

    cands = pd.DataFrame([
        dict(CANDIDATE, Property="1022 Mandeville", City="New Orleans"),
        dict(CANDIDATE, Property="6 Lake", City="Savannah"),
        dict(CANDIDATE, Property="315 Main 1 A", City="bay saint louis"),  # spelling drift
        dict(CANDIDATE, Property="8249 E Chaparral", City="Scottsdale"),
        dict(CANDIDATE, Property="2407 Hyde A", City="Boston"),
        dict(CANDIDATE, Property="Website TEST", City=""),               # unknown scope
    ])
    kept, removed = sync.filter_to_cities(cands, sync.DEFAULT_CITIES)

    assert list(kept["Property"]) == ["1022 Mandeville", "6 Lake", "315 Main 1 A"], \
        list(kept["Property"])
    # "bay saint louis" must match "Bay St. Louis" -- a near-miss would silently drop
    # a whole market.
    assert kept.iloc[2]["City"] == "bay saint louis"
    # A blank city is dropped, not assumed in scope.
    assert "Website TEST" not in set(kept["Property"])
    # ...and it reports WHAT it dropped and why, which the merge needs to tell an
    # out-of-scope row from a cancelled one.
    from processing import _canonical_key
    assert removed[_canonical_key("8249 E Chaparral")] == "Scottsdale", removed
    assert removed[_canonical_key("2407 Hyde A")] == "Boston", removed
    assert removed[_canonical_key("Website TEST")] == "unknown city", removed
    assert _canonical_key("1022 Mandeville") not in removed, removed
    print("OK: city filter keeps the 5 covered markets, drops the rest and blanks")


def test_dropped_property_is_out_of_scope_not_cancelled():
    """The live regression. Rows for a dropped market were written before the City
    lookup worked, so their City cell is blank AND property_to_city.csv has never
    heard of them -- both fallbacks silent. They were struck as CANCELLED, telling
    the team a job was called off when it was never this sheet's work: 157
    "cancellations" on a tab that had 37 real ones."""
    from processing import _canonical_key

    existing = pd.DataFrame([
        # Blank City, unknown to the CSV, and out of scope: the exact failing shape.
        _row(**{"Date": "2026-11-01", "Confirmation Code": "HMBOS00001",
                "Property": "12 Hinckley 1", "City": ""}),
        _row(**{"Date": "2026-11-01", "Confirmation Code": "HMSTOWE001",
                "Property": "56 Turner Mill 1", "City": ""}),
        # In scope, genuinely gone -> a real cancellation, must still be struck.
        _row(**{"Date": "2026-11-01", "Confirmation Code": "HMGONE0001",
                "Property": "6 Lake", "City": "Savannah"}),
    ], columns=LIVE_HEADER).astype(str)

    dropped = {_canonical_key("12 Hinckley 1"): "Boston",
               _canonical_key("56 Turner Mill 1"): "Stowe"}

    _, stats, changes = merge_reservations_into_sheet(
        pd.DataFrame([CANDIDATE]), existing,
        cancel_window=("2026-10-01", "2026-12-31"),
        allowed_cities=frozenset(["New Orleans", "Savannah", "Austin"]),
        out_of_scope_properties=dropped)

    assert stats["out_of_scope"] == 2, stats
    assert stats["cancelled"] == 1, stats
    assert [r["Confirmation Code"] for r in changes["cancelled"]] == ["HMGONE0001"]
    cities = {r["Property"]: r["City"] for r in changes["out_of_scope"]}
    assert cities == {"12 Hinckley 1": "Boston", "56 Turner Mill 1": "Stowe"}, cities
    # Out-of-scope rows carry no mark at all -- not struck, not highlighted.
    for prop in ("12 Hinckley 1", "56 Turner Mill 1"):
        at = changes["kept_positions"].index(
            [i for i, p in enumerate(existing["Property"]) if p == prop][0])
        assert changes["row_flags"][at] == "", (prop, changes["row_flags"])
    print("OK: a dropped market is reported out of scope, never struck as cancelled")


def test_out_of_scope_rows_are_not_struck_as_cancelled():
    """An existing row for a dropped market produces no candidate by definition.
    Striking it would call an out-of-scope property a CANCELLED booking, and enough
    of them would trip the short-fetch guard and suppress the real cancellations."""
    existing = pd.DataFrame([
        _row(**{"Date": "2026-11-01", "Confirmation Code": "HMLIVE0001",
                "Property": "1022 Mandeville", "City": "New Orleans"}),
        _row(**{"Date": "2026-11-01", "Confirmation Code": "HMGONE0001",
                "Property": "6 Lake", "City": "Savannah"}),
        _row(**{"Date": "2026-11-01", "Confirmation Code": "HMAZ00001",
                "Property": "8249 E Chaparral", "City": "Scottsdale"}),
    ], columns=LIVE_HEADER).astype(str)

    _, stats, changes = merge_reservations_into_sheet(
        pd.DataFrame([CANDIDATE]), existing,
        cancel_window=("2026-10-01", "2026-12-31"),
        allowed_cities=frozenset(["New Orleans", "Savannah", "Austin"]))

    # Only the Savannah row is a real cancellation.
    assert stats["cancelled"] == 1, stats
    assert [r["Confirmation Code"] for r in changes["cancelled"]] == ["HMGONE0001"]
    # The Scottsdale row is reported, not struck.
    assert stats["out_of_scope"] == 1, stats
    assert changes["out_of_scope"][0]["City"] == "Scottsdale", changes["out_of_scope"]
    # ...and it carries no mark at all: find where sheet row 2 landed in the output.
    at = changes["kept_positions"].index(2)
    assert changes["row_flags"][at] == "", changes["row_flags"]
    assert changes["row_flags"].count("cancelled") == 1, changes["row_flags"]
    print("OK: out-of-scope rows reported, never struck as cancellations")


def test_blank_city_is_unknown_not_out_of_scope():
    """A blank City means 'unknown', never 'foreign'. Reading it as out of scope
    excused real New Orleans rows from cancellation detection entirely -- 1405
    Carondelet and 1401 Delano turned up in the out-of-scope list on a live run."""
    existing = pd.DataFrame([
        # Blank City, but property_to_city.csv places it in New Orleans.
        _row(**{"Date": "2026-11-01", "Confirmation Code": "HMGONE0001",
                "Property": "1022 Mandeville", "City": ""}),
        # Blank City and unknown to the CSV -> treated as in scope, not silently skipped.
        _row(**{"Date": "2026-11-01", "Confirmation Code": "HMUNKNOWN1",
                "Property": "Somewhere Unmapped", "City": ""}),
        # Blank City, but the CSV knows it is out of scope.
        _row(**{"Date": "2026-11-01", "Confirmation Code": "HMAZ00001",
                "Property": "2407 Hyde A", "City": ""}),
    ], columns=LIVE_HEADER).astype(str)

    # 2407 Hyde A resolves to a city outside the allowlist via the seeded CSV row.
    _, stats, changes = merge_reservations_into_sheet(
        pd.DataFrame([dict(CANDIDATE, Property="6 Lake", City="Savannah")]), existing,
        cancel_window=("2026-10-01", "2026-12-31"),
        allowed_cities=frozenset(["New Orleans", "Savannah", "Austin"]))

    struck = {r["Confirmation Code"] for r in changes["cancelled"]}
    # The New Orleans row is judged normally -> struck, not excused.
    assert "HMGONE0001" in struck, changes["cancelled"]
    # The unplaceable row is also judged normally rather than silently skipped.
    assert "HMUNKNOWN1" in struck, changes["cancelled"]
    assert stats["out_of_scope"] == 0 or all(
        r["Confirmation Code"] != "HMGONE0001" for r in changes["out_of_scope"])
    print("OK: blank City resolves before scope is judged; unknown != out of scope")


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
    test_billing_listings_never_become_properties()
    test_listing_id_is_carried_through_the_adapter()
    test_turnover_row_keeps_both_confirmation_codes()
    test_operator_columns_survive_a_rewrite()
    test_operator_column_carries_from_a_duplicate_row()
    test_city_filter_keeps_only_covered_markets()
    test_out_of_scope_rows_are_not_struck_as_cancelled()
    test_dropped_property_is_out_of_scope_not_cancelled()
    test_blank_city_is_unknown_not_out_of_scope()
    test_upstream_city_survives_the_merge()
    test_duplicate_rows_do_not_lose_a_tick()
    test_validated_checkbox_columns_beat_the_header_guess()
    test_shifted_layout_raises_a_typed_error()
    test_canonical_property_spelling_is_not_a_cancellation()
    print("\nALL TESTS PASSED")
