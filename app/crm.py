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


def write_crm_queue(queue, db_path=DB_DEFAULT, contacts=None):
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
        conn.execute('PRAGMA foreign_keys=ON')
        conn.execute('CREATE TABLE IF NOT EXISTS companies (account_key TEXT PRIMARY KEY, display_name TEXT NOT NULL)')
        conn.execute('CREATE TABLE IF NOT EXISTS contacts (contact_id TEXT PRIMARY KEY, account_key TEXT NOT NULL REFERENCES companies(account_key), kind TEXT NOT NULL, name TEXT, job_title TEXT, work_email TEXT, phone TEXT, contact_permission TEXT NOT NULL, permission_timestamp TEXT, do_not_contact INTEGER NOT NULL, reviewed_by TEXT NOT NULL, reviewed_at TEXT NOT NULL, storage_basis TEXT NOT NULL, permission_evidence TEXT)')
        conn.execute('CREATE TABLE IF NOT EXISTS contact_evidence (contact_id TEXT NOT NULL REFERENCES contacts(contact_id), content_hash TEXT NOT NULL, source_url TEXT NOT NULL, captured_at TEXT NOT NULL, excerpt TEXT NOT NULL, company_excerpt TEXT NOT NULL, PRIMARY KEY(contact_id,content_hash,source_url))')
        conn.execute('CREATE TABLE IF NOT EXISTS contact_review_events (contact_id TEXT NOT NULL REFERENCES contacts(contact_id), reviewed_at TEXT NOT NULL, record_json TEXT NOT NULL, PRIMARY KEY(contact_id,reviewed_at))')
        with conn:
            for item in queue:
                conn.execute('INSERT INTO companies VALUES (?,?) ON CONFLICT(account_key) DO UPDATE SET display_name=excluded.display_name', (item['account_key'],item['display_name']))
            for row in rows:
                existing = conn.execute('SELECT * FROM leads WHERE idempotency_key=?', (row[0],)).fetchone()
                if existing is not None:
                    if tuple(existing) != row:
                        raise ValueError('Existing CRM record has different reviewed content; resolve the conflict before retrying.')
                    skipped += 1
                    continue
                conn.execute('INSERT INTO leads (idempotency_key,account_key,display_name,grade,score,target_buyer_role,reviewed_by,reviewed_at,review_reason,ai_reason,evidence_json) VALUES (?,?,?,?,?,?,?,?,?,?,?)', row)
                written += 1
            for c in contacts or []:
                fields=('contact_id','account_key','kind','name','job_title','work_email','phone','contact_permission','permission_timestamp','do_not_contact','reviewed_by','reviewed_at','storage_basis','permission_evidence')
                old=conn.execute('SELECT reviewed_at FROM contacts WHERE contact_id=?',(c['contact_id'],)).fetchone()
                if old and old[0]>c['reviewed_at']:
                    raise ValueError('Contact review is older than the stored decision.')
                conn.execute('INSERT INTO contacts ('+','.join(fields)+') VALUES ('+','.join('?' for _ in fields)+') ON CONFLICT(contact_id) DO UPDATE SET '+','.join(f'{f}=excluded.{f}' for f in fields[1:]),tuple(c.get(f) for f in fields))
                conn.execute('INSERT OR IGNORE INTO contact_evidence VALUES (?,?,?,?,?,?)',tuple(c[k] for k in ('contact_id','content_hash','source_url','captured_at','excerpt','company_excerpt')))
                audit={k:v for k,v in c.items() if k!='source_text'}
                conn.execute('INSERT OR IGNORE INTO contact_review_events VALUES (?,?,?)',(c['contact_id'],c['reviewed_at'],json.dumps(audit,ensure_ascii=False)))
        return written, skipped
    finally:
        conn.close()


def write_review(review, db_path=DB_DEFAULT):
    from .contacts import Contact, validate_contact
    queue=validated_queue(review)
    names={c['account_key']:c['display_name'] for c in queue}
    contacts=review.get('contact_reviews',[])
    for c in contacts:
        if c.get('account_key') not in names or c.get('decision')!='approve' or not c.get('storage_basis') or not c.get('reviewed_by') or not c.get('reviewed_at'):
            raise ValueError('Contact requires separate approval and an approved linked company.')
        checked=validate_contact({k:c.get(k) for k in Contact.model_fields},names[c['account_key']])
        if any(c.get(k)!=checked[k] for k in ('contact_id','content_hash')):
            raise ValueError('Contact evidence identity does not match.')
        permission=c.get('contact_permission')
        if permission not in ('unknown','consented','denied') or not isinstance(c.get('do_not_contact'),bool):
            raise ValueError('Invalid contact permission state.')
        if permission!='consented' and c['do_not_contact'] is not True:
            raise ValueError('Unknown or denied contacts must remain do-not-contact.')
        if permission=='consented' and (not c.get('permission_evidence') or not c.get('permission_timestamp')):
            raise ValueError('Consent requires an evidence reference and timestamp.')
    return write_crm_queue(queue, db_path, contacts)


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
