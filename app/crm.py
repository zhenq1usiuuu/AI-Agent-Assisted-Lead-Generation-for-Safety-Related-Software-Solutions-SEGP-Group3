"""
Step 7: Write approved leads from crm_queue into a local SQLite CRM database.

Usage:
    signal-crm  review.json
    signal-crm  review.json --db data/crm.db
    python -m app.crm review.json
"""
from __future__ import annotations

import argparse
import json
import sqlite3
from pathlib import Path

DB_DEFAULT = Path("data/crm.db")


def get_connection(db_path: Path) -> sqlite3.Connection:
    """Open (or create) the SQLite database and ensure the leads table exists."""
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(db_path)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS leads (
            idempotency_key   TEXT PRIMARY KEY,
            account_key       TEXT,
            display_name      TEXT,
            grade             TEXT,
            score             INTEGER,
            target_buyer_role TEXT,
            reviewed_by       TEXT,
            reviewed_at       TEXT,
            review_reason     TEXT,
            ai_reason         TEXT,
            evidence_json     TEXT
        )
    """)
    conn.commit()
    return conn


def write_crm_queue(queue: list[dict], db_path: Path = DB_DEFAULT) -> tuple[int, int]:
    """
    Insert approved leads into the database.
    Skips duplicates using idempotency_key (safe to run twice on the same file).
    Returns (written, skipped) counts.
    """
    conn = get_connection(db_path)
    written, skipped = 0, 0
    for item in queue:
        try:
            conn.execute(
                """INSERT INTO leads VALUES (?,?,?,?,?,?,?,?,?,?,?)""",
                (
                    item["idempotency_key"],
                    item["account_key"],
                    item["display_name"],
                    item.get("grade"),
                    item.get("score"),
                    item.get("target_buyer_role"),
                    item.get("reviewed_by"),
                    item.get("reviewed_at"),
                    item.get("review_reason"),
                    item.get("ai_reason"),
                    json.dumps(item.get("evidence", []), ensure_ascii=False),
                ),
            )
            written += 1
        except sqlite3.IntegrityError:
            skipped += 1  # already in DB — idempotent, safe to ignore
    conn.commit()
    conn.close()
    return written, skipped


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(
        description="Write approved leads from a review JSON into the CRM database."
    )
    ap.add_argument("review_json", type=Path, help="Output file from signal-review.")
    ap.add_argument(
        "--db",
        type=Path,
        default=DB_DEFAULT,
        help=f"SQLite database path (default: {DB_DEFAULT}).",
    )
    args = ap.parse_args(argv)

    try:
        data = json.loads(args.review_json.read_bytes().decode("utf-8-sig"))
    except (OSError, json.JSONDecodeError) as exc:
        print(f"Error reading {args.review_json}: {exc}")
        return 1

    queue = data.get("crm_queue", [])
    if not queue:
        print("No items in crm_queue — nothing to write. Have you run signal-review yet?")
        return 0

    written, skipped = write_crm_queue(queue, args.db)
    print(
        f"Done. {written} new lead(s) written to {args.db.resolve()}"
        + (f", {skipped} duplicate(s) skipped." if skipped else ".")
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
