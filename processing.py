import re
import pandas as pd
from datetime import datetime

EXCLUDE_PROPERTIES = {
    # Austin, TX
    "1117 Brookswood A", "1117 Brookswood B",
    "1126 Brookswood A", "1126 Brookswood B",
    "1229 Delano A",     "1229 Delano B",
    "1231 Delano A",     "1231 Delano B",
    "4807 Prock A",      "4807 Prock B",      "4807 Prock C",
    "6108 Club terrace",
    "707 Valdez",
    # New Orleans, LA / Bay St. Louis, MS
    "307 Main",
    "315 Main 1A", "315 Main 1B", "315 Main 1C", "315 Main 1E",
    "315 Main 2F",  "315 Main 2H", "315 Main 2I", "315 Main 2J",
    "1401 Delachaise A", "1401 Delachaise B", "1401 Delachaise C", "1401 Delachaise D",
    "4417 Dryades",
    "468 St Joseph",
    "1560 Magazine",
    "1562 Magazine 1A", "1562 Magazine 1B", "1562 Magazine 2A", "1562 Magazine 2B",
    # Savannah, GA
    "105 Duffy 1", "105 Duffy 2",
    "107 W Park",
    "1312 Abercorn",
    "220 W Park",
    "302 W Park",
    "311 W York 1", "311 W York 2",
    "315 Duffy",
    "319 Congress",
    "320 W Bolton",
    "417 E Bay",
    "440 Habersham",
    "505 E Henry",
    "513 E Jones",
    "520 E Harris Ch",
    "521 Bolton",
    "522 Harris",
    "524 E Jones",
    "710 Barnard",
    # Test/demo listings. Guesty gives these a real in-scope city, so the city
    # filter cannot catch them and they arrive looking like genuine bookings --
    # "Website TEST" reached the Septiembre rebuild as a cleaning job.
    "Website TEST",
    "TEST-1209 MagazineSB",
}

# The source listing names spell the same *unit* many different ways — inconsistent
# directionals ("315 W Duffy" vs "315 Duffy"), "&"-glued names ("520 E Harris&CH"),
# and casing. We compare on a canonical key that strips those cosmetic differences
# so any spelling of a listed unit is excluded. We deliberately KEEP the "CH"
# (carriage-house) token and unit numbers/letters significant, so only the specific
# units listed above are excluded — a plain "520 E Harris" stays when only the
# "520 E Harris Ch" unit is listed, and whole-building combos (e.g. "311 W York 1&2",
# "105 E Duffy 1&2&CH") are kept unless listed in their own right.
_DIRECTIONALS = {"e", "w", "n", "s"}


def _canonical_key(prop: str) -> str:
    tokens = prop.lower().replace("&", " ").split()
    tokens = [t for t in tokens if t not in _DIRECTIONALS]
    return "".join(tokens)


_EXCLUDE_KEYS = {_canonical_key(p) for p in EXCLUDE_PROPERTIES}


def normalize_property(listing: str) -> list:
    raw = listing.split('/')[0].strip()

    # "Billing …" listings are accounting placeholders, not places anyone cleans.
    # The live catalogue holds ~1900 listings and a large share are these; the
    # prefix was previously kept, so "Billing 3223 Canal MU V1" would have become
    # a property named "Billing 3223 Canal" and appeared in the schedule as a job.
    # Stripping the prefix instead would be worse: the row would merge into the
    # REAL "3223 Canal" and invent a turnover from a billing record. Neither is a
    # property, so it maps to nothing at all.
    if re.match(r'^billing\b', raw, re.I):
        return []

    raw = re.sub(r'^F2X?\s+', '', raw)

    abbreviations = {
        'Grav':       'Gravier',
        'Gra':        'Gravier',
        'Baronn':     'Baronne',
        'Baro':       'Baronne',
        'Caron':      'Carondelet',
        'Barth':      'Bartholomew',
        'Brookswo':   'Brookswood',
        'Broo':       'Brookswood',
        'Webbervill': 'Webberville',
        'Webber':     'Webberville',
        'Montgom':    'Montgomery',
        'Mont':       'Montgomery',
        'Dela':       'Delano',
        'Con':        'Congress',
        'S Ramp':     'S Rampart',
        'OCH':        'Oretha Castle Haley',
        'Tchoupit':   'Tchoupitoulas',
        'MLK':        'Martin Luther King',
    }
    for abbr, full in abbreviations.items():
        raw = re.sub(rf'\b{abbr}\b', full, raw)

    # Remove all version/unit markers (V, V1, V2, VI, VII, U) wherever they appear
    raw = re.sub(r'\s*\b(V(?:I+|\d*)|U)\b', '', raw).strip()
    # Strip any trailing & left behind by combined-unit listing names
    raw = raw.rstrip('&').strip()

    # Join word-halves split by & into a space (e.g. "Harris&Ch" -> "Harris Ch"),
    # while leaving single-letter unit combos like "A&B" / "B&C" intact for the
    # unit-splitting rules below.
    raw = re.sub(
        r'([A-Za-z]+)&([A-Za-z]+)',
        lambda m: f"{m.group(1)} {m.group(2)}"
        if len(m.group(1)) > 1 or len(m.group(2)) > 1 else m.group(0),
        raw,
    )

    # "704-715 & N 2nd": hyphen-range street numbers + & + street name
    match = re.match(r'^(\d+)-(\d+)\s*&\s*(.+)$', raw)
    if match:
        num1, num2, street = match.group(1), match.group(2), match.group(3).strip()
        return [f"{num1} {street}", f"{num2} {street}"]

    # "1308&12 Baronne": two street numbers where second may be a short suffix
    match = re.match(r'^(\d+)&(\d+)\s+(.+)$', raw)
    if match:
        num1, num2, street = match.group(1), match.group(2), match.group(3)
        if len(num2) < len(num1):
            num2 = num1[:len(num1) - len(num2)] + num2
        return [f"{num1} {street}", f"{num2} {street}"]

    # "422 Gravier 201&202": digit unit list after street name
    match = re.match(r'^(.*?\d+\s+\w+)\s+([\d]+(?:&[\d]+)+)$', raw)
    if match:
        base    = match.group(1)
        units   = match.group(2).split('&')
        ref_len = len(units[0])
        return [f"{base} {u.zfill(ref_len)}" for u in units]

    # "1229 Dela A&B": letter unit list after address
    match = re.match(r'^(.+)\s+([A-Z](?:&[A-Z])+)$', raw)
    if match:
        base  = match.group(1)
        units = match.group(2).split('&')
        return [f"{base} {u}" for u in units]

    return [raw]


_DT_FORMATS = (
    "%Y-%m-%d %I:%M %p",   # 2026-06-19 11:00 AM
    "%m/%d/%Y %H:%M",      # 6/19/2026 11:00
)


def parse_dt(value: str) -> datetime:
    for fmt in _DT_FORMATS:
        try:
            return datetime.strptime(value, fmt)
        except ValueError:
            continue
    raise ValueError(f"Unrecognised datetime format: {value!r}")


_CHECKOUT_STD = datetime.strptime("11:00 AM", "%I:%M %p").time()
_CHECKIN_STD  = datetime.strptime("04:00 PM", "%I:%M %p").time()


def compute_adjustments(co_time: str, ci_time: str) -> str:
    codes = []
    if co_time:
        t = datetime.strptime(co_time, "%I:%M %p").time()
        if t < _CHECKOUT_STD:
            codes.append("ECO")
        elif t > _CHECKOUT_STD:
            codes.append("LCO")
    if ci_time:
        t = datetime.strptime(ci_time, "%I:%M %p").time()
        if t < _CHECKIN_STD:
            codes.append("ECI")
        elif t > _CHECKIN_STD:
            codes.append("LCI")
    return ", ".join(codes)


def process_reservations(
    df_checkout: pd.DataFrame,
    df_checkin: pd.DataFrame,
    city_seed: dict = None,
) -> pd.DataFrame:
    """
    Pure transformation — no filesystem access.

    Takes two DataFrames (checkout and checkin, with columns already stripped)
    and an optional city_seed dict {normalized_property: city}. Returns the
    processed output DataFrame sorted by Date / City / Property.

    City priority: city_seed (lowest) < source CSV LISTING'S CITY column (highest).
    To add a new property's city mapping, add a row to property_to_city.csv and
    redeploy, or include LISTING'S CITY in the exported CSVs.
    """
    city_lookup = dict(city_seed) if city_seed else {}
    # Canonical-key city index: lets a property's city be found even when the
    # reference name is spelled differently from the normalized output name
    # (e.g. "521 E Bolton&CH" in property_to_city.csv vs the emitted
    # "521 E Bolton CH"). Keyed via _canonical_key so directional/&/CH/spacing
    # differences don't break the lookup.
    city_by_key = {}
    if city_seed:
        for name, city in city_seed.items():
            if str(city).strip():
                city_by_key[_canonical_key(name)] = city

    checkout_lookup = {}
    checkin_lookup  = {}
    # Departing and arriving reservations are tracked SEPARATELY. A single
    # guest_lookup keyed on (prop, date) meant the check-in pass overwrote whatever
    # the check-out pass had written, so on a turnover date -- the one date where
    # both exist, and the only date a cleaner actually works -- the departing
    # reservation's confirmation code was silently lost.
    checkout_guest  = {}   # (prop, date) -> (conf code, guest) of the DEPARTING stay
    checkin_guest   = {}   # (prop, date) -> (conf code, guest) of the ARRIVING stay

    for row in df_checkout.to_dict(orient="records"):
        props   = normalize_property(row['LISTING'])
        co_dt   = parse_dt(f"{row['CHECK-OUT DATE']} {row['CHECK-OUT TIME']}")
        co_date = co_dt.strftime("%Y-%m-%d")
        city    = str(row.get("LISTING'S CITY", '')).strip()
        for prop in props:
            checkout_lookup[(prop, co_date)] = co_dt.strftime("%I:%M %p")
            checkout_guest[(prop, co_date)]  = (row.get('CONFIRMATION CODE', ''), row.get('GUEST', ''))
            if city and city.lower() != 'nan':
                city_lookup[prop] = city
                city_by_key[_canonical_key(prop)] = city

    for row in df_checkin.to_dict(orient="records"):
        props   = normalize_property(row['LISTING'])
        ci_dt   = parse_dt(f"{row['CHECK-IN DATE']} {row['CHECK-IN TIME']}")
        ci_date = ci_dt.strftime("%Y-%m-%d")
        city    = str(row.get("LISTING'S CITY", '')).strip()
        for prop in props:
            checkin_lookup[(prop, ci_date)] = ci_dt.strftime("%I:%M %p")
            checkin_guest[(prop, ci_date)]  = (row.get('CONFIRMATION CODE', ''), row.get('GUEST', ''))
            if city and city.lower() != 'nan':
                city_lookup[prop] = city
                city_by_key[_canonical_key(prop)] = city

    all_events = set(checkout_lookup.keys()) | set(checkin_lookup.keys())

    output_rows = []
    for (prop, date) in all_events:
        if _canonical_key(prop) in _EXCLUDE_KEYS:
            continue
        date_obj  = datetime.strptime(date, "%Y-%m-%d")
        co_time   = checkout_lookup.get((prop, date), "")
        ci_time   = checkin_lookup.get((prop, date), "")
        out_conf, out_guest = checkout_guest.get((prop, date), ('', ''))
        in_conf,  in_guest  = checkin_guest.get((prop, date), ('', ''))
        # `Confirmation Code` / `Guest` keep exactly their old meaning: the check-in
        # pass used to write last and therefore win, so prefer the arriving stay and
        # fall back to the departing one. Existing consumers see no change; the two
        # new columns are what make a turnover row unambiguous.
        conf_code = in_conf  or out_conf
        guest     = in_guest or out_guest
        output_rows.append({
            'City':              city_lookup.get(prop) or city_by_key.get(_canonical_key(prop), ""),
            'Day':               date_obj.strftime("%A"),
            'Date':              date,
            'Confirmation Code': conf_code,
            'Guest':             guest,
            'Property':          prop,
            'Check-out Time':    co_time,
            'Check-in Time':     ci_time,
            'T/O':               "yes" if (co_time and ci_time) else "",
            'Adjustments':       compute_adjustments(co_time, ci_time),
            'Out Code':          out_conf,
            'In Code':           in_conf,
        })

    # New columns are APPENDED so every existing column keeps its position -- the
    # Gradio export and the sheet both align by name, but nothing gains from
    # reshuffling a layout people read every morning.
    df_out = pd.DataFrame(output_rows, columns=[
        'City', 'Day', 'Date', 'Confirmation Code', 'Guest', 'Property',
        'Check-out Time', 'Check-in Time', 'T/O', 'Adjustments',
        'Out Code', 'In Code'
    ])
    return df_out.sort_values(by=['Date', 'City', 'Property']).reset_index(drop=True)
