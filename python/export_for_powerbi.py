"""Exporta las tablas 'mart' a CSV para conectarlas en Power BI Desktop sin
necesitar un driver ODBC de SQLite (Power BI no lo trae de fabrica). Correr
despues de analysis.py.
"""

import csv
import os
import sqlite3

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DB_PATH = os.path.join(BASE_DIR, "data", "teleterapia.db")
OUTPUT_DIR = os.path.join(BASE_DIR, "data", "powerbi")

# channels es la dimension que relaciona a las tres tablas mart por canal en
# el modelo de Power BI, para que un unico slicer filtre todos los visuales.
TABLES = [
    "channels",
    "months",
    "channel_metrics",
    "monthly_channel_metrics",
    "funnel_conversion",
    "ab_test_results",
    "ab_test_summary",
    "ab_test_daily",
    "state_metrics",
    "state_channel_metrics",
]


def export_table(conn, table_name):
    cursor = conn.execute(f"SELECT * FROM {table_name}")
    columns = [d[0] for d in cursor.description]
    rows = cursor.fetchall()

    path = os.path.join(OUTPUT_DIR, f"{table_name}.csv")
    with open(path, "w", newline="", encoding="utf-8") as f:
        writer = csv.writer(f)
        writer.writerow(columns)
        writer.writerows(rows)
    return path, len(rows)


def main():
    os.makedirs(OUTPUT_DIR, exist_ok=True)
    conn = sqlite3.connect(DB_PATH)
    for table in TABLES:
        path, n_rows = export_table(conn, table)
        print(f"{table}: {n_rows} filas -> {path}")
    conn.close()


if __name__ == "__main__":
    main()
