import os
import re
import pandas as pd
import requests
from datetime import datetime, time
from concurrent.futures import ThreadPoolExecutor, as_completed
from tqdm import tqdm

INPUT_FILE = r"KotakCC_ConnectedJune.xlsx"
OUTPUT_FOLDER = r"Downloaded_Recordings"

MAX_WORKERS = 10
RETRY_COUNT = 3
TIMEOUT_SECONDS = 60

ERROR_LOG_FILE = "failed_downloads.csv"


def clean(value):
    if pd.isna(value):
        return ""
    return re.sub(r'[\\/:*?"<>|]', "", str(value).strip())


def parse_call_connect_time(value):
    dt = pd.to_datetime(value, errors="coerce", dayfirst=False)

    if pd.isna(dt):
        raise ValueError(f"Invalid Call_connect_time: {value}")

    return dt


def parse_call_time_hhmmss(raw_call_time, call_connect_time):
    if pd.isna(raw_call_time):
        return call_connect_time.strftime("%H%M%S")

    try:
        if isinstance(raw_call_time, time):
            return raw_call_time.strftime("%H%M%S")

        parsed = pd.to_datetime(raw_call_time, errors="coerce")

        if not pd.isna(parsed):
            return parsed.strftime("%H%M%S")

        raw_text = str(raw_call_time).strip()

        for fmt in ["%H:%M:%S", "%H:%M", "%I:%M:%S %p", "%I:%M %p"]:
            try:
                parsed_time = datetime.strptime(raw_text, fmt)
                return parsed_time.strftime("%H%M%S")
            except:
                pass

        return call_connect_time.strftime("%H%M%S")

    except:
        return call_connect_time.strftime("%H%M%S")


def download_file(url, save_path):
    headers = {
        "User-Agent": "Mozilla/5.0"
    }

    for attempt in range(1, RETRY_COUNT + 1):
        try:
            response = requests.get(
                url,
                stream=True,
                timeout=TIMEOUT_SECONDS,
                headers=headers
            )
            response.raise_for_status()

            with open(save_path, "wb") as f:
                for chunk in response.iter_content(chunk_size=8192):
                    if chunk:
                        f.write(chunk)

            return True, ""

        except Exception as e:
            last_error = str(e)

    return False, last_error


def process_row(index, row):
    try:
        recording_url = row.get("Recording_Path")

        if pd.isna(recording_url) or str(recording_url).strip() == "":
            return {
                "status": "failed",
                "row": index + 2,
                "reason": "Recording_Path missing"
            }

        call_connect_time = parse_call_connect_time(row.get("Call_connect_time"))

        customer_id = clean(row.get("customer_id"))
        phone_number = clean(row.get("phone_number"))
        did_no = clean(row.get("did_no"))

        call_time_hhmmss = parse_call_time_hhmmss(
            row.get("Call_time"),
            call_connect_time
        )

        year = call_connect_time.strftime("%Y")
        month = call_connect_time.strftime("%m")
        day = call_connect_time.strftime("%d")

        day_path = os.path.join(OUTPUT_FOLDER, year, month, day)
        os.makedirs(day_path, exist_ok=True)

        file_name = (
            "RARDCCSTC_"
            + customer_id + "_"
            + call_connect_time.strftime("%Y%m%d")
            + call_time_hhmmss + "_"
            + phone_number + "_"
            + "KMBL02120_"
            + did_no
            + ".mp3"
        )

        save_path = os.path.join(day_path, file_name)

        if os.path.exists(save_path) and os.path.getsize(save_path) > 0:
            return {
                "status": "skipped",
                "row": index + 2,
                "file": save_path,
                "reason": "Already downloaded"
            }

        success, error = download_file(str(recording_url).strip(), save_path)

        if success:
            return {
                "status": "downloaded",
                "row": index + 2,
                "file": save_path,
                "reason": ""
            }

        return {
            "status": "failed",
            "row": index + 2,
            "file": save_path,
            "reason": error,
            "url": recording_url
        }

    except Exception as e:
        return {
            "status": "failed",
            "row": index + 2,
            "reason": str(e),
            "url": row.get("Recording_Path")
        }


def main():
    df = pd.read_excel(INPUT_FILE)

    required_columns = [
        "Recording_Path",
        "Call_connect_time",
        "Call_time",
        "customer_id",
        "phone_number",
        "did_no"
    ]

    missing_columns = [col for col in required_columns if col not in df.columns]

    if missing_columns:
        print("Missing columns:", missing_columns)
        return

    results = []

    with ThreadPoolExecutor(max_workers=MAX_WORKERS) as executor:
        futures = [
            executor.submit(process_row, index, row)
            for index, row in df.iterrows()
        ]

        for future in tqdm(as_completed(futures), total=len(futures), desc="Downloading"):
            result = future.result()
            results.append(result)

    downloaded = sum(1 for r in results if r["status"] == "downloaded")
    skipped = sum(1 for r in results if r["status"] == "skipped")
    failed = sum(1 for r in results if r["status"] == "failed")

    print("\nDone")
    print("Downloaded:", downloaded)
    print("Skipped:", skipped)
    print("Failed:", failed)

    failed_rows = [r for r in results if r["status"] == "failed"]

    if failed_rows:
        pd.DataFrame(failed_rows).to_csv(ERROR_LOG_FILE, index=False)
        print("Failed rows saved in:", ERROR_LOG_FILE)


if __name__ == "__main__":
    main()