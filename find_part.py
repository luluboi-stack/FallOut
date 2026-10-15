import sqlite3
import pandas as pd
import json
import os

conn = sqlite3.connect("loading_persistence.db")

# 1. Fetch all distinct dates available in the database
date_query = """
SELECT 
    substr(timestamp, 1, 10) AS audit_date, 
    COUNT(*) AS total_records 
FROM material_decision_audit 
WHERE timestamp IS NOT NULL 
GROUP BY audit_date 
ORDER BY audit_date DESC;
"""
dates_df = pd.read_sql(date_query, conn)

if dates_df.empty:
    print("❌ No audit records found in the database.")
    conn.close()
    exit()

print("\n" + "="*70)
print("📅 AVAILABLE AUDIT DATES IN DATABASE:")
print("="*70)
for idx, row in dates_df.iterrows():
    print(f"  [{idx + 1}]  {row['audit_date']}  ({row['total_records']:,} records)")
print("="*70)

# 2. Select Date
choice = input(f"Select date number [1-{len(dates_df)}] (Default: 1 for latest): ").strip()
selected_idx = int(choice) - 1 if choice.isdigit() and 1 <= int(choice) <= len(dates_df) else 0
chosen_date = dates_df.iloc[selected_idx]['audit_date']

# 3. Optional Part Filter (Press Enter to get ALL parts)
part_filter = input(f"Enter Part ID to filter on {chosen_date} (or press Enter for ALL parts): ").strip().upper()

# 4. Fetch Everything for the chosen date
if part_filter:
    query = """
    SELECT * 
    FROM material_decision_audit 
    WHERE substr(timestamp, 1, 10) = ? 
      AND part_id LIKE ? 
    ORDER BY timestamp DESC;
    """
    params = [chosen_date, f"%{part_filter}%"]
    json_filename = f"audit_{chosen_date}_{part_filter}.json"
else:
    query = """
    SELECT * 
    FROM material_decision_audit 
    WHERE substr(timestamp, 1, 10) = ? 
    ORDER BY timestamp DESC;
    """
    params = [chosen_date]
    json_filename = f"audit_{chosen_date}_ALL_PARTS.json"

df = pd.read_sql(query, conn, params=params)
conn.close()

# 5. Export to Pretty JSON
if df.empty:
    print(f"\n❌ No records found for date {chosen_date} with part '{part_filter}'.")
else:
    records = df.to_dict(orient="records")
    json_path = os.path.abspath(json_filename)

    with open(json_path, "w", encoding="utf-8") as f:
        json.dump(records, f, indent=2, default=str)

    print("\n" + "="*70)
    print(f"✅ EXPORT COMPLETE FOR {chosen_date}!")
    print(f"📊 Total Records Exported: {len(records):,}")
    print(f"📁 JSON File Saved At:     {json_path}")
    print("="*70)
    print("Tip: You can open this JSON file directly in VS Code to collapse sections and search (Ctrl+F).\n")