from flask import Flask, render_template, request
import boto3
import pymysql
import os
from werkzeug.utils import secure_filename

app = Flask(__name__)

# TODO: Connect to Amazon RDS later
# db = pymysql.connect(host='...', user='...', password='...', database='...')

@app.route('/')
def dashboard():
    # Midpoint Hack: Use mock data until your RDS database is live
    mock_containers = [
        {"id": "MSKU1234567", "cutoff": "2026-10-18 14:00", "status": "Pending"},
        {"id": "HLXU9876543", "cutoff": "2026-10-17 09:00", "status": "Cleared"}
    ]
    # This sends the data to your dashboard.html file to be displayed
    return render_template('dashboard.html', containers=mock_containers)

@app.route('/upload', methods=['POST'])
def upload_document():
    if 'document' not in request.files:
        return "No file uploaded", 400
    
    file = request.files['document']
    if file.filename == '':
        return "No selected file", 400
        
    # Secure the filename and save it temporarily to the EC2 instance
    filename = secure_filename(file.filename)
    file.save(filename)
    
    try:
        # Connect to S3 and upload the file
        s3 = boto3.client('s3', region_name='us-east-1')
        bucket_name = 'YOUR_BUCKET_NAME_HERE'
        
        s3.upload_file(filename, bucket_name, filename)
        
        # Delete the temporary file from the EC2 hard drive
        os.remove(filename)
        
        return f"Successfully uploaded {filename} to S3!"
    except Exception as e:
        return f"S3 Upload Failed: {str(e)}"

if __name__ == '__main__':
    # Running on 0.0.0.0 ensures it works when deployed to the EC2 server
    app.run(host='0.0.0.0', port=8080)