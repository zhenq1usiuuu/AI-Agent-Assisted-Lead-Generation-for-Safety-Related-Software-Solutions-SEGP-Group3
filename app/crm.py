"""Transactional local SQLite CRM writer for completed human reviews."""
import argparse
import json
import sqlite3
from pathlib import Path

DB_DEFAULT = Path('data/crm.db')


def validated_queue(review):
    if review.get('complete') is not True:
        raise ValueError('Finish human review before writing to CRM.')
    events, queue = review.get('events'), review.get('crm_queue')
    if not isinstance(events, list) or not isinstance(queue, list):
        raise ValueError('Expected review events and crm_queue arrays.')
    approved = {}
    for event in events:
        key = event.get('idempotency_key')
        if key in approved:
            raise ValueError('Duplicate review event key.')
        approved[key] = event
    seen = set()
    for item in queue:
        key = item.get('idempotency_key')
        event = approved.get(key, {})
        if not key or key in seen or event.get('decision') not in ('approve', 'edit'):
            raise ValueError('CRM item lacks a unique approve/edit decision.')
        seen.add(key)
        for field in ('account_key', 'evidence', 'grade', 'score'):
            if item.get(field) != event.get(field):
                raise ValueError('CRM item differs from reviewed ' + field)
        if not item.get('reviewed_by') or item['reviewed_by'] != event.get('reviewer'):
            raise ValueError('CRM reviewer does not match.')
        if item.get('reviewed_at') != event.get('reviewed_at'):
            raise ValueError('CRM review timestamp does not match.')
        if item.get('review_reason') != event.get('reason'):
            raise ValueError('CRM reason differs from the review decision.')
        if item.get('ai_reason') and event.get('ai_validation') != 'valid':
            raise ValueError('Unvalidated AI explanation cannot be written to CRM.')
        role = event.get('edits', {}).get('buyer_role')
        if role and item.get('target_buyer_role') != role:
            raise ValueError('CRM buyer role differs from approved edits.')
        name = event.get('edits', {}).get('display_name') or event.get('display_name')
        if not name or item.get('display_name') != name:
            raise ValueError('CRM display name differs from approved edits.')
    return queue


def write_crm_queue(queue, db_path=DB_DEFAULT):
    """Internal writer; external callers should use write_review for gate checks."""
    rows = []
    for item in queue:
        if any(not item.get(k) for k in ('idempotency_key','account_key','display_name','reviewed_by','reviewed_at')):
            raise ValueError('Missing CRM identity/review fields.')
        rows.append((item['idempotency_key'], item['account_key'], item['display_name'],
                     item.get('grade'), item.get('score'), item.get('target_buyer_role'),
                     item['reviewed_by'], item['reviewed_at'], item.get('review_reason'),
                     item.get('ai_reason'), json.dumps(item.get('evidence', []), ensure_ascii=False, sort_keys=True)))
    db_path = Path(db_path)
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(db_path)
    written, skipped = 0, 0
    try:
        conn.execute('CREATE TABLE IF NOT EXISTS leads (idempotency_key TEXT PRIMARY KEY, account_key TEXT, display_name TEXT, grade TEXT, score INTEGER, target_buyer_role TEXT, reviewed_by TEXT, reviewed_at TEXT, review_reason TEXT, ai_reason TEXT, evidence_json TEXT)')
        with conn:
            for row in rows:
                existing = conn.execute('SELECT * FROM leads WHERE idempotency_key=?', (row[0],)).fetchone()
                if existing is not None:
                    if tuple(existing) != row:
                        raise ValueError('Existing CRM record has different reviewed content; resolve the conflict before retrying.')
                    skipped += 1
                    continue
                conn.execute('INSERT INTO leads (idempotency_key,account_key,display_name,grade,score,target_buyer_role,reviewed_by,reviewed_at,review_reason,ai_reason,evidence_json) VALUES (?,?,?,?,?,?,?,?,?,?,?)', row)
                written += 1
        return written, skipped
    finally:
        conn.close()


def write_review(review, db_path=DB_DEFAULT):
    return write_crm_queue(validated_queue(review), db_path)


def main(argv=None):
    parser = argparse.ArgumentParser(description='Write approved review records to local SQLite CRM.')
    parser.add_argument('review_json', type=Path)
    parser.add_argument('--db', type=Path, default=DB_DEFAULT)
    args = parser.parse_args(argv)
    try:
        review = json.loads(args.review_json.read_text(encoding='utf-8-sig'))
        written, skipped = write_review(review, args.db)
        print(json.dumps({'written':written,'skipped':skipped,'database':str(args.db.resolve())}))
        return 0
    except (ValueError, OSError, sqlite3.Error, TypeError, AttributeError) as exc:
        print(f'CRM write failed: {exc}')
        return 1


if __name__ == '__main__':
    raise SystemExit(main())
