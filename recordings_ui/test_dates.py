"""python test_dates.py -- DD-MM vs MM-DD detection in headers.parse_dates."""

import pandas as pd

from core.headers import parse_dates


def days(values):
    return list(parse_dates(pd.Series(values, dtype=object)).dt.strftime("%Y-%m-%d"))


# New exports: DD-MM-YYYY, including all-ambiguous rows.
assert days(["03-09-2026", "15-09-2026"]) == ["2026-09-03", "2026-09-15"]
assert days(["03-09-2026", "04-09-2026", "09-09-2026"]) == [
    "2026-09-03", "2026-09-04", "2026-09-09"]
# Old exports: MM-DD-YYYY.
assert days(["08-05-2026", "08-27-2026"]) == ["2026-08-05", "2026-08-27"]
assert days(["08-05-2026", "08-06-2026"]) == ["2026-08-05", "2026-08-06"]
# ISO text is unaffected.
assert days(["2026-09-03", "2026-09-15"]) == ["2026-09-03", "2026-09-15"]
# Real datetimes pass straight through.
real = pd.Series(pd.to_datetime(["2026-09-03"]))
assert parse_dates(real) is real

print("ok")
