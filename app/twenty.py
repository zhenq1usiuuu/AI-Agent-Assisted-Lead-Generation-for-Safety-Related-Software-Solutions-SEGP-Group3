"""Explicit, dry-run-first Twenty REST synchronization using workspace mappings."""
import argparse
import json
import os
from pathlib import Path
from uuid import uuid5, NAMESPACE_URL
from urllib.parse import urlsplit
import httpx
from dotenv import load_dotenv
from .context import ROOT
from .crm import validated_queue


def remote_id(kind, key):
    return str(uuid5(NAMESPACE_URL,'safety-signal-lab:'+kind+':'+key))


def build_records(review, mapping):
    """Map logical fields; users must verify destinations against their workspace API."""
    companies=validated_queue(review)
    records=[]
    def add(kind,key,values):
        cfg=mapping.get(kind)
        if not cfg or not cfg.get('resource') or not isinstance(cfg.get('fields'),dict):
            raise ValueError('Missing Twenty mapping: '+kind)
        resource=cfg['resource']
        if not resource.replace('_','').isalnum():raise ValueError('Invalid REST resource name.')
        missing=set(values)-set(cfg['fields'])
        if missing:raise ValueError('Missing mapped fields for '+kind+': '+','.join(sorted(missing)))
        payload={'id':remote_id(kind,key)}
        for field,value in values.items():
            target=cfg['fields'][field]
            if not isinstance(target,str) or not target or target=='id':raise ValueError('Invalid destination field.')
            parts=target.split('.')
            dest=payload
            for part in parts[:-1]:
                dest=dest.setdefault(part,{})
                if not isinstance(dest,dict):raise ValueError('Conflicting composite mapping.')
            if parts[-1] in dest:raise ValueError('Duplicate destination field.')
            dest[parts[-1]]=value
        records.append({'kind':kind,'resource':resource,'payload':payload})
    for c in companies:
        key=c['account_key'];company_id=remote_id('company',key)
        add('company',key,{'name':c['display_name'],'external_key':key})
        add('opportunity',c['idempotency_key'],{'name':c['display_name']+' safety research','company_id':company_id,'grade':c.get('grade'),'score':c.get('score')})
        for e in c['evidence']:
            add('signal',key+'|'+str(e.get('signal_id')),{'company_id':company_id,'source_url':e.get('source_url'),'excerpt':e.get('excerpt'),'published_at':e.get('published_at'), 'captured_at':e.get('captured_at'), 'content_hash':e.get('content_hash'), 'source_class':e.get('source_class'), 'source_title':e.get('source_title'), 'confidence':e.get('confidence')})
    for e in review['events']:
        if e['account_key'] not in {c['account_key'] for c in companies}:continue
        add('review',e['idempotency_key']+'|'+e['reviewed_at'],{'company_id':remote_id('company',e['account_key']),'reviewer':e['reviewer'],'decision':e['decision'],'reason':e.get('reason'),'reviewed_at':e['reviewed_at']})
    for c in review.get('contact_reviews',[]):
        # Unknown contact permission is carried through as do-not-contact, never outreach.
        kind='person' if c['kind']=='person' else 'contact_route'
        add(kind,c['contact_id'],{'company_id':remote_id('company',c['account_key']),
            'name':c.get('name'),'job_title':c.get('job_title'),'work_email':c.get('work_email'),'phone':c.get('phone'),
            'source_url':c['source_url'],'captured_at':c['captured_at'],'content_hash':c['content_hash'],
            'excerpt':c['excerpt'],'contact_permission':c['contact_permission'],
            'permission_timestamp':c.get('permission_timestamp'),'permission_evidence':c.get('permission_evidence'),
            'do_not_contact':c['do_not_contact'],'storage_basis':c['storage_basis']})
    return records


def synchronize(records, base, key, receipt_path, client=None):
    parsed=urlsplit(base)
    if parsed.scheme!='https' or not parsed.hostname or parsed.username or parsed.query or parsed.fragment:
        raise ValueError('TWENTY_BASE_URL must be an HTTPS origin without credentials/query.')
    if not key:raise ValueError('Set TWENTY_API_KEY in .env.')
    receipt={'status':'running','records':[]}
    own=client is None
    client=client or httpx.Client(timeout=30,trust_env=False,follow_redirects=False)
    def save():
        tmp=receipt_path.with_suffix('.tmp');tmp.write_text(json.dumps(receipt,indent=2),encoding='utf-8');tmp.replace(receipt_path)
    try:
        for record in records:
            payload=record['payload'];url=base.rstrip('/')+'/rest/'+record['resource']
            headers={'Authorization':'Bearer '+key}
            row={'kind':record['kind'],'id':payload['id'],'status':'attempting'}
            receipt['records'].append(row);save()
            response=client.get(url+'/'+payload['id'],headers=headers)
            if response.status_code==404:
                response=client.post(url,headers=headers,json=payload)
            elif response.status_code==200:
                response=client.patch(url+'/'+payload['id'],headers=headers,json={k:v for k,v in payload.items() if k!='id'})
            else:raise ValueError(f'Twenty lookup failed: HTTP {response.status_code}')
            if response.status_code not in (200,201):raise ValueError(f'Twenty write failed: HTTP {response.status_code}')
            # Standard workspace REST responses contain the record under data.
            def ids(value):
                if isinstance(value,dict):
                    yield value.get('id')
                    for child in value.values():yield from ids(child)
                elif isinstance(value,list):
                    for child in value:yield from ids(child)
            if payload['id'] not in set(ids(response.json())):
                raise ValueError('Twenty did not confirm the deterministic record ID; inspect the receipt before retrying.')
            row['status']='confirmed';save()
        receipt['status']='completed'
    except Exception:
        receipt['status']='failed_or_uncertain'
        raise
    finally:
        save()
        if own:client.close()
    return receipt


def main(argv=None):
    parser=argparse.ArgumentParser(description='Plan or explicitly apply approved records to a configured Twenty workspace.')
    parser.add_argument('review_json',type=Path)
    parser.add_argument('--mapping',type=Path,required=True)
    parser.add_argument('--out',type=Path,required=True,help='New export plan/receipt path; contains business data.')
    parser.add_argument('--apply',action='store_true',help='Actually send records; default is local dry-run.')
    args=parser.parse_args(argv);load_dotenv(ROOT/'.env')
    try:
        if args.out.exists():raise ValueError('Output file exists; choose a new filename.')
        review=json.loads(args.review_json.read_text(encoding='utf-8-sig'))
        # Reuse SQLite gate/evidence validation in an isolated temporary database.
        import tempfile
        from .crm import write_review
        with tempfile.TemporaryDirectory() as temp:
            write_review(review,Path(temp)/'validation.db')
        records=build_records(review,json.loads(args.mapping.read_text(encoding='utf-8-sig')))
        if args.apply:
            if json.loads(args.mapping.read_text(encoding='utf-8-sig')).get('workspace_verified') is not True:
                raise ValueError('Verify the mapping against your workspace schema and set workspace_verified=true first.')
            synchronize(records,os.getenv('TWENTY_BASE_URL',''),os.getenv('TWENTY_API_KEY',''),args.out)
        else:
            args.out.write_text(json.dumps({'mode':'dry_run','records':records},ensure_ascii=False,indent=2),encoding='utf-8')
        print(f'Saved Twenty output: {args.out}')
        return 0
    except (ValueError,OSError,httpx.HTTPError) as exc:
        print(f'Twenty synchronization failed: {type(exc).__name__}. Check local mapping, credentials and receipt.');return 1


if __name__=='__main__':raise SystemExit(main())
