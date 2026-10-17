"""Create the oceantracker schema and (optionally) seed demo data.

Usage:
    export DB_HOST=... DB_USER=... DB_PASSWORD=...
    python init_db.py            # create schema, seed demo rows only if the table is empty
    python init_db.py --reset    # DESTRUCTIVE: wipe the containers table, then reseed
"""
import argparse
import os
import sys

import pymysql

DB_NAME = os.environ.get("DB_NAME", "oceantracker")

SEED_DATA = [
    ("MSKU1234567", "BK-9928410", "Maersk Mc-Kinney Møller", "USHOU", "NLRTM", "2026-10-18 14:00",
     "Pending Export Description", "2026-10-20", "Gate-In CY • Awaiting Vessel"),
    ("HLXU9876543", "BK-4410293", "Hapag-Lloyd Berlin Express", "USSAV", "DEHAM", "2026-10-17 09:00",
     "Cleared to Load", "2026-10-19", "On Track • Ready for Vessel"),
    ("CMAU4582910", "BK-7731904", "CMA CGM Jacques Saadé", "USLAX", "SGSIN", "2026-10-15 16:00",
     "Vessel Arrival Delayed", "2026-10-17", "Berth Congestion • +12h"),
    ("OOLU8821044", "BK-3029182", "OOCL Hong Kong", "USNYC", "CNSHA", "2026-10-21 17:00",
     "Cleared to Load", "2026-10-24", "On Track • Yard Stacked"),
]


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--reset", action="store_true",
                        help="wipe the containers table before seeding (destructive)")
    args = parser.parse_args()

    host, user, password = (os.environ.get(k) for k in ("DB_HOST", "DB_USER", "DB_PASSWORD"))
    if not (host and user and password):
        sys.exit("Set DB_HOST, DB_USER and DB_PASSWORD in the environment first.")

    kwargs = dict(host=host, user=user, password=password, charset="utf8mb4",
                  connect_timeout=10, cursorclass=pymysql.cursors.DictCursor)
    if os.environ.get("DB_SSL_CA"):
        kwargs["ssl"] = {"ca": os.environ["DB_SSL_CA"]}

    connection = pymysql.connect(**kwargs)
    try:
        with connection.cursor() as cursor:
            cursor.execute(f"CREATE DATABASE IF NOT EXISTS `{DB_NAME}` CHARACTER SET utf8mb4;")
            cursor.execute(f"USE `{DB_NAME}`;")
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
                ) CHARACTER SET utf8mb4;
            """)

            if args.reset:
                cursor.execute("TRUNCATE TABLE containers;")
                print("containers table wiped (--reset).")

            cursor.execute("SELECT COUNT(*) AS n FROM containers;")
            if cursor.fetchone()["n"] == 0:
                cursor.executemany(
                    """
                    INSERT INTO containers (id, booking, vessel, pol, pod, cutoff, status, edos, loading_status)
                    VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
                    """,
                    SEED_DATA,
                )
                print(f"Seeded {len(SEED_DATA)} demo containers.")
            else:
                print("containers table already has data; skipping seed.")
        connection.commit()
        print(f"Database '{DB_NAME}' is ready.")
    finally:
        connection.close()


if __name__ == "__main__":
    main()