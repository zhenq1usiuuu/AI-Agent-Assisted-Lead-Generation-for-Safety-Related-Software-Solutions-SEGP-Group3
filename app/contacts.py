"""Evidence-backed optional contact discovery and separate human approval."""
import hashlib
import re
from datetime import datetime, timezone
from typing import Literal
from urllib.parse import urlsplit
from pydantic import BaseModel, ConfigDict, Field
from .tools import PublicFetcher, clean_html, clean_pdf


def now(): return datetime.now(timezone.utc).isoformat()


class Contact(BaseModel):
    model_config = ConfigDict(extra='forbid')
    account_key: str = Field(min_length=1)
    kind: Literal['person', 'organisation_route']
    name: str | None = None
    job_title: str | None = None
    work_email: str | None = None
    phone: str | None = None
    source_url: str
    captured_at: str
    source_text: str = Field(max_length=180000)
    excerpt: str = Field(max_length=3000)
    company_excerpt: str = Field(max_length=180000)


def validate_contact(raw, company_name):
    c = Contact.model_validate(raw).model_dump()
    datetime.fromisoformat(c['captured_at'].replace('Z','+00:00'))
    if urlsplit(c['source_url']).scheme != 'https':
        raise ValueError('Contact evidence requires an HTTPS source URL.')
    if not c['excerpt'] or c['excerpt'] not in c['source_text']:
        raise ValueError('Contact quote is not present in source text.')
    if not c['company_excerpt'] or c['company_excerpt'] not in c['source_text'] or company_name.casefold() not in c['company_excerpt'].casefold():
        raise ValueError('Source must explicitly associate this company with the contact page.')
    if c['kind']=='person' and not c['name']:
        raise ValueError('A person requires a sourced name.')
    if c['kind']=='organisation_route' and (c['name'] or c['job_title']):
        raise ValueError('Company contact routes must not invent an individual.')
    for field in ('name','job_title','work_email','phone'):
        if c[field] and c[field] not in c['excerpt']:
            raise ValueError('Contact field lacks verbatim quote support: '+field)
    if not any(c[k] for k in ('work_email','phone')):
        raise ValueError('Contact has no communication route.')
    if c['work_email'] and not re.fullmatch(r'[^\s@]+@[^\s@]+\.[^\s@]+',c['work_email']):
        raise ValueError('Invalid work email format.')
    if c['phone'] and not 7 <= len(re.sub(r'\D','',c['phone'])) <= 15:
        raise ValueError('Invalid phone format.')
    identity='|'.join(str(c.get(k) or '').casefold() for k in ('account_key','kind','name','work_email','phone'))
    c['contact_id']=hashlib.sha256(identity.encode()).hexdigest()
    c['content_hash']=hashlib.sha256(c['source_text'].encode()).hexdigest()
    return c


async def discover_contacts(manifest, companies):
    """Only explicit operator-provided public URLs. No guessed domains or emails."""
    names={c['account_key']:c['display_name'] for c in companies}
    accepted=[];errors=[]
    sources=manifest.get('sources',[])
    if len(sources)>8 or len(manifest.get('contacts',[]))>50:
        raise ValueError('Contact input exceeds 8 pages or 50 supplied contacts.')
    for raw in manifest.get('contacts',[]):
        try:
            accepted.append(validate_contact(raw,names[raw['account_key']]))
        except (ValueError,KeyError) as exc:
            errors.append({'account_key':raw.get('account_key'),'message':str(exc)})
    for source in sources:
        fetcher=None
        try:
            key=source['account_key'];name=names[key];url=source['url']
            if urlsplit(url).scheme!='https':raise ValueError('HTTPS required.')
            fetcher=PublicFetcher([urlsplit(url).hostname],True)
            raw=await fetcher.fetch_public_page(url)
            doc=clean_pdf(raw['pdf_base64'],raw['url']) if raw.get('format')=='pdf' else clean_html(raw['html'],raw['url'])
            text=doc['text']
            # Automatically collect only general company inboxes; personal addresses
            # require a named, separately sourced contact in the input manifest.
            name_at=text.casefold().find(name.casefold())
            company_quote=text[max(0,name_at-100):name_at+len(name)+100] if name_at>=0 else ''
            emails=re.findall(r'\b(?:info|contact|sales|enquiry|enquiries|office|admin|procurement|hse|ehs)@[A-Za-z0-9.-]+\.[A-Za-z]{2,}\b',text,re.I)
            for email in dict.fromkeys(emails):
                at=text.index(email);excerpt=text[max(0,at-150):at+len(email)+150]
                c=dict(account_key=key,kind='organisation_route',work_email=email,
                       source_url=raw['url'],captured_at=raw['captured_at'],source_text=text,
                       excerpt=excerpt,company_excerpt=company_quote)
                accepted.append(validate_contact(c,name))
            if not emails:errors.append({'account_key':key,'message':'No general business email found; no contact inferred.'})
        except (ValueError,KeyError) as exc:
            errors.append({'account_key':source.get('account_key'),'message':str(exc)})
        finally:
            if fetcher:await fetcher.close()
    return {'candidates':list({c['contact_id']:c for c in accepted}.values()),'errors':errors}


def review_contacts(discovery, reviewer, ask=input, say=print):
    records=[]
    for c in discovery['candidates']:
        say(f"Contact for {c['account_key']}: {c.get('name') or 'Company contact route'} | {c.get('job_title') or ''} | {c.get('work_email') or ''} | {c.get('phone') or ''}")
        say(f"Source: {c['source_url']}\nEvidence: {c['excerpt']}")
        if ask('Confirm company association and approve storing this contact? [y/N]: ').strip().lower()!='y':continue
        basis=ask('Reason/source establishing permission to store this business contact (blank = skip): ').strip()
        if not basis:continue
        permission=ask('Contact permission [unknown/consented/denied, default unknown]: ').strip().lower() or 'unknown'
        if permission not in ('unknown','consented','denied'):permission='unknown'
        permission_evidence=ask('Consent evidence/reference (blank = unknown): ').strip() if permission=='consented' else ''
        if permission=='consented' and not permission_evidence:permission='unknown'
        records.append({**c,'decision':'approve','reviewed_by':reviewer,'reviewed_at':now(),
                        'storage_basis':basis,'contact_permission':permission,
                        'permission_timestamp':now() if permission!='unknown' else None,
                        'permission_evidence':permission_evidence or None,
                        'do_not_contact':permission!='consented'})
    return records
