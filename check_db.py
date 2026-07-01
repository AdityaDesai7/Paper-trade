import sqlite3
import os
import json

db_path = r'output\paper_trades.db'
if not os.path.exists(db_path):
    print('DB does not exist!')
else:
    try:
        conn = sqlite3.connect(db_path)
        conn.row_factory = sqlite3.Row
        count = conn.execute("SELECT COUNT(*) FROM trades").fetchone()[0]
        print(f"Total trade rows: {count}")
        trades = conn.execute("SELECT * FROM trades ORDER BY id DESC LIMIT 5").fetchall()
        for t in trades:
            print(dict(t))
        conn.close()
    except Exception as e:
        print(f"Error reading DB: {e}")
