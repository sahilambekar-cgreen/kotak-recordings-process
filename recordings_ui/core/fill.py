"""Stage 1 -- give every row a usable Recording_Path.

Replaces download_TW_NC.py / download_recordings_CC_NC.py. Those copied a random
sample file straight to the output. This instead completes the sheet, so the
workbook itself records which tone each not-connected call was given:

  * a row already holding an http link          -> left alone
  * a row naming a tone the pool has            -> rewritten to the local path,
                                                   keeping the recorded outcome
  * a blank row, or one naming a tone we lack   -> assigned a random tone

Only the third case is random. A sheet that already carries Ringing / Busy /
Switch per row keeps that assignment.
"""

import os
import random
from datetime import datetime

import pandas as pd

from . import pool
from .sheets import is_blank, resolve_kinds


def fill_links(job, df, tone_folder, source_path, sheet, out_dir):
    """Complete the Recording_Path column; write a new workbook. Returns its path."""
    tones = pool.list_tones(tone_folder)
    tone_index = pool.index(tone_folder)
    if not tones:
        raise ValueError(
            "The NC tone folder has no audio files. Point Settings at the "
            "folder holding Ringing.mp3, Busy.mp3 and the rest."
        )

    df = df.copy()

    # A sheet whose Recording_Path is entirely empty comes back from pandas as
    # float64, which refuses a string. Force both columns to object first.
    df["Recording_Path"] = df["Recording_Path"].astype(object)
    if "NC_Link_Source" not in df.columns:
        df["NC_Link_Source"] = ""
    df["NC_Link_Source"] = df["NC_Link_Source"].astype(object)

    kinds = resolve_kinds(df["Recording_Path"], tone_index)
    original = df["Recording_Path"].copy()

    new_paths = {}
    new_tags = {}

    for i in df.index[kinds != pool.URL]:
        if job.cancel.is_set():
            job.state = "cancelled"
            return None

        value = original.loc[i]
        excel_row = df.index.get_loc(i) + 2  # +1 for 0-index, +1 for the header

        if kinds.loc[i] == pool.TONE:
            # Same outcome, path that resolves on this machine.
            target = tone_index[pool.basename(value).lower()]
            why = "kept"
        else:
            target = random.choice(tones)
            why = "blank" if is_blank(value) else f"unknown tone {pool.basename(value)}"

        new_paths[i] = target
        new_tags[i] = pool.label(target)
        job.tick("filled", line=f"row {excel_row}: {pool.label(target)} ({why})")

    if new_paths:
        df.loc[list(new_paths), "Recording_Path"] = pd.Series(new_paths)
        df.loc[list(new_tags), "NC_Link_Source"] = pd.Series(new_tags)

    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    base = os.path.splitext(os.path.basename(source_path))[0]
    out_path = os.path.join(out_dir, f"{base}__filled_{stamp}.xlsx")

    with pd.ExcelWriter(out_path, engine="openpyxl") as writer:
        df.to_excel(writer, sheet_name=str(sheet)[:31], index=False)

    return out_path
