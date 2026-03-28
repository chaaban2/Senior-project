import sqlite3
from datetime import date, timedelta
from pathlib import Path

DB_PATH = Path(r"C:\Users\alsen\Desktop\dss_dashboard_project\dss_inventory_demo.db")
START_DATE = date(2025, 9, 1)
END_DATE = date(2025, 9, 26)


def stable_int(a: int, b: int, c: int) -> int:
    # Deterministic pseudo-random number, stable across runs
    return ((a * 73856093) ^ (b * 19349663) ^ (c * 83492791)) & 0x7FFFFFFF


def day_in_window(seed: int, start: date, end: date) -> str:
    span = (end - start).days + 1
    return (start + timedelta(days=seed % span)).isoformat()


def ensure_event_coverage(conn: sqlite3.Connection) -> tuple[int, int]:
    cur = conn.cursor()
    items = cur.execute("SELECT item_id, unit_cost_aed FROM items ORDER BY item_id").fetchall()
    locations = cur.execute("SELECT location_id FROM locations ORDER BY location_id").fetchall()
    user_row = cur.execute(
        "SELECT user_id FROM users WHERE role_id = 2 ORDER BY user_id LIMIT 1"
    ).fetchone()
    if not user_row:
        user_row = cur.execute("SELECT user_id FROM users ORDER BY user_id LIMIT 1").fetchone()
    if not user_row:
        raise RuntimeError("No users found for performed_by_user_id")
    performed_by = int(user_row[0])

    receipt_start = START_DATE
    receipt_end = date(2025, 9, 10)
    issue_start = date(2025, 9, 11)
    issue_end = END_DATE

    inserted_receipts = 0
    inserted_issues = 0

    for item_id, unit_cost in items:
        for (location_id,) in locations:
            has_receipt = cur.execute(
                """
                SELECT 1
                FROM inventory_events
                WHERE item_id = ?
                  AND location_id = ?
                  AND event_type = 'RECEIPT'
                  AND date(event_ts) BETWEEN date(?) AND date(?)
                LIMIT 1
                """,
                (item_id, location_id, receipt_start.isoformat(), receipt_end.isoformat()),
            ).fetchone()

            has_issue = cur.execute(
                """
                SELECT 1
                FROM inventory_events
                WHERE item_id = ?
                  AND location_id = ?
                  AND event_type = 'ISSUE'
                  AND date(event_ts) BETWEEN date(?) AND date(?)
                LIMIT 1
                """,
                (item_id, location_id, issue_start.isoformat(), issue_end.isoformat()),
            ).fetchone()

            base = stable_int(item_id, location_id, 1)
            receipt_qty = 10 + (base % 21)  # 10..30
            issue_qty = 1 + (stable_int(item_id, location_id, 2) % 10)  # 1..10
            if issue_qty >= receipt_qty:
                issue_qty = max(1, receipt_qty - 1)

            if not has_receipt:
                receipt_date = day_in_window(stable_int(item_id, location_id, 3), receipt_start, receipt_end)
                cur.execute(
                    """
                    INSERT INTO inventory_events
                      (event_ts, event_type, item_id, location_id, performed_by_user_id, quantity, unit_cost_aed, notes)
                    VALUES (?, 'RECEIPT', ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        f"{receipt_date}T09:00:00",
                        item_id,
                        location_id,
                        performed_by,
                        receipt_qty,
                        float(unit_cost),
                        "seed for ROP coverage",
                    ),
                )
                inserted_receipts += 1

            if not has_issue:
                issue_date = day_in_window(stable_int(item_id, location_id, 4), issue_start, issue_end)
                cur.execute(
                    """
                    INSERT INTO inventory_events
                      (event_ts, event_type, item_id, location_id, performed_by_user_id, quantity, unit_cost_aed, notes)
                    VALUES (?, 'ISSUE', ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        f"{issue_date}T14:00:00",
                        item_id,
                        location_id,
                        performed_by,
                        -issue_qty,
                        float(unit_cost),
                        "seed for ROP coverage",
                    ),
                )
                inserted_issues += 1

    return inserted_receipts, inserted_issues


def ensure_default_parameters(conn: sqlite3.Connection) -> tuple[int, int]:
    cur = conn.cursor()
    items = [r[0] for r in cur.execute("SELECT item_id FROM items ORDER BY item_id").fetchall()]
    locations = [r[0] for r in cur.execute("SELECT location_id FROM locations ORDER BY location_id").fetchall()]

    rop_added = 0
    safety_added = 0

    for item_id in items:
        for location_id in locations:
            has_rop = cur.execute(
                """
                SELECT 1 FROM rop_parameters
                WHERE item_id = ? AND location_id = ?
                LIMIT 1
                """,
                (item_id, location_id),
            ).fetchone()
            if not has_rop:
                cur.execute(
                    """
                    INSERT INTO rop_parameters
                      (item_id, location_id, daily_demand_avg, lead_time_days, safety_stock_qty, review_date, notes)
                    VALUES (?, ?, 0, 7, 0, '2025-09-26', 'seed default')
                    """,
                    (item_id, location_id),
                )
                rop_added += 1

            has_safety = cur.execute(
                """
                SELECT 1 FROM safety_stock_parameters
                WHERE item_id = ? AND location_id = ?
                LIMIT 1
                """,
                (item_id, location_id),
            ).fetchone()
            if not has_safety:
                cur.execute(
                    """
                    INSERT INTO safety_stock_parameters
                      (item_id, location_id, demand_std_dev, lead_time_days, service_level_z, review_date, notes)
                    VALUES (?, ?, 0, 7, 1.65, '2025-09-26', 'seed default')
                    """,
                    (item_id, location_id),
                )
                safety_added += 1

    return rop_added, safety_added


def verify_pair_coverage(conn: sqlite3.Connection) -> tuple[int, int]:
    cur = conn.cursor()
    distinct_pairs = cur.execute(
        """
        SELECT COUNT(*) FROM (
          SELECT DISTINCT item_id, location_id
          FROM inventory_events
          WHERE date(event_ts) BETWEEN date('2025-09-01') AND date('2025-09-26')
        ) x
        """
    ).fetchone()[0]
    expected_pairs = cur.execute("SELECT COUNT(*) FROM items").fetchone()[0] * cur.execute(
        "SELECT COUNT(*) FROM locations"
    ).fetchone()[0]
    return int(distinct_pairs), int(expected_pairs)


def main():
    if not DB_PATH.exists():
        raise FileNotFoundError(f"Missing DB file: {DB_PATH}")
    conn = sqlite3.connect(str(DB_PATH))
    try:
        receipts, issues = ensure_event_coverage(conn)
        rop_added, safety_added = ensure_default_parameters(conn)
        conn.commit()
        distinct_pairs, expected_pairs = verify_pair_coverage(conn)
    finally:
        conn.close()

    print("Seed complete.")
    print(f"Inserted RECEIPT events: {receipts}")
    print(f"Inserted ISSUE events: {issues}")
    print(f"Added rop_parameters defaults: {rop_added}")
    print(f"Added safety_stock_parameters defaults: {safety_added}")
    print(f"Distinct (item_id, location_id) pairs in range: {distinct_pairs}")
    print(f"Expected pairs (items x locations): {expected_pairs}")


if __name__ == "__main__":
    main()

