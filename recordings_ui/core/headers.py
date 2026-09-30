"""Header normalisation: make every Kotak export shape look like one shape.

Two shapes arrive in practice, and they disagree about which column holds the
date and which holds the time.

  Shape A -- the processed sheet the original scripts were written for:
      Call_connect_time   a DATE    2026-06-02
      Call_time           a TIME    18:11:27

  Shape B -- the raw ADO export, where the roles are split differently:
      Call_connect_time   a TIME    18:08:52
      <a date column>     a DATE    2026-06-02

In Shape B the date column has been spelled differently in every file seen so
far -- `date` (TW_June_ADO), `Call date` (CC_June_ADO), `Call_dates`
(Kotak Tw With Link Aug_2026) -- and sometimes there is no `Call_time` at all.
Matching on names alone would break again on the next export, so columns are
classified by what they CONTAIN; names only break ties.

`normalize` rewrites a Shape B frame into Shape A. Everything downstream
(naming, fill, download) only ever sees Shape A and needs no changes. It is
applied in sheets.load(), so all three routes get it.
"""

import pandas as pd

from .naming import is_time_only

DATE = "date"
TIME = "time"

CANONICAL_DATE_COLUMN = "Call_connect_time"
CANONICAL_TIME_COLUMN = "Call_time"

# When several columns qualify, prefer these names in this order.
#
# The time order matters. In the raw ADO export BOTH Call_connect_time
# (18:08:52) and Call_time (18:11:27) hold a time, and the processed sheet took
# its filename time from Call_time -- so Call_time must win to keep filenames
# byte-identical to the ones already delivered to the bank.
_TIME_PREFERENCE = ["Call_time", "Call_connect_time", "Call end time"]
_DATE_PREFERENCE = [
    "Call_connect_time",
    "Call_dates",
    "Call date",
    "Call_date",
    "date",
]

# Enough rows to tell a date column from a phone number, cheap on a 18k sheet.
_SAMPLE = 200


def classify(series):
    """Return DATE, TIME, or None for what a column actually holds."""
    # Numeric columns are excluded before anything else: pandas will happily
    # read an int64 phone number as epoch nanoseconds and call it a date.
    if pd.api.types.is_numeric_dtype(series):
        return None

    values = series.dropna().head(_SAMPLE)
    if values.empty:
        return None

    if all(is_time_only(v) for v in values):
        return TIME

    try:
        parsed = pd.to_datetime(values, errors="coerce", format="mixed")
    except (ValueError, TypeError):
        return None

    # Every sampled value must parse, so a text column with one date-like cell
    # is not mistaken for the date column.
    return DATE if parsed.notna().all() else None


def parse_dates(series):
    """Turn a text date column into datetimes, choosing DD-MM or MM-DD per file.

    Older exports wrote MM-DD-YYYY, newer ones DD-MM-YYYY, and 03-09-2026 is
    valid either way. A sheet covers one month, so the reading that parses the
    most rows into the fewest months wins; a tie keeps month-first, the old
    behaviour. Columns Excel already stored as dates are returned untouched.
    """
    if not (pd.api.types.is_object_dtype(series)
            or pd.api.types.is_string_dtype(series)):
        return series

    def read(dayfirst):
        parsed = pd.to_datetime(
            series, errors="coerce", format="mixed", dayfirst=dayfirst
        )
        return parsed, (parsed.notna().sum(), -parsed.dt.to_period("M").nunique())

    mdy, mdy_score = read(False)
    dmy, dmy_score = read(True)
    return dmy if dmy_score > mdy_score else mdy


def _pick(kinds, wanted, preference):
    candidates = [name for name, kind in kinds.items() if kind == wanted]
    for name in preference:
        if name in candidates:
            return name
    return candidates[0] if candidates else None


def normalize(df):
    """Return (frame, mapping) with the date/time columns in canonical places.

    `mapping` records what was moved -- canonical name -> column it came from --
    and is empty when the sheet was already Shape A. The frame is returned
    untouched in that case.
    """
    kinds = {name: classify(df[name]) for name in df.columns}

    date_column = _pick(kinds, DATE, _DATE_PREFERENCE)
    time_column = _pick(kinds, TIME, _TIME_PREFERENCE)

    # No date anywhere: leave it alone. The REQUIRED_COLUMNS check in
    # sheets.inspect() reports the problem in the user's own terms.
    if date_column is None:
        return df, {}

    # Read both source columns BEFORE writing: in the Aug export the time is
    # read out of Call_connect_time, which the date then overwrites.
    date_values = parse_dates(df[date_column])
    time_values = df[time_column] if time_column else None

    renamed = date_column != CANONICAL_DATE_COLUMN
    needs_date = renamed or date_values is not df[date_column]
    needs_time = time_column is not None and time_column != CANONICAL_TIME_COLUMN
    if not needs_date and not needs_time:
        return df, {}

    df = df.copy()
    mapping = {}

    if needs_date:
        df[CANONICAL_DATE_COLUMN] = date_values
        if renamed:
            mapping[CANONICAL_DATE_COLUMN] = date_column

    if needs_time:
        df[CANONICAL_TIME_COLUMN] = time_values
        mapping[CANONICAL_TIME_COLUMN] = time_column

    return df, mapping
