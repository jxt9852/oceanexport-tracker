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
from flask import (Flask, Response, abort, flash, jsonify, redirect,
                   render_template, request, session, url_for)
from werkzeug.utils import secure_filename

# --------------------------------------------------------------------------
# Configuration (all secrets come from the environment, never from source)
# --------------------------------------------------------------------------
DB_HOST = os.environ.get("DB_HOST")
DB_USER = os.environ.get("DB_USER")
DB_PASSWORD = os.environ.get("DB_PASSWORD")
DB_NAME = os.environ.get("DB_NAME", "oceantracker")
DB_SSL_CA = os.environ.get("DB_SSL_CA")  # path to the RDS CA bundle (optional)

AWS_REGION = os.environ.get("AWS_REGION", "us-east-1")
S3_BUCKET = os.environ.get("S3_BUCKET")
S3_PREFIX = "vault/"

# Optional HTTP Basic auth. Set both to protect the whole app.
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
CONTAINER_RE = re.compile(r"^[A-Z]{4}\d{7}$")  # ISO 6346 shape
PORT_RE = re.compile(r"^[A-Z]{5}$")  # UN/LOCODE

BASE_RATES = {
    "USHOU-NLRTM": 1450,
    "USSAV-DEHAM": 1320,
    "USLAX-SGSIN": 890,
    "USNYC-CNSHA": 1150,
}
EQUIPMENT_MULTIPLIER = {"20DC": 1.0, "40HC": 1.25}
BAF_SURCHARGE = 350
THC_SURCHARGE = 150

app = Flask(__name__)
app.config.update(
    # Sessions (used for CSRF) must survive across gunicorn workers, so set a
    # stable SECRET_KEY in production. The random fallback is dev-only.
    SECRET_KEY=os.environ.get("SECRET_KEY") or secrets.token_hex(32),
    MAX_CONTENT_LENGTH=MAX_UPLOAD_MB * 1024 * 1024,
    SESSION_COOKIE_HTTPONLY=True,
    SESSION_COOKIE_SAMESITE="Lax",
    SESSION_COOKIE_SECURE=os.environ.get("SESSION_COOKIE_SECURE", "0") == "1",
)
logging.basicConfig(level=logging.INFO)

if not os.environ.get("SECRET_KEY"):
    app.logger.warning("SECRET_KEY is not set; using a random key (sessions reset on restart).")
if not (AUTH_USER and AUTH_PASS):
    app.logger.warning("APP_USERNAME/APP_PASSWORD not set; the app is running WITHOUT authentication.")


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
            return jsonify({"success": False, "message": "Session expired. Refresh the page and try again."}), 400
        abort(400)
    return None


@app.errorhandler(413)
def too_large(_e):
    return jsonify({"success": False, "message": f"File too large (max {MAX_UPLOAD_MB} MB)"}), 413


# --------------------------------------------------------------------------
# Database
# --------------------------------------------------------------------------
def get_db_connection():
    if not (DB_HOST and DB_USER and DB_PASSWORD):
        raise pymysql.err.OperationalError(2003, "DB_HOST / DB_USER / DB_PASSWORD are not configured")
    kwargs = dict(
        host=DB_HOST,
        user=DB_USER,
        password=DB_PASSWORD,
        database=DB_NAME,
        charset="utf8mb4",
        connect_timeout=5,
        cursorclass=pymysql.cursors.DictCursor,
    )
    if DB_SSL_CA:
        kwargs["ssl"] = {"ca": DB_SSL_CA}
    return pymysql.connect(**kwargs)


# --------------------------------------------------------------------------
# S3 helpers
# --------------------------------------------------------------------------
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
    if seconds < 90:
        return "Just now"
    if seconds < 3600:
        return f"{seconds // 60}m ago"
    if seconds < 86400:
        return f"{seconds // 3600}h ago"
    return f"{seconds // 86400}d ago"


def list_vault_files(limit=8):
    """Most recent vault files straight from S3, so state survives restarts."""
    if not S3_BUCKET:
        return [], 0
    try:
        resp = get_s3().list_objects_v2(Bucket=S3_BUCKET, Prefix=S3_PREFIX, MaxKeys=1000)
    except (BotoCoreError, ClientError):
        app.logger.exception("Could not list S3 vault")
        return [], 0
    objects = sorted(resp.get("Contents", []), key=lambda o: o["LastModified"], reverse=True)
    files = []
    for obj in objects[:limit]:
        leaf = obj["Key"].rsplit("/", 1)[-1]
        name = leaf.split("-", 1)[1] if "-" in leaf else leaf  # strip the uuid prefix
        files.append({"name": name, "size": _fmt_size(obj["Size"]), "time": _fmt_age(obj["LastModified"])})
    return files, len(objects)


# --------------------------------------------------------------------------
# Routes
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
        app.logger.exception("Dashboard query failed")
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


def _parse_cutoff(raw: str):
    for fmt in ("%Y-%m-%dT%H:%M", "%Y-%m-%d %H:%M"):
        try:
            return datetime.strptime(raw.strip(), fmt)
        except ValueError:
            continue
    return None


@app.route("/add_container", methods=["POST"])
def add_container():
    form = request.form
    c_id = form.get("id", "").strip().upper()
    booking = form.get("booking", "").strip().upper()
    vessel = form.get("vessel", "").strip()
    pol = form.get("pol", "").strip().upper()
    pod = form.get("pod", "").strip().upper()
    status = form.get("status", "Pending Export Description")
    cutoff_dt = _parse_cutoff(form.get("cutoff", ""))

    errors = []
    if not CONTAINER_RE.match(c_id):
        errors.append("Container number must be 4 letters followed by 7 digits (e.g. MAEU8492011).")
    if not booking or len(booking) > 50:
        errors.append("Booking reference is required (50 characters max).")
    if not vessel or len(vessel) > 100:
        errors.append("Vessel name is required (100 characters max).")
    if not PORT_RE.match(pol) or not PORT_RE.match(pod):
        errors.append("Ports must be 5-letter UN/LOCODEs (e.g. USHOU).")
    if cutoff_dt is None:
        errors.append("Cut-off must be a valid date and time.")
    if status not in ALLOWED_STATUSES:
        errors.append("Unknown status.")

    if errors:
        for message in errors:
            flash(message, "error")
        return redirect(url_for("dashboard"))

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
    except pymysql.err.IntegrityError as exc:
        if exc.args and exc.args[0] == 1062:
            flash(f"Container {c_id} is already on the watchlist.", "error")
        else:
            app.logger.exception("Insert failed")
            flash("Could not save the container.", "error")
        return redirect(url_for("dashboard"))
    except pymysql.MySQLError:
        app.logger.exception("Insert failed")
        flash("Could not save the container. Please try again.", "error")
        return redirect(url_for("dashboard"))

    flash(f"Container {c_id} added to the watchlist.", "success")
    return redirect(url_for("dashboard"))


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
        return jsonify({"success": False,
                        "message": "Unsupported file type. Allowed: PDF, PNG, JPG, XLSX"}), 400

    head = file.stream.read(8)
    file.stream.seek(0)
    if not any(head.startswith(sig) for sig in MAGIC_BYTES[ext]):
        return jsonify({"success": False, "message": "File contents do not match its extension"}), 400

    filename = secure_filename(original)
    if not filename.lower().endswith("." + ext):  # secure_filename strips non-ASCII names
        filename = f"document.{ext}"

    file.stream.seek(0, os.SEEK_END)
    size_bytes = file.stream.tell()
    file.stream.seek(0)

    key = f"{S3_PREFIX}{uuid4().hex}-{filename}"
    try:
        get_s3().upload_fileobj(
            file.stream,
            S3_BUCKET,
            key,
            ExtraArgs={
                "ContentType": mimetypes.guess_type(filename)[0] or "application/octet-stream",
                "ServerSideEncryption": "AES256",
            },
        )
    except (BotoCoreError, ClientError):
        app.logger.exception("S3 upload failed for %s", key)
        return jsonify({"success": False, "message": "Upload failed. Please try again."}), 502

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
        "valid_until": valid_until.isoformat(),
        "valid_until_label": f"{valid_until:%b} {valid_until.day}",
    })


if __name__ == "__main__":
    # Local development only. In production run: gunicorn -w 2 -b 0.0.0.0:8080 app:app
    app.run(
        host=os.environ.get("HOST", "127.0.0.1"),
        port=int(os.environ.get("PORT", "8080")),
        debug=os.environ.get("FLASK_DEBUG", "0") == "1",
    )