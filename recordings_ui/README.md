# Kotak Recording Pipeline

Local web UI replacing `download_recordings.py`, `download_TW_NC.py` and
`download_recordings_CC_NC.py`.

## Run

```
run.bat
```

or

```
pip install -r requirements.txt
python app.py
```

The browser opens at <http://127.0.0.1:5000>. Everything runs on your machine;
nothing is uploaded anywhere.

## The flow

Two independent tabs, **TW** and **CC**. Each does the same three steps, and they
never share an output folder.

**1. Workbook** — drop the `.xlsx` in, pick the sheet. A pre-flight report shows
the row count, how many rows already carry an S3 link, how many need one, and
whether any rows will collide on filename.

**2. Fill missing links** — completes the `Recording_Path` column:

| Row | What happens |
|---|---|
| already an `http` link | left alone |
| names a tone the folder has | rewritten to the local path — **the recorded outcome is kept** |
| blank, or names a tone the folder lacks | assigned a tone at random |

Only the last case is random. A sheet that already carries Ringing / Busy /
Switch per row keeps that assignment, because which tone a row got says what
actually happened on the call. Writes a **new** workbook to `uploads/`; the
original is never modified, and an `NC_Link_Source` column records the tone.
Skipped automatically when every row already resolves.

**3. Download recordings** — each row becomes a file: `http` links are fetched
from S3, local tones are copied from the NC folder. Both land in

```
<output folder>/<YYYY>/<MM>/<DD>/RARDCCSTC_<customer_id>_<YYYYMMDD><HHMMSS>_<phone>_KMBL02120_<did_no>.mp3
```

Date comes from `Call_connect_time`, time from `Call_time`, exactly as the
original scripts did. Runs 10 downloads in parallel with retries, live progress
and a cancel button. Failed rows are downloadable as a CSV.

## Settings

- **Output folders** — separate paths for TW and CC.
- **NC tone folder** — folder holding the outcome recordings (`Ringing.mp3`,
  `Busy.mp3`, …). Defaults to `test/`. Sheet references are matched by
  **filename, case-insensitively**, so the `Switch.mp3` a sheet names under a
  `D:\TestRec\test\` path finds `switch.mp3` here. The panel lists what it found.
- **Workers / retries / timeout** — download tuning.
- **Collisions** — keep both files with `_1`, `_2` suffixes (default), or
  reproduce the original overwrite behaviour.

Settings persist in `config.json`.

## What changed from the original scripts

| | Before | Now |
|---|---|---|
| NC rows | copied a random sample, losing which tone the row named | resolved by filename, so the recorded outcome survives |
| Tone source | hard-coded `Call_Rec_Sample_Adp_file/` | configurable folder, contents shown in Settings |
| TW vs CC | same `Downloaded_Recordings` folder | separate folders, enforced |
| Mixed sheets | needed manual splitting | one pass handles URL and blank rows |
| Failed download | left a truncated `.mp3` that later runs skipped | downloads to `.part`, renames only on success |
| Two rows, same filename | silently overwrote | `_1`, `_2` suffix (configurable) |
| Bad `Call_connect_time` | discovered mid-run | counted in the pre-flight report |
| `last_error` | could raise `NameError` | always initialised |
| Backslash in an ID | not stripped, could spawn a subfolder | stripped with the other illegal characters |
| Time-only `Call_connect_time` | parsed as *today*, filing old calls under today's date | rejected outright |
| Pre-flight on 18k rows | ~9 s | ~0.7 s (vectorised) |

## Notes

- A sheet like `kotaj July NC.xlsx`, where every row already names a tone, needs
  no step 2 at all — it goes straight to step 3 and copies 11,000 files.
- A sheet whose `Recording_Path` column is entirely empty arrives from pandas as
  `float64`; the fill casts it to text first, so it no longer raises
  `Invalid value ... for dtype 'float64'`.
- Resume is automatic: a complete file already on disk is skipped, so re-running
  after a cancel or a crash picks up where it left off.
- The raw `CC_TW_June_ADO.xlsx` export is **not** usable directly. Its
  `Call_connect_time` holds a bare time with the date in a separate column, so
  every row fails to parse. Merge date and time into `Call_connect_time` first —
  the pre-flight report will tell you when a sheet has this problem.
