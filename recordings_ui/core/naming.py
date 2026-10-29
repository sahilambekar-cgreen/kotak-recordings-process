"""Filename and path rules, ported from the original scripts.

The bank consumes the filename, so these rules are the contract. The pattern
depends on the product -- see PATTERN_BY_PRODUCT -- and the file is filed
under <root>/<YYYY>/<MM>/<DD>/.

`build_filename` handles one row; `build_filenames` does a whole sheet with
pandas vector ops and is ~40x faster. They must agree -- the test suite checks
them against each other.
"""

import os
import re
from datetime import datetime, time

import pandas as pd

# Columns every sheet must have before a run can start.
REQUIRED_COLUMNS = [
    "Recording_Path",
    "Call_connect_time",
    "Call_time",
    "customer_id",
    "phone_number",
    "did_no",
]

# The parts of a row that make its filename unique. Two rows agreeing on all
# of these collide on disk.
IDENTITY_COLUMNS = [
    "customer_id",
    "phone_number",
    "did_no",
    "Call_connect_time",
    "Call_time",
]

# The bank takes a different filename per product: TW is the two-wheeler book,
# CC the credit-card one, PL personal loans. {stamp} is YYYYMMDDHHMMSS.
# Getting this wrong is invisible until the bank rejects the batch, so
# `pattern_for` refuses an unknown product rather than guessing a default.
PATTERN_BY_PRODUCT = {
    "TW": "RARDWHEELS_{customer_id}_{stamp}_{phone}_KMBL02120_{did}.mp3",
    "CC": "RARDCCSTC_{customer_id}_{stamp}_{phone}_KMBL02120_{did}.mp3",
    "PL": "{customer_id}_{phone}_{stamp}.mp3",
}


def pattern_for(product):
    try:
        return PATTERN_BY_PRODUCT[product]
    except KeyError:
        raise ValueError(
            f"Unknown product {product!r} -- expected one of "
            + ", ".join(sorted(PATTERN_BY_PRODUCT))
        ) from None

# Characters Windows rejects in a filename. The backslash is first and must
# stay escaped -- without it a stray path separator in customer_id would spawn
# an unintended subdirectory.
_INVALID = r'\\/:*?"<>|'
_INVALID_FILENAME_CHARS = re.compile("[" + _INVALID + "]")

_TIME_FORMATS = ["%H:%M:%S", "%H:%M", "%I:%M:%S %p", "%I:%M %p"]

# A Call_connect_time carrying only a time of day is NOT a date. pandas would
# happily parse "18:08:52" as today, which would file June calls under today's
# folder -- so these are rejected outright. This is the shape of the raw ADO
# export, where the date lives in a separate column, and it survives a
# round-trip through Excel as a plain string, hence the text form too.
_TIME_ONLY = re.compile(
    r"^\s*\d{1,2}:\d{2}(:\d{2}(\.\d+)?)?\s*([AaPp]\.?[Mm]\.?)?\s*$"
)


def is_time_only(value):
    """True when a value gives a time of day but no date."""
    if isinstance(value, time):
        return True
    if isinstance(value, str):
        return bool(_TIME_ONLY.match(value))
    return False


# --------------------------------------------------------------- one row ---

def clean(value):
    """Strip characters Windows rejects in a filename. Blank cells become ''."""
    if pd.isna(value):
        return ""
    return _INVALID_FILENAME_CHARS.sub("", str(value).strip())


def parse_datetime(value):
    """Parse Call_connect_time. Raises ValueError so the caller can log the row.

    Note a bare datetime.time (as in the raw ADO export, where the date lives
    in a separate column) parses to NaT and is rejected here.
    """
    if is_time_only(value):
        raise ValueError(
            f"Call_connect_time {value!r} has a time but no date"
        )
    dt = pd.to_datetime(value, errors="coerce")
    if pd.isna(dt):
        raise ValueError(f"Invalid date/time: {value!r}")
    return dt


def call_time_hhmmss(raw_call_time, call_connect_time):
    """Coax any of Excel's time representations into HHMMSS.

    Falls back to the connect time rather than raising, matching the original
    behaviour: a weird time cell must never abort the row.
    """
    if pd.isna(raw_call_time):
        return call_connect_time.strftime("%H%M%S")

    if isinstance(raw_call_time, time):
        return raw_call_time.strftime("%H%M%S")

    parsed = pd.to_datetime(raw_call_time, errors="coerce")
    if not pd.isna(parsed):
        return parsed.strftime("%H%M%S")

    raw_text = str(raw_call_time).strip()
    for fmt in _TIME_FORMATS:
        try:
            return datetime.strptime(raw_text, fmt).strftime("%H%M%S")
        except ValueError:
            pass

    return call_connect_time.strftime("%H%M%S")


def build_filename(row, product):
    """Return (filename, call_connect_time) for a row."""
    connect = parse_datetime(row.get("Call_connect_time"))
    hhmmss = call_time_hhmmss(row.get("Call_time"), connect)

    name = pattern_for(product).format(
        customer_id=clean(row.get("customer_id")),
        stamp=connect.strftime("%Y%m%d") + hhmmss,
        phone=clean(row.get("phone_number")),
        did=clean(row.get("did_no")),
    )
    return name, connect


# ------------------------------------------------------------ whole sheet ---

def clean_series(s):
    """Vector form of clean()."""
    text = s.astype(object).where(s.notna(), "").astype(str).str.strip()
    return text.str.replace(_INVALID_FILENAME_CHARS, "", regex=True)


def _hhmmss_series(raw, connect):
    """Vector form of call_time_hhmmss(), same fallback order."""
    out = pd.Series([None] * len(raw), index=raw.index, dtype=object)

    # 1. real datetime.time cells
    is_time = raw.map(lambda v: isinstance(v, time))
    if is_time.any():
        out.loc[is_time] = raw[is_time].map(lambda t: t.strftime("%H%M%S"))

    # 2. anything pandas can parse (covers "18:11:27" strings and Timestamps)
    rest = raw.index[~is_time & raw.notna()]
    if len(rest):
        parsed = pd.to_datetime(raw.loc[rest], errors="coerce", format="mixed")
        good = parsed.index[parsed.notna()]
        out.loc[good] = parsed.loc[good].dt.strftime("%H%M%S")

    # 3. explicit formats, row by row -- only for whatever is still unresolved
    stuck = out.index[out.isna() & raw.notna()]
    for i in stuck:
        text = str(raw.loc[i]).strip()
        for fmt in _TIME_FORMATS:
            try:
                out.loc[i] = datetime.strptime(text, fmt).strftime("%H%M%S")
                break
            except ValueError:
                pass

    # 4. fall back to the connect time
    missing = out.isna() & connect.notna()
    if missing.any():
        out.loc[missing] = connect[missing].dt.strftime("%H%M%S")

    return out


def build_filenames(df, product):
    """Filenames for a whole sheet.

    Returns (names, connect, valid) aligned to df.index. `valid` is False where
    Call_connect_time could not be parsed; `names` is None on those rows.
    """
    raw_connect = df["Call_connect_time"]
    dateless = raw_connect.map(is_time_only)

    connect = pd.to_datetime(
        raw_connect.where(~dateless), errors="coerce", format="mixed"
    )
    valid = connect.notna()
    hhmmss = _hhmmss_series(df["Call_time"], connect).fillna("")

    pattern = pattern_for(product)
    stamps = connect.dt.strftime("%Y%m%d").fillna("") + hhmmss
    names = pd.Series(
        [
            pattern.format(customer_id=c, stamp=s, phone=p, did=d)
            for c, s, p, d in zip(
                clean_series(df["customer_id"]), stamps,
                clean_series(df["phone_number"]), clean_series(df["did_no"]),
            )
        ],
        index=df.index,
    )
    return names.where(valid, None), connect, valid


# ----------------------------------------------------------------- paths ---

def day_folder(output_root, connect):
    """<output_root>/<YYYY>/<MM>/<DD>"""
    return os.path.join(
        output_root,
        connect.strftime("%Y"),
        connect.strftime("%m"),
        connect.strftime("%d"),
    )


def suffixed(path, taken):
    """Return a path not already in `taken`, appending _1, _2, ... if needed.

    `taken` is a set the caller owns; this both reads and updates it, so
    collisions within a single run never silently overwrite.
    """
    if path not in taken:
        taken.add(path)
        return path

    stem, ext = os.path.splitext(path)
    n = 1
    while f"{stem}_{n}{ext}" in taken:
        n += 1
    candidate = f"{stem}_{n}{ext}"
    taken.add(candidate)
    return candidate
