from flask import Flask, render_template, request, jsonify
import boto3
import pymysql
import os
from werkzeug.utils import secure_filename

app = Flask(__name__)

# TODO: Connect to Amazon RDS later
# db = pymysql.connect(host='...', user='...', password='...', database='...')

@app.route('/')
def dashboard():
    # Rich maritime mock data until RDS is live
    mock_containers = [
        {
            "id": "MSKU1234567",
            "vessel": "Maersk Mc-Kinney Møller",
            "route": "USHOU → NLRTM",
            "cutoff": "2026-10-18 14:00",
            "status": "Pending"
        },
        {
            "id": "HLXU9876543",
            "vessel": "Hapag-Lloyd Berlin Express",
            "route": "USSAV → DEHAM",
            "cutoff": "2026-10-17 09:00",
            "status": "Cleared"
        },
        {
            "id": "CMAU4491028",
            "vessel": "CMA CGM Jacques Saadé",
            "route": "USLAX → SGSIN",
            "cutoff": "2026-10-15 16:30",
            "status": "Urgent"
        },
        {
            "id": "OOLU8832104",
            "vessel": "OOCL Hong Kong",
            "route": "USNYC →CNSHA",
            "cutoff": "2026-10-21 11:00",
            "status": "Cleared"
        }
    ]
    return render_template('dashboard.html', containers=mock_containers)

@app.route('/upload', methods=['POST'])
def upload_document():
    if 'document' not in request.files:
        return jsonify({"success": False, "message": "No file uploaded"}), 400
    
    file = request.files['document']
    if file.filename == '':
        return jsonify({"success": False, "message": "No selected file"}), 400
        
    filename = secure_filename(file.filename)
    file.save(filename)
    
    try:
        s3 = boto3.client('s3', region_name='us-east-1')
        bucket_name = 'YOUR_BUCKET_NAME_HERE'  # <-- Keep your exact S3 bucket name here
        
        s3.upload_file(filename, bucket_name, filename)
        os.remove(filename)
        
        return jsonify({
            "success": True,
            "filename": filename,
            "message": f"Encrypted & vaulted {filename} to Amazon S3"
        })
    except Exception as e:
        if os.path.exists(filename):
            os.remove(filename)
        return jsonify({"success": False, "message": f"S3 Upload Failed: {str(e)}"}), 500

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=8080, debug=True)