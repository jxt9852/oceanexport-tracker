import calendar
import hmac
import logging
import mimetypes
import os
import re
import secrets
from datetime import date, datetime
from uuid import uuid4

import boto3
import pymysql
from botocore.exceptions import BotoCoreError, ClientError
from bs4 import BeautifulSoup
import requests
from flask import (Flask, Response, abort, flash, jsonify, redirect,
                   render_template, request, session, url_for)
from werkzeug.utils import secure_filename

# --------------------------------------------------------------------------
# Configuration
# --------------------------------------------------------------------------
DB_HOST = os.environ.get("DB_HOST")
DB_USER = os.environ.get("DB_USER")
DB_PASSWORD = os.environ.get("DB_PASSWORD")
DB_NAME = os.environ.get("DB_NAME", "oceantracker")
DB_SSL_CA = os.environ.get("DB_SSL_CA")

AWS_REGION = os.environ.get("AWS_REGION", "us-east-1")
S3_BUCKET = os.environ.get("S3_BUCKET")
S3_PREFIX = "vault/"

AUTH_USER = os.environ.get("APP_USERNAME")
AUTH_PASS = os.environ.get("APP_PASSWORD")

MAX_UPLOAD_MB = 25
ALLOWED_EXTENSIONS = {"pdf", "png", "jpg", "jpeg", "xlsx"}
MAGIC_BYTES = {
    "pdf": (b"%PDF",),
    "png": (b"\x89PNG",),
    "jpg": (b"\xff\xd8",),
    "jpeg": (b"\xff\xd8",),
    "xlsx": (b"PK",),
}

ALLOWED_STATUSES = {"Pending Export Description", "Cleared to Load", "Vessel Arrival Delayed"}
CONTAINER_RE = re.compile(r"^[A-Z]{4}\d{7}$")
PORT_RE = re.compile(r"^[A-Z]{5}$")

BASE_RATES = {
    "USHOU-NLRTM": 1450,
    "USSAV-DEHAM": 1320,
    "USLAX-SGSIN": 890,
    "USNYC-CNSHA": 1150,
    "USMIA-AEJEA": 1650,
    "USMIA-INBOM": 1850,
    "USHOU-AEJEA": 1550,
    "USNYC-INBOM": 1750,
}
EQUIPMENT_MULTIPLIER = {"20DC": 1.0, "40HC": 1.25}
BAF_SURCHARGE = 350
THC_SURCHARGE = 150

app = Flask(__name__)
app.config.update(
    SECRET_KEY=os.environ.get("SECRET_KEY") or secrets.token_hex(32),
    MAX_CONTENT_LENGTH=MAX_UPLOAD_MB * 1024 * 1024,
    SESSION_COOKIE_HTTPONLY=True,
    SESSION_COOKIE_SAMESITE="Lax",
    SESSION_COOKIE_SECURE=os.environ.get("SESSION_COOKIE_SECURE", "0") == "1",
)
logging.basicConfig(level=logging.INFO)

# --------------------------------------------------------------------------
# Auth + CSRF
# --------------------------------------------------------------------------
def _safe_equal(a: str, b: str) -> bool:
    return hmac.compare_digest(a.encode("utf-8"), b.encode("utf-8"))

@app.before_request
def require_auth():
    if not (AUTH_USER and AUTH_PASS):
        return None
    creds = request.authorization
    if not creds or not _safe_equal(creds.username or "", AUTH_USER) \
            or not _safe_equal(creds.password or "", AUTH_PASS):
        return Response("Authentication required", 401,
                        {"WWW-Authenticate": 'Basic realm="OceanTracker"'})
    return None

def _csrf_token() -> str:
    token = session.get("_csrf")
    if not token:
        token = secrets.token_hex(16)
        session["_csrf"] = token
    return token

app.jinja_env.globals["csrf_token"] = _csrf_token

@app.before_request
def csrf_protect():
    if request.method not in ("POST", "PUT", "PATCH", "DELETE"):
        return None
    expected = session.get("_csrf")
    sent = request.headers.get("X-CSRFToken") or request.form.get("csrf_token") or ""
    if not expected or not _safe_equal(sent, expected):
        if request.path.startswith(("/upload", "/api/")):
            return jsonify({"success": False, "message": "Session expired. Refresh the page."}), 400
        abort(400)
    return None

# --------------------------------------------------------------------------
# Database & S3
# --------------------------------------------------------------------------
def get_db_connection():
    if not (DB_HOST and DB_USER and DB_PASSWORD):
        raise pymysql.err.OperationalError(2003, "DB credentials not configured")
    kwargs = dict(
        host=DB_HOST, user=DB_USER, password=DB_PASSWORD, database=DB_NAME,
        charset="utf8mb4", connect_timeout=5, cursorclass=pymysql.cursors.DictCursor,
    )
    if DB_SSL_CA:
        kwargs["ssl"] = {"ca": DB_SSL_CA}
    return pymysql.connect(**kwargs)

_s3_client = None
def get_s3():
    global _s3_client
    if _s3_client is None:
        _s3_client = boto3.client("s3", region_name=AWS_REGION)
    return _s3_client

def _fmt_size(num_bytes: int) -> str:
    return f"{max(1, round(num_bytes / 1024))} KB"

def _fmt_age(then: datetime) -> str:
    seconds = int((datetime.now(then.tzinfo) - then).total_seconds())
    if seconds < 90: return "Just now"
    if seconds < 3600: return f"{seconds // 60}m ago"
    if seconds < 86400: return f"{seconds // 3600}h ago"
    return f"{seconds // 86400}d ago"

def list_vault_files(limit=8):
    if not S3_BUCKET: return [], 0
    try:
        resp = get_s3().list_objects_v2(Bucket=S3_BUCKET, Prefix=S3_PREFIX, MaxKeys=1000)
    except (BotoCoreError, ClientError):
        return [], 0
    objects = sorted(resp.get("Contents", []), key=lambda o: o["LastModified"], reverse=True)
    files = []
    for obj in objects[:limit]:
        leaf = obj["Key"].rsplit("/", 1)[-1]
        name = leaf.split("-", 1)[1] if "-" in leaf else leaf
        files.append({"name": name, "size": _fmt_size(obj["Size"]), "time": _fmt_age(obj["LastModified"])})
    return files, len(objects)

# --------------------------------------------------------------------------
# Routes (Multi-Page Architecture with Clean UI)
# --------------------------------------------------------------------------
@app.route("/")
def dashboard():
    containers, db_error = [], None
    try:
        connection = get_db_connection()
        try:
            with connection.cursor() as cursor:
                cursor.execute("SELECT * FROM containers ORDER BY cutoff ASC;")
                containers = cursor.fetchall()
        finally:
            connection.close()
    except pymysql.MySQLError:
        db_error = "Live container data is temporarily unavailable."

    vault_files, vault_total = list_vault_files()
    pending_count = sum(1 for c in containers if c["status"] != "Cleared to Load")

    return render_template(
        "dashboard.html",
        containers=containers,
        pending_count=pending_count,
        vault_files=vault_files,
        vault_total=vault_total,
        db_error=db_error,
        s3_configured=bool(S3_BUCKET),
    ), (503 if db_error else 200)

@app.route("/port-data")
def port_data_feed():
    vessels, db_error = [], None
    try:
        connection = get_db_connection()
        try:
            with connection.cursor() as cursor:
                cursor.execute("""
                    CREATE TABLE IF NOT EXISTS port_vessels (
                        id INT AUTO_INCREMENT PRIMARY KEY,
                        vessel_name VARCHAR(255) NOT NULL,
                        terminal VARCHAR(100) NOT NULL,
                        eta DATETIME NOT NULL,
                        cutoff DATETIME NOT NULL,
                        status VARCHAR(100) NOT NULL,
                        UNIQUE KEY unique_vessel (vessel_name, terminal)
                    )
                """)
                cursor.execute("SELECT * FROM port_vessels ORDER BY eta ASC;")
                vessels = cursor.fetchall()
        finally:
            connection.close()
    except pymysql.MySQLError:
        db_error = "Live port telemetry data is temporarily unavailable."

    return render_template("port_data.html", vessels=vessels, db_error=db_error), (503 if db_error else 200)

@app.route("/containers/new", methods=["GET", "POST"])
def new_container():
    if request.method == "POST":
        form = request.form
        c_id = form.get("id", "").strip().upper()
        booking = form.get("booking", "").strip().upper()
        vessel = form.get("vessel", "").strip()
        pol = form.get("pol", "").strip().upper()
        pod = form.get("pod", "").strip().upper()
        status = form.get("status", "Pending Export Description")
        
        # parse cutoff
        raw_cutoff = form.get("cutoff", "")
        cutoff_dt = None
        for fmt in ("%Y-%m-%dT%H:%M", "%Y-%m-%d %H:%M"):
            try:
                cutoff_dt = datetime.strptime(raw_cutoff.strip(), fmt)
                break
            except ValueError:
                continue

        errors = []
        if not CONTAINER_RE.match(c_id):
            errors.append("Container number must be 4 letters followed by 7 digits (e.g. MAEU8492011).")
        if not booking or len(booking) > 50:
            errors.append("Booking reference is required.")
        if not vessel or len(vessel) > 100:
            errors.append("Vessel name is required.")
        if not PORT_RE.match(pol) or not PORT_RE.match(pod):
            errors.append("Ports must be 5-letter UN/LOCODEs (e.g. USHOU).")
        if cutoff_dt is None:
            errors.append("Cut-off must be a valid date and time.")

        if errors:
            for err in errors:
                flash(err, "error")
            return render_template("new_container.html")

        try:
            connection = get_db_connection()
            try:
                with connection.cursor() as cursor:
                    cursor.execute(
                        """
                        INSERT INTO containers (id, booking, vessel, pol, pod, cutoff, status, edos, loading_status)
                        VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
                        """,
                        (c_id, booking, vessel, pol, pod, cutoff_dt.strftime("%Y-%m-%d %H:%M"),
                         status, "TBD", "Dispatched to Shipper"),
                    )
                connection.commit()
            finally:
                connection.close()
        except pymysql.err.IntegrityError:
            flash(f"Container {c_id} is already on the watchlist.", "error")
            return render_template("new_container.html")
        except pymysql.MySQLError:
            flash("Could not save the container. Please try again.", "error")
            return render_template("new_container.html")

        flash(f"Container {c_id} successfully added to active watchlist.", "success")
        return redirect(url_for("dashboard"))

    return render_template("new_container.html")

@app.route("/spot-quote", methods=["GET", "POST"])
def spot_quote_page():
    breakdown = None
    error = None
    if request.method == "POST":
        pol = str(request.form.get("pol", "")).upper()
        pod = str(request.form.get("pod", "")).upper()
        eq_size = str(request.form.get("equipment", "40HC")).upper()
        weight = float(request.form.get("weight", 20000) or 20000)
        hazmat = request.form.get("hazmat") == "on"

        if eq_size not in EQUIPMENT_MULTIPLIER:
            error = "Unsupported equipment size."
        else:
            base_cost = BASE_RATES.get(f"{pol}-{pod}")
            if base_cost is None:
                error = f"No tariff rate on file for {pol} → {pod}."
            else:
                final_rate = int(base_cost * EQUIPMENT_MULTIPLIER[eq_size])
                weight_fee = 175 if weight > 35000 else 0
                hazmat_fee = 450 if hazmat else 0
                total = final_rate + BAF_SURCHARGE + THC_SURCHARGE + weight_fee + hazmat_fee

                today = date.today()
                valid_until = date(today.year, today.month, calendar.monthrange(today.year, today.month)[1])

                breakdown = {
                    "pol": pol,
                    "pod": pod,
                    "equipment": eq_size,
                    "base": f"${final_rate:,}",
                    "weight": f"${weight_fee:,}",
                    "hazmat": f"${hazmat_fee:,}",
                    "bunker": f"${BAF_SURCHARGE:,}",
                    "thc": f"${THC_SURCHARGE:,}",
                    "total": f"${total:,}",
                    "valid_until": f"{valid_until:%B} {valid_until.day}, {valid_until.year}"
                }
    return render_template("spot_quote.html", breakdown=breakdown, error=error)

@app.route("/upload", methods=["POST"])
def upload_document():
    if not S3_BUCKET:
        return jsonify({"success": False, "message": "Document vault is not configured"}), 503

    file = request.files.get("document")
    if file is None or file.filename == "":
        return jsonify({"success": False, "message": "No file selected"}), 400

    original = file.filename
    ext = original.rsplit(".", 1)[-1].lower() if "." in original else ""
    if ext not in ALLOWED_EXTENSIONS:
        return jsonify({"success": False, "message": "Unsupported file type. Allowed: PDF, PNG, JPG, XLSX"}), 400

    head = file.stream.read(8)
    file.stream.seek(0)
    if not any(head.startswith(sig) for sig in MAGIC_BYTES[ext]):
        return jsonify({"success": False, "message": "File contents do not match its extension"}), 400

    filename = secure_filename(original)
    if not filename.lower().endswith("." + ext):
        filename = f"document.{ext}"

    file.stream.seek(0, os.SEEK_END)
    size_bytes = file.stream.tell()
    file.stream.seek(0)

    key = f"{S3_PREFIX}{uuid4().hex}-{filename}"
    try:
        get_s3().upload_fileobj(
            file.stream, S3_BUCKET, key,
            ExtraArgs={
                "ContentType": mimetypes.guess_type(filename)[0] or "application/octet-stream",
                "ServerSideEncryption": "AES256",
            },
        )
    except (BotoCoreError, ClientError):
        return jsonify({"success": False, "message": "Upload failed."}), 502

    return jsonify({
        "success": True,
        "filename": filename,
        "size": _fmt_size(size_bytes),
        "message": f"Successfully vaulted {filename} to Amazon S3",
    })

@app.route("/api/quote", methods=["POST"])
def generate_quote():
    data = request.get_json(silent=True)
    if not isinstance(data, dict):
        return jsonify({"success": False, "message": "Invalid request"}), 400

    pol = str(data.get("pol", "")).upper()
    pod = str(data.get("pod", "")).upper()
    eq_size = str(data.get("equipment", "40HC")).upper()

    if eq_size not in EQUIPMENT_MULTIPLIER:
        return jsonify({"success": False, "message": "Unsupported equipment size"}), 400

    base_cost = BASE_RATES.get(f"{pol}-{pod}")
    if base_cost is None:
        return jsonify({"success": False, "message": f"No rate on file for {pol} → {pod}"}), 400

    final_rate = int(base_cost * EQUIPMENT_MULTIPLIER[eq_size])
    total_cost = final_rate + BAF_SURCHARGE + THC_SURCHARGE

    today = date.today()
    valid_until = date(today.year, today.month, calendar.monthrange(today.year, today.month)[1])

    return jsonify({
        "success": True,
        "base_rate": f"${final_rate:,}",
        "baf_surcharge": f"${BAF_SURCHARGE:,}",
        "thc_surcharge": f"${THC_SURCHARGE:,}",
        "total": f"${total_cost:,}",
        "valid_until_label": f"{valid_until:%b} {valid_until.day}",
    })

if __name__ == "__main__":
    app.run(
        host=os.environ.get("HOST", "127.0.0.1"),
        port=int(os.environ.get("PORT", "8080")),
        debug=os.environ.get("FLASK_DEBUG", "0") == "1",
    )