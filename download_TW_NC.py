import os
import re
import random
import shutil
import pandas as pd
from datetime import datetime, time

INPUT_FILE = r"kotaj July NC.xlsx"
SAMPLE_AUDIO_FOLDER = r"Call_Rec_Sample_Adp_file"
OUTPUT_FOLDER = r"Final_Audio_Output"

def clean(value):
    if pd.isna(value):
        return ""
    return re.sub(r'[\\/:*?"<>|]', "", str(value).strip())

def parse_datetime(value):
    dt = pd.to_datetime(value, errors="coerce")
    if pd.isna(dt):
        raise ValueError(f"Invalid date/time: {value}")
    return dt

def get_call_time_hhmmss(raw_call_time, call_connect_time):
    if pd.isna(raw_call_time):
        return call_connect_time.strftime("%H%M%S")

    if isinstance(raw_call_time, time):
        return raw_call_time.strftime("%H%M%S")

    parsed = pd.to_datetime(raw_call_time, errors="coerce")

    if not pd.isna(parsed):
        return parsed.strftime("%H%M%S")

    raw_text = str(raw_call_time).strip()

    for fmt in ["%H:%M:%S", "%H:%M", "%I:%M:%S %p", "%I:%M %p"]:
        try:
            return datetime.strptime(raw_text, fmt).strftime("%H%M%S")
        except:
            pass

    return call_connect_time.strftime("%H%M%S")

def get_random_audio_file(sample_folder):
    audio_extensions = (".mp3", ".wav", ".m4a", ".aac", ".ogg", ".flac")

    audio_files = [
        os.path.join(sample_folder, file)
        for file in os.listdir(sample_folder)
        if file.lower().endswith(audio_extensions)
    ]

    if not audio_files:
        raise FileNotFoundError("No audio files found in Call_Rec_Sample_Adp_file folder")

    return random.choice(audio_files)

def main():
    base_dir = os.path.dirname(os.path.abspath(__file__))

    input_path = os.path.join(base_dir, INPUT_FILE)
    sample_folder_path = os.path.join(base_dir, SAMPLE_AUDIO_FOLDER)
    output_base_path = os.path.join(base_dir, OUTPUT_FOLDER)

    df = pd.read_excel(input_path)

    required_columns = [
        "Call_connect_time",
        "Call_time",
        "customer_id",
        "phone_number",
        "did_no"
    ]

    missing_columns = [col for col in required_columns if col not in df.columns]

    if missing_columns:
        print("Missing columns:", missing_columns)
        input("Press Enter to close...")
        return

    success_count = 0
    failed_rows = []

    for index, row in df.iterrows():
        try:
            call_connect_time = parse_datetime(row["Call_connect_time"])

            call_time_hhmmss = get_call_time_hhmmss(
                row["Call_time"],
                call_connect_time
            )

            customer_id = clean(row["customer_id"])
            phone_number = clean(row["phone_number"])
            did_no = clean(row["did_no"])

            year = call_connect_time.strftime("%Y")
            month = call_connect_time.strftime("%m")
            day = call_connect_time.strftime("%d")

            day_path = os.path.join(output_base_path, year, month, day)
            os.makedirs(day_path, exist_ok=True)

            file_name = (
                "RARDWHEELS_"
                + customer_id + "_"
                + call_connect_time.strftime("%Y%m%d")
                + call_time_hhmmss + "_"
                + phone_number + "_"
                + "KMBL02120_"
                + did_no
                + ".mp3"
            )

            final_path = os.path.join(day_path, file_name)

            random_audio = get_random_audio_file(sample_folder_path)
            shutil.copy2(random_audio, final_path)

            success_count += 1
            print(f"Created: {final_path}")

        except Exception as e:
            failed_rows.append({
                "excel_row": index + 2,
                "error": str(e)
            })
            print(f"Failed row {index + 2}: {e}")

    if failed_rows:
        pd.DataFrame(failed_rows).to_csv(
            os.path.join(base_dir, "failed_rows.csv"),
            index=False
        )

    print("\nDone")
    print("Files created:", success_count)
    print("Failed rows:", len(failed_rows))

    if failed_rows:
        print("Failed details saved in failed_rows.csv")

    input("Press Enter to close...")

if __name__ == "__main__":
    main()