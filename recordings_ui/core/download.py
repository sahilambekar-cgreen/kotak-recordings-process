"""Stage 2 -- turn every row into a file under <root>/<product>/YYYY/MM/DD/.

Two sources feed the same output tree:
  * an http link  -> fetched from S3, threaded, with retries and resume
  * a local tone  -> written from an in-memory copy of the pool file

The run is planned up front in one vectorised pass (filenames, day folders,
collision suffixes), so the worker threads do nothing but I/O. That also means
the collision set is built single-threaded and needs no lock.

Carries over the behaviour of the original download_recordings.py and fixes
three faults it had:
  * a partial file from a dropped connection is no longer left behind and then
    mistaken for a completed download on the next run;
  * `last_error` is always defined;
  * two rows that generate the same filename get _1, _2 suffixes rather than
    silently overwriting each other;
  * a permanently dead link is no longer retried, and says what is wrong.
"""

import os
from concurrent.futures import ThreadPoolExecutor

import requests

from . import pool
from .naming import build_filenames, suffixed
from .sheets import resolve_kinds

HEADERS = {"User-Agent": "Mozilla/5.0"}

# A tone is copied thousands of times; hold each one in memory rather than
# re-reading it from disk per row. Nine tones is well under a megabyte.
MAX_CACHED_TONE = 8 * 1024 * 1024

# Ozonetel's bucket grants anonymous GetObject but not ListBucket, so S3 answers
# a MISSING object with 403 AccessDenied rather than 404 -- a key that has never
# existed returns byte-identical XML to one that was purged. Either way the file
# is not coming, and retrying a permanent 4xx only triples the wait on a dead
# link. 408 and 429 are the two that genuinely mean "try again".
RETRY_ANYWAY = {408, 429}


def is_permanent(status):
    return 400 <= status < 500 and status not in RETRY_ANYWAY


def describe(status):
    """Say what a status means for a recording, not just what HTTP called it."""
    if status in (403, 404):
        return (
            f"{status}: the recording is not in the S3 bucket. It was either "
            "never uploaded or has since been purged -- re-request it from "
            "Ozonetel; retrying this link cannot help."
        )
    return f"HTTP {status} from S3"


def _discard(tmp_path):
    if os.path.exists(tmp_path):
        try:
            os.remove(tmp_path)
        except OSError:
            pass


def download_file(url, save_path, retries, timeout, cancel):
    """Fetch to a .part file, then rename. Returns (ok, error)."""
    last_error = "unknown error"
    tmp_path = save_path + ".part"

    for _ in range(max(1, retries)):
        if cancel.is_set():
            return False, "cancelled"
        try:
            with requests.get(
                url, stream=True, timeout=timeout, headers=HEADERS
            ) as response:
                response.raise_for_status()
                with open(tmp_path, "wb") as f:
                    for chunk in response.iter_content(chunk_size=65536):
                        if cancel.is_set():
                            raise RuntimeError("cancelled")
                        if chunk:
                            f.write(chunk)

            if os.path.getsize(tmp_path) == 0:
                raise ValueError("empty response body")

            os.replace(tmp_path, save_path)
            return True, ""

        except requests.HTTPError as e:
            status = e.response.status_code if e.response is not None else 0
            last_error = describe(status)
            _discard(tmp_path)
            if is_permanent(status):
                break

        except Exception as e:
            last_error = str(e)
            _discard(tmp_path)

    return False, last_error


def write_tone(data, source, save_path):
    """Write a cached tone into place, via .part so a crash leaves nothing."""
    tmp_path = save_path + ".part"
    try:
        if data is None:
            with open(source, "rb") as f:
                data = f.read()
        with open(tmp_path, "wb") as f:
            f.write(data)
        os.replace(tmp_path, save_path)
        return True, ""
    except Exception as e:
        if os.path.exists(tmp_path):
            try:
                os.remove(tmp_path)
            except OSError:
                pass
        return False, str(e)


def _plan(df, output_root, tone_index, on_collision, product):
    """Work out, for every row, what to write and where. Returns (tasks, failures).

    A task is (excel_row, kind, source, save_path).
    """
    names, connect, valid = build_filenames(df, product)
    kinds = resolve_kinds(df["Recording_Path"], tone_index)

    years = connect.dt.strftime("%Y")
    months = connect.dt.strftime("%m")
    days = connect.dt.strftime("%d")

    raw_paths = df["Recording_Path"].astype(object).where(
        df["Recording_Path"].notna(), ""
    ).astype(str).tolist()

    names = names.tolist()
    kinds = kinds.tolist()
    valid = valid.tolist()
    years, months, days = years.tolist(), months.tolist(), days.tolist()

    tasks, failures = [], []
    taken = set()

    for pos in range(len(names)):
        excel_row = pos + 2  # +1 for 0-index, +1 for the header row
        raw = raw_paths[pos]
        kind = kinds[pos]

        if kind == pool.UNRESOLVED:
            reason = (
                "Recording_Path is blank -- run step 2 first"
                if raw.strip() == ""
                else f"Recording_Path names {pool.basename(raw)}, which the "
                     "tone folder does not have -- run step 2 first"
            )
            failures.append((excel_row, reason, raw))
            continue

        if not valid[pos]:
            failures.append(
                (excel_row, f"Invalid date/time: {df['Call_connect_time'].iloc[pos]!r}", raw)
            )
            continue

        folder = os.path.join(output_root, years[pos], months[pos], days[pos])
        save_path = os.path.join(folder, names[pos])
        if on_collision == "suffix":
            save_path = suffixed(save_path, taken)

        source = raw if kind == pool.URL else tone_index[pool.basename(raw).lower()]
        tasks.append((excel_row, kind, source, save_path))

    return tasks, failures


def run(job, df, output_root, tone_folder, workers, retries, timeout,
        on_collision="suffix"):
    """Process every row of `df`. Blocks until finished or cancelled."""
    os.makedirs(output_root, exist_ok=True)
    tone_index = pool.index(tone_folder)

    tasks, failures = _plan(df, output_root, tone_index, on_collision, job.product)

    for excel_row, reason, raw in failures:
        job.tick(
            "failed",
            line=f"row {excel_row}: {reason}",
            failure={"excel_row": excel_row, "reason": reason, "url": raw},
        )

    # Create each day folder once, not once per row.
    for folder in {os.path.dirname(t[3]) for t in tasks}:
        os.makedirs(folder, exist_ok=True)

    # Hold each tone in memory; thousands of rows share nine files.
    tone_bytes = {}
    for path in {t[2] for t in tasks if t[1] == pool.TONE}:
        try:
            if os.path.getsize(path) <= MAX_CACHED_TONE:
                with open(path, "rb") as f:
                    tone_bytes[path] = f.read()
        except OSError:
            pass

    def process(task):
        excel_row, kind, source, save_path = task
        if job.cancel.is_set():
            return

        # Resume: a complete file from an earlier run is left alone.
        try:
            if os.path.getsize(save_path) > 0:
                job.tick("skipped", line=f"row {excel_row}: already present")
                return
        except OSError:
            pass

        name = os.path.basename(save_path)
        if kind == pool.URL:
            ok, error = download_file(source, save_path, retries, timeout, job.cancel)
            status, line = "downloaded", f"row {excel_row}: {name}"
        else:
            ok, error = write_tone(tone_bytes.get(source), source, save_path)
            status, line = "copied", f"row {excel_row}: {pool.label(source)} -> {name}"

        if ok:
            job.tick(status, line=line)
        else:
            job.tick(
                "failed",
                line=f"row {excel_row}: {error}",
                failure={"excel_row": excel_row, "reason": error, "url": source},
            )

    with ThreadPoolExecutor(max_workers=workers) as executor:
        executor.map(process, tasks)

    job.output_path = output_root
    job.state = "cancelled" if job.cancel.is_set() else "finished"
