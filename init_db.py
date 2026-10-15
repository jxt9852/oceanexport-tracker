import pymysql

# 1. Connect to your new AWS RDS instance
connection = pymysql.connect(
    host='oceantracker-db.cefhkqlkm5ww.us-east-1.rds.amazonaws.com',
    user='admin',
    password='OceanTracker2026!',
    cursorclass=pymysql.cursors.DictCursor
)

try:
    with connection.cursor() as cursor:
        # 2. Create the database and table
        cursor.execute("CREATE DATABASE IF NOT EXISTS oceantracker;")
        cursor.execute("USE oceantracker;")
        
        cursor.execute("""
        CREATE TABLE IF NOT EXISTS containers (
            id VARCHAR(20) PRIMARY KEY,
            booking VARCHAR(50),
            vessel VARCHAR(100),
            pol VARCHAR(10),
            pod VARCHAR(10),
            cutoff VARCHAR(50),
            status VARCHAR(50),
            edos VARCHAR(20),
            loading_status VARCHAR(100)
        );
        """)
        
        # 3. Clear the table just in case, then insert the startup data
        cursor.execute("TRUNCATE TABLE containers;")
        
        insert_query = """
        INSERT INTO containers (id, booking, vessel, pol, pod, cutoff, status, edos, loading_status)
        VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
        """
        
        mock_data = [
            ("MSKU1234567", "BK-9928410", "Maersk Mc-Kinney Møller", "USHOU", "NLRTM", "2026-10-18 14:00", "Pending Export Description", "2026-10-20", "Gate-In CY • En route to AEPJA"),
            ("HLXU9876543", "BK-4410293", "Hapag-Lloyd Berlin Express", "USSAV", "DEHAM", "2026-10-17 09:00", "Cleared to Load", "2026-10-19", "On Track • Ready for Vessel"),
            ("CMAU4582910", "BK-7731904", "CMA CGM Jacques Saadé", "USLAX", "SGSIN", "2026-10-15 16:00", "Vessel Arrival Delayed", "2026-10-17", "Berth Congestion • +12h"),
            ("OOLU8821044", "BK-3029182", "OOCL Hong Kong", "USNYC", "CNSHA", "2026-10-21 17:00", "Cleared to Load", "2026-10-24", "On Track • Yard Stacked")
        ]
        
        cursor.executemany(insert_query, mock_data)

    connection.commit()
    print("✅ AWS RDS Initialized: Database 'oceantracker' created and populated!")

finally:
    connection.close()