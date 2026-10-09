from flask import Flask, render_template, request
import boto3
import pymysql

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
    # TODO: Add Boto3 S3 upload logic here next week
    return f"Successfully received {file.filename} (S3 upload pending)"

if __name__ == '__main__':
    # Running on 0.0.0.0 ensures it works when deployed to the EC2 server
    app.run(host='0.0.0.0', port=8080)