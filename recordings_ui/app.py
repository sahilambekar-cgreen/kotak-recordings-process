"""Kotak recording pipeline -- local web UI.

Run:  python app.py    then open http://127.0.0.1:5000

Stage 1 (Fill)     : empty Recording_Path cells get an S3 link from the NC pool.
Stage 2 (Download) : every Recording_Path is fetched into a per-product tree.

TW and CC keep entirely separate uploads, sheets, jobs and output folders.
"""

import io
import json
import os
import threading
import webbrowser

import pandas as pd
from flask import Flask, jsonify, request, send_file, render_template

from core import fill, jobs, pool, sheets
from core.naming import PREFIX_BY_PRODUCT
from core.download import run as run_download

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
UPLOAD_DIR = os.path.join(BASE_DIR, "uploads")
CONFIG_PATH = os.path.join(BASE_DIR, "config.json")

os.makedirs(UPLOAD_DIR, exist_ok=True)

app = Flask(__name__)
app.config["MAX_CONTENT_LENGTH"] = 256 * 1024 * 1024   # 256 MB workbooks

DEFAULT_CONFIG = {
    "output_tw": os.path.join(BASE_DIR, "Downloaded_Recordings", "TW"),
    "output_cc": os.path.join(BASE_DIR, "Downloaded_Recordings", "CC"),
    "workers": 10,
    "retries": 3,
    "timeout": 60,
    "on_collision": "suffix",
    # Folder of not-connected outcome tones (Ringing.mp3, Busy.mp3, ...).
    # Sheet references are matched against this folder by filename, so a path
    # from the exporting machine still resolves here.
    "nc_folder": os.path.join(BASE_DIR, "test"),
}

# Uploaded workbooks for this server run: token -> saved path.
UPLOADS = {}


def load_config():
    if os.path.exists(CONFIG_PATH):
        try:
            with open(CONFIG_PATH, encoding="utf-8") as f:
                cfg = dict(DEFAULT_CONFIG)
                cfg.update(json.load(f))
                return cfg
        except Exception:
            pass
    return dict(DEFAULT_CONFIG)


def save_config(cfg):
    with open(CONFIG_PATH, "w", encoding="utf-8") as f:
        json.dump(cfg, f, indent=2)


def output_root(cfg, product):
    return cfg["output_tw"] if product == "TW" else cfg["output_cc"]


def product_of(body):
    """TW or CC. The upload token carries it, so /api/inspect need not send it."""
    return body.get("product") or (body.get("token") or "").split(":", 1)[0] or "TW"


@app.route("/")
def index():
    # The filename prefix differs per product and the panel is built client
    # side, so hand the real map to the page rather than repeating it in JS.
    return render_template("index.html", prefixes=PREFIX_BY_PRODUCT)


@app.route("/api/config", methods=["GET", "POST"])
def api_config():
    if request.method == "POST":
        cfg = load_config()
        cfg.update(request.get_json(force=True))
        save_config(cfg)
        return jsonify(cfg)
    return jsonify(load_config())


@app.route("/api/tones")
def api_tones():
    """What the NC tone folder currently holds, for the Settings panel."""
    folder = request.args.get("folder") or load_config()["nc_folder"]
    tones = pool.list_tones(folder)
    return jsonify({
        "folder": folder,
        "exists": os.path.isdir(folder),
        "tones": [
            {"label": pool.label(p), "file": os.path.basename(p),
             "size": os.path.getsize(p)}
            for p in tones
        ],
    })


@app.route("/api/upload", methods=["POST"])
def api_upload():
    product = request.form.get("product", "TW")
    file = request.files.get("file")
    if not file or not file.filename:
        return jsonify({"error": "No file received."}), 400
    if not file.filename.lower().endswith(sheets.EXTENSIONS):
        return jsonify(
            {"error": "Please upload an .xlsx, .xlsm, .xls or .csv file."}
        ), 400

    safe = os.path.basename(file.filename)
    saved = os.path.join(UPLOAD_DIR, f"{product}__{safe}")
    file.save(saved)

    try:
        names = sheets.sheet_names(saved)
    except Exception as e:
        return jsonify({"error": f"Could not read the workbook: {e}"}), 400

    token = f"{product}:{safe}"
    UPLOADS[token] = saved
    return jsonify({"token": token, "filename": safe, "sheets": names})


@app.route("/api/inspect", methods=["POST"])
def api_inspect():
    body = request.get_json(force=True)
    path = UPLOADS.get(body.get("token"))
    if not path:
        return jsonify({"error": "Upload the workbook again."}), 400
    try:
        df = sheets.load(path, body["sheet"])
    except Exception as e:
        return jsonify({"error": f"Could not read that sheet: {e}"}), 400
    return jsonify(
        sheets.inspect(df, load_config()["nc_folder"], product_of(body))
    )


@app.route("/api/fill", methods=["POST"])
def api_fill():
    body = request.get_json(force=True)
    cfg = load_config()
    path = UPLOADS.get(body.get("token"))
    if not path:
        return jsonify({"error": "Upload the workbook again."}), 400

    product = body.get("product", "TW")
    sheet = body["sheet"]
    df = sheets.load(path, sheet)

    tone_index = pool.index(cfg["nc_folder"])
    todo = int(
        df["Recording_Path"]
        .map(lambda v: pool.resolve(v, tone_index)[0] != pool.URL)
        .sum()
    )
    if todo == 0:
        return jsonify({"error": "Every row already has an http link."}), 400

    job = jobs.create("fill", product, todo)

    def worker():
        try:
            out = fill.fill_links(
                job, df, cfg["nc_folder"], path, sheet, UPLOAD_DIR
            )
            if out:
                job.output_path = out
                token = f"{product}:{os.path.basename(out)}"
                UPLOADS[token] = out
                job.message = token
                job.state = "finished"
        except Exception as e:
            job.state = "error"
            job.message = str(e)

    threading.Thread(target=worker, daemon=True).start()
    return jsonify({"job": job.id})


@app.route("/api/download", methods=["POST"])
def api_download():
    body = request.get_json(force=True)
    cfg = load_config()
    path = UPLOADS.get(body.get("token"))
    if not path:
        return jsonify({"error": "Upload the workbook again."}), 400

    product = body.get("product", "TW")
    df = sheets.load(path, body["sheet"])
    job = jobs.create("download", product, len(df))

    def worker():
        try:
            run_download(
                job,
                df,
                output_root(cfg, product),
                cfg["nc_folder"],
                int(cfg["workers"]),
                int(cfg["retries"]),
                int(cfg["timeout"]),
                cfg.get("on_collision", "suffix"),
            )
        except Exception as e:
            job.state = "error"
            job.message = str(e)

    threading.Thread(target=worker, daemon=True).start()
    return jsonify({"job": job.id})


@app.route("/api/job/<job_id>")
def api_job(job_id):
    job = jobs.get(job_id)
    if not job:
        return jsonify({"error": "Unknown job."}), 404
    return jsonify(job.snapshot())


@app.route("/api/job/<job_id>/cancel", methods=["POST"])
def api_cancel(job_id):
    job = jobs.get(job_id)
    if not job:
        return jsonify({"error": "Unknown job."}), 404
    job.cancel.set()
    return jsonify({"ok": True})


@app.route("/api/job/<job_id>/failures.csv")
def api_failures(job_id):
    job = jobs.get(job_id)
    if not job or not job.failures:
        return jsonify({"error": "No failures recorded."}), 404
    buf = io.StringIO()
    pd.DataFrame(job.failures).to_csv(buf, index=False)
    data = io.BytesIO(buf.getvalue().encode("utf-8"))
    return send_file(
        data,
        mimetype="text/csv",
        as_attachment=True,
        download_name=f"failed_{job.product}_{job.id}.csv",
    )


@app.route("/api/job/<job_id>/workbook")
def api_workbook(job_id):
    job = jobs.get(job_id)
    if not job or not job.output_path or not os.path.isfile(job.output_path):
        return jsonify({"error": "No filled workbook for this job."}), 404
    return send_file(job.output_path, as_attachment=True)


if __name__ == "__main__":
    url = "http://127.0.0.1:5000"
    print(f"\n  Kotak recording pipeline  ->  {url}\n")
    threading.Timer(1.2, lambda: webbrowser.open(url)).start()
    app.run(host="127.0.0.1", port=5000, debug=False, threaded=True)
