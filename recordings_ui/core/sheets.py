"""Loading and pre-flight inspection of an uploaded workbook."""

import pandas as pd

from . import headers, pool
from .naming import (
    IDENTITY_COLUMNS,
    REQUIRED_COLUMNS,
    build_filenames,
)


EXTENSIONS = (".xlsx", ".xlsm", ".xls", ".csv")

# A CSV has no sheets; this stands in so the sheet picker and the filled
# workbook's tab name still have something to show.
CSV_SHEET = "Sheet1"


def is_csv(path):
    return str(path).lower().endswith(".csv")


def read_csv(path):
    # Excel on Windows saves "CSV" as cp1252 unless UTF-8 is chosen explicitly.
    try:
        return pd.read_csv(path, encoding="utf-8-sig")
    except UnicodeDecodeError:
        return pd.read_csv(path, encoding="cp1252")


def sheet_names(path):
    if is_csv(path):
        read_csv(path)  # fail the upload now if the file is not a readable CSV
        return [CSV_SHEET]
    return pd.ExcelFile(path).sheet_names


def load(path, sheet):
    """Read a sheet and put its date/time columns where the rest of the code
    expects them. The mapping rides along in .attrs so inspect() can report it;
    pandas carries .attrs through the .copy() that fill and download do."""
    df = read_csv(path) if is_csv(path) else pd.read_excel(path, sheet_name=sheet)
    df, mapping = headers.normalize(df)
    df.attrs["header_mapping"] = mapping
    return df


def is_blank(value):
    return pd.isna(value) or str(value).strip() == ""


def is_url(value):
    if is_blank(value):
        return False
    return str(value).strip().lower().startswith(("http://", "https://"))


def resolve_kinds(paths, tone_index):
    """Vector form of pool.resolve()'s first return value."""
    text = paths.astype(object).where(paths.notna(), "").astype(str).str.strip()
    lower = text.str.lower()

    kinds = pd.Series(pool.UNRESOLVED, index=paths.index, dtype=object)
    kinds[lower.str.startswith(("http://", "https://"))] = pool.URL

    rest = kinds.index[kinds == pool.UNRESOLVED]
    if len(rest):
        base = (
            text.loc[rest]
            .str.replace("\\", "/", regex=False)
            .str.rsplit("/", n=1)
            .str[-1]
            .str.lower()
        )
        kinds.loc[rest[base.isin(tone_index.keys()).to_numpy()]] = pool.TONE
    return kinds


def inspect(df, tone_folder, product):
    """Pre-flight report for a sheet: what's there, what's missing, what collides."""
    missing = [c for c in REQUIRED_COLUMNS if c not in df.columns]
    report = {
        "rows": int(len(df)),
        "columns": list(df.columns),
        "missing_columns": missing,
        "header_mapping": df.attrs.get("header_mapping", {}),
        "ok": not missing,
    }
    if missing:
        return report

    tone_index = pool.index(tone_folder)
    report["tones_available"] = len(tone_index)

    kinds = resolve_kinds(df["Recording_Path"], tone_index)
    report["with_link"] = int((kinds == pool.URL).sum())
    report["with_tone"] = int((kinds == pool.TONE).sum())
    report["needs_fill"] = int((kinds == pool.UNRESOLVED).sum())
    report["ready"] = report["with_link"] + report["with_tone"]

    # Split the unresolved rows: truly empty vs naming a tone we do not have.
    unresolved = df.loc[kinds == pool.UNRESOLVED, "Recording_Path"]
    report["blank"] = sum(1 for v in unresolved if is_blank(v))
    report["unknown_tone"] = report["needs_fill"] - report["blank"]
    report["missing_tone_names"] = sorted(
        {pool.basename(v) for v in unresolved if not is_blank(v)}
    )[:8]

    # Duplicate detection, split into the two cases that need different fixes.
    id_cols = [c for c in IDENTITY_COLUMNS if c in df.columns]
    report["exact_duplicate_rows"] = int(df.duplicated().sum())
    report["identity_duplicate_rows"] = int(df.duplicated(subset=id_cols).sum())

    # Rows whose date/time cannot be parsed would fail at run time; surface the
    # count now rather than after a long run.
    names, _connect, valid = build_filenames(df, product)
    good = names[valid]
    report["unparseable_rows"] = int((~valid).sum())
    report["unique_filenames"] = int(good.nunique())
    report["colliding_files"] = int(len(good) - good.nunique())

    return report
