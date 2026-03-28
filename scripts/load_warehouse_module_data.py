import csv
import sqlite3
from datetime import datetime
from pathlib import Path

DB_PATH = Path(r"C:\Users\alsen\Desktop\dss_dashboard_project\dss_inventory_demo.db")
INVENTORY_CSV = Path(r"C:\Users\alsen\Desktop\senior project\d2\Data Workbook_sara.csv")
STAFF_CSV = Path(r"C:\Users\alsen\Desktop\senior project\d2\warehouse_staff_productivity.csv")

# Based on your Python assumptions
ITEM_VOL_ASSUMPTIONS = {
    100: 12.0,
    101: 0.5,
    102: 0.5,
    103: 0.2,
    104: 0.2,
    105: 12.0,
    106: 75.0,
    107: 0.8,
    108: 0.3,
    109: 0.6,
}

TOTAL_SQ_FT = 80000.0
CLEAR_HEIGHT = 20.0
NON_STORAGE_BUFFER = 0.25
UTILIZATION_RATE = 0.85


def parse_date(value: str):
    value = (value or "").strip()
    for fmt in ("%d/%m/%Y", "%Y-%m-%d"):
        try:
            return datetime.strptime(value, fmt).date().isoformat()
        except ValueError:
            continue
    return None


def load_inventory_space_metrics(conn: sqlite3.Connection):
    latest_by_item = {}
    with INVENTORY_CSV.open("r", encoding="utf-8-sig", newline="") as f:
        reader = csv.DictReader(f)
        for row in reader:
            item_id_raw = row.get("Item_ID")
            stock_raw = row.get("Current_Stock")
            date_raw = row.get("Date")

            try:
                item_id = int(float(item_id_raw))
                stock = float(stock_raw)
            except (TypeError, ValueError):
                continue

            date_iso = parse_date(date_raw)
            if not date_iso:
                continue

            prev = latest_by_item.get(item_id)
            if not prev or date_iso > prev["date"]:
                latest_by_item[item_id] = {"date": date_iso, "stock": stock}

    net_storage_sq_ft = TOTAL_SQ_FT * (1 - NON_STORAGE_BUFFER)
    theoretical_cubic_capacity = net_storage_sq_ft * CLEAR_HEIGHT
    effective_capacity = theoretical_cubic_capacity * UTILIZATION_RATE

    occupied = 0.0
    for item_id, rec in latest_by_item.items():
        unit_vol = ITEM_VOL_ASSUMPTIONS.get(item_id, 1.0)
        occupied += rec["stock"] * unit_vol

    occupancy_pct = (occupied / effective_capacity) * 100 if effective_capacity > 0 else 0
    metric_date = max((rec["date"] for rec in latest_by_item.values()), default=datetime.now().date().isoformat())

    # Use location_id = 1 (Main Warehouse) as baseline metric location
    conn.execute("DELETE FROM warehouse_space_metrics")
    conn.execute(
        """
        INSERT INTO warehouse_space_metrics
          (location_id, metric_date, total_capacity_m3, used_capacity_m3, occupancy_pct, notes)
        VALUES (?, ?, ?, ?, ?, ?)
        """,
        (
            1,
            metric_date,
            effective_capacity,
            occupied,
            occupancy_pct,
            f"source=Data Workbook_sara.csv; net_storage_sq_ft={net_storage_sq_ft:.2f}; "
            f"theoretical={theoretical_cubic_capacity:.2f}; utilization_rate={UTILIZATION_RATE}",
        ),
    )


def load_staff_productivity(conn: sqlite3.Connection):
    by_employee = {}
    all_rows = []
    with STAFF_CSV.open("r", encoding="utf-8-sig", newline="") as f:
        reader = csv.DictReader(f)
        for row in reader:
            employee_id = (row.get("Employee_ID") or "").strip()
            if not employee_id:
                continue

            try:
                hours = float(row.get("Hours_Worked") or 0)
                overtime = float(row.get("Overtime_Hours") or 0)
                items_handled = float(row.get("Items_Handled") or 0)
            except ValueError:
                continue

            date_iso = parse_date(row.get("Date") or "")
            if not date_iso:
                continue

            total_hours = hours + overtime
            rate = (items_handled / total_hours) if total_hours > 0 else None
            all_rows.append((employee_id, date_iso, total_hours, items_handled, rate))

            agg = by_employee.setdefault(
                employee_id,
                {"hours": 0.0, "items": 0.0, "rate_sum": 0.0, "rate_count": 0},
            )
            agg["hours"] += total_hours
            agg["items"] += items_handled
            if rate is not None:
                agg["rate_sum"] += rate
                agg["rate_count"] += 1

    conn.execute("DELETE FROM labor_productivity_metrics")
    for employee_id, date_iso, total_hours, items_handled, rate in all_rows:
        productivity_index = rate if rate is not None else 0
        conn.execute(
            """
            INSERT INTO labor_productivity_metrics
              (location_id, metric_date, labor_hours, lines_processed, units_moved, productivity_index, notes)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (
                1,
                date_iso,
                total_hours,
                int(items_handled),
                items_handled,
                productivity_index,
                f"employee_id={employee_id}",
            ),
        )

    conn.execute("DELETE FROM warehouse_capacity_forecast")
    conn.execute("DELETE FROM supplier_scores WHERE grade='WAREHOUSE_LABOR_TMP'")
    for employee_id, agg in by_employee.items():
        weighted_rate = (agg["items"] / agg["hours"]) if agg["hours"] > 0 else 0
        avg_daily = (agg["rate_sum"] / agg["rate_count"]) if agg["rate_count"] > 0 else 0
        conn.execute(
            """
            INSERT INTO supplier_scores
              (supplier_name, score_date, quality_score, delivery_score, cost_score, risk_score, total_score, grade, notes)
            VALUES (?, ?, 0, 0, 0, 0, ?, 'WAREHOUSE_LABOR_TMP', ?)
            """,
            (
                employee_id,
                datetime.now().date().isoformat(),
                weighted_rate,
                f"total_hours={agg['hours']:.2f};items={agg['items']:.0f};avg_daily_rate={avg_daily:.4f}",
            ),
        )


def main():
    if not DB_PATH.exists():
        raise FileNotFoundError(f"Missing database: {DB_PATH}")
    if not INVENTORY_CSV.exists():
        raise FileNotFoundError(f"Missing inventory csv: {INVENTORY_CSV}")
    if not STAFF_CSV.exists():
        raise FileNotFoundError(f"Missing staff csv: {STAFF_CSV}")

    conn = sqlite3.connect(str(DB_PATH))
    try:
        load_inventory_space_metrics(conn)
        load_staff_productivity(conn)
        conn.commit()
    finally:
        conn.close()

    print("Warehouse module data loaded successfully.")


if __name__ == "__main__":
    main()

