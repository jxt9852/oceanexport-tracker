from flask import Flask, render_template, request, jsonify, redirect, url_for
import boto3
import pymysql
import os
from werkzeug.utils import secure_filename

app = Flask(__name__)

# AWS RDS Connection Setup
def get_db_connection():
    return pymysql.connect(
        host='oceantracker-db.cefhkqlkm5ww.us-east-1.rds.amazonaws.com',
        user='admin',
        password='OceanTracker2026!',
        database='oceantracker',  # <--- Added this line
        cursorclass=pymysql.cursors.DictCursor
    )

uploaded_vault_files = [
    {"name": "MSKU1234567_Commercial_Invoice.pdf", "size": "248 KB", "time": "2h ago"},
    {"name": "HLXU9876543_Bill_of_Lading.pdf", "size": "412 KB", "time": "5h ago"}
]

@app.route('/')
def dashboard():
    # Fetch live data from AWS RDS
    connection = get_db_connection()
    try:
        with connection.cursor() as cursor:
            cursor.execute("SELECT * FROM containers ORDER BY cutoff ASC;")
            live_containers = cursor.fetchall()
    finally:
        connection.close()

    return render_template(
        'dashboard.html',
        containers=live_containers,
        vault_files=uploaded_vault_files
    )

@app.route('/add_container', methods=['POST'])
def add_container():
    # Capture form data
    c_id = request.form.get("id", "").upper()
    booking = request.form.get("booking", "BK-8841200").upper()
    vessel = request.form.get("vessel", "MSC Gülsün")
    pol = request.form.get("pol", "USHOU")
    pod = request.form.get("pod", "NLRTM")
    cutoff = request.form.get("cutoff", "2026-10-19 12:00").replace("T", " ")
    status = request.form.get("status", "Pending Export Description")
    edos = "TBD"
    loading_status = "Dispatched to Shipper"

    # Insert into AWS RDS
    connection = get_db_connection()
    try:
        with connection.cursor() as cursor:
            sql = """
            INSERT INTO containers (id, booking, vessel, pol, pod, cutoff, status, edos, loading_status)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
            """
            cursor.execute(sql, (c_id, booking, vessel, pol, pod, cutoff, status, edos, loading_status))
        connection.commit()
    finally:
        connection.close()

    return redirect(url_for('dashboard'))

@app.route('/upload', methods=['POST'])
def upload_document():
    if 'document' not in request.files:
        return jsonify({"success": False, "message": "No file uploaded"}), 400
    
    file = request.files['document']
    if file.filename == '':
        return jsonify({"success": False, "message": "No selected file"}), 400
        
    filename = secure_filename(file.filename)
    file.save(filename)
    
    size_kb = max(1, round(os.path.getsize(filename) / 1024))
    
    try:
        s3 = boto3.client('s3', region_name='us-east-1')
        bucket_name = 'YOUR_BUCKET_NAME_HERE'  # <-- Keep your actual S3 bucket name here!
        
        s3.upload_file(filename, bucket_name, filename)
        os.remove(filename)
        
        uploaded_vault_files.insert(0, {
            "name": filename,
            "size": f"{size_kb} KB",
            "time": "Just now"
        })
        
        return jsonify({
            "success": True,
            "filename": filename,
            "size": f"{size_kb} KB",
            "message": f"Successfully vaulted {filename} to Amazon S3"
        })
    except Exception as e:
        if os.path.exists(filename):
            os.remove(filename)
        return jsonify({"success": False, "message": f"S3 Upload Failed: {str(e)}"}), 500

@app.route('/api/quote', methods=['POST'])
def generate_quote():
    data = request.json
    pol = data.get('pol')
    pod = data.get('pod')
    eq_size = data.get('equipment', '40HC')

    base_rates = {
        "USHOU-NLRTM": 1450,
        "USSAV-DEHAM": 1320,
        "USLAX-SGSIN": 890,
        "USNYC-CNSHA": 1150
    }

    route_key = f"{pol}-{pod}"
    base_cost = base_rates.get(route_key, 1250)

    multiplier = 1.0 if eq_size == '20DC' else 1.25
    final_rate = int(base_cost * multiplier)

    total_cost = final_rate + 350 + 150 

    return jsonify({
        "success": True,
        "base_rate": f"${final_rate:,}",
        "baf_surcharge": "$350",
        "thc_surcharge": "$150",
        "total": f"${total_cost:,}",
        "valid_until": "2026-10-31"
    })

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=8080, debug=True)