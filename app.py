from flask import Flask, render_template, request, jsonify, redirect, url_for
import boto3
import pymysql
import os
from werkzeug.utils import secure_filename

app = Flask(__name__)

# Updated with authentic freight forwarding terminology
mock_containers = [
    {
        "id": "MSKU1234567",
        "booking": "BK-9928410",
        "vessel": "Maersk Mc-Kinney Møller",
        "pol": "USHOU",
        "pod": "NLRTM",
        "cutoff": "2026-10-18 14:00",
        "status": "Pending Export Description",
        "edos": "2026-10-20",
        "loading_status": "Gate-In CY • En route to AEPJA"
    },
    {
        "id": "HLXU9876543",
        "booking": "BK-4410293",
        "vessel": "Hapag-Lloyd Berlin Express",
        "pol": "USSAV",
        "pod": "DEHAM",
        "cutoff": "2026-10-17 09:00",
        "status": "Cleared to Load",
        "edos": "2026-10-19",
        "loading_status": "On Track • Ready for Vessel"
    },
    {
        "id": "CMAU4582910",
        "booking": "BK-7731904",
        "vessel": "CMA CGM Jacques Saadé",
        "pol": "USLAX",
        "pod": "SGSIN",
        "cutoff": "2026-10-15 16:00",
        "status": "Vessel Arrival Delayed",
        "edos": "2026-10-17",
        "loading_status": "Berth Congestion • +12h"
    },
    {
        "id": "OOLU8821044",
        "booking": "BK-3029182",
        "vessel": "OOCL Hong Kong",
        "pol": "USNYC",
        "pod": "CNSHA",
        "cutoff": "2026-10-21 17:00",
        "status": "Cleared to Load",
        "edos": "2026-10-24",
        "loading_status": "On Track • Yard Stacked"
    }
]

uploaded_vault_files = [
    {"name": "MSKU1234567_Commercial_Invoice.pdf", "size": "248 KB", "time": "2h ago"},
    {"name": "HLXU9876543_Bill_of_Lading.pdf", "size": "412 KB", "time": "5h ago"}
]

@app.route('/')
def dashboard():
    return render_template(
        'dashboard.html',
        containers=mock_containers,
        vault_files=uploaded_vault_files
    )

@app.route('/add_container', methods=['POST'])
def add_container():
    new_c = {
        "id": request.form.get("id", "").upper(),
        "booking": request.form.get("booking", "BK-8841200").upper(),
        "vessel": request.form.get("vessel", "MSC Gülsün"),
        "pol": request.form.get("pol", "USHOU"),
        "pod": request.form.get("pod", "NLRTM"),
        "cutoff": request.form.get("cutoff", "2026-10-19 12:00").replace("T", " "),
        "status": request.form.get("status", "Pending Export Description"),
        "edos": "TBD",
        "loading_status": "Dispatched to Shipper"
    }
    mock_containers.insert(0, new_c)
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
        bucket_name = 'YOUR_BUCKET_NAME_HERE'  # Keep your S3 bucket name here
        
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

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=8080, debug=True)