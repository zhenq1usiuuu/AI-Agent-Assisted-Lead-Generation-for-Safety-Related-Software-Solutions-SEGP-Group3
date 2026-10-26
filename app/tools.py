"""Deterministic, bounded public-page tools. No LLM is used here."""
import asyncio
import base64
import hashlib
import io
import ipaddress
import json
import re
import socket
import time
from datetime import datetime, timezone
from urllib.parse import urljoin, urlsplit, urlunsplit, unquote
from urllib.robotparser import RobotFileParser
import httpx
from bs4 import BeautifulSoup

USER_AGENT = 'SafetyResearchBot/0.1'
MAX_BYTES = 2_000_000

def definition(name, description, properties, required):
    return {'type':'function','function':{'name':name,'description':description,
        'parameters':{'type':'object','properties':properties,'required':required,'additionalProperties':False}}}

TOOLS = [
    definition('fetch_public_page','Fetch a permission-confirmed, robots-allowed public HTML/text or PDF document. Exact-host allowlist, pinned public IP, per-request delay, redirect checks and 2 MB cap enforced. Returns raw HTML or PDF base64 internally and audit metadata. Does not submit forms or read embedded dashboards.',
               {'url':{'type':'string'}}, ['url']),
    definition('clean_html','Extract article/main content, remove scripts/navigation/form controls, preserve body text in wrapper forms, and collect up to 1000 deduplicated same-host links with truncation and quality metadata; no LLM.',
               {'html':{'type':'string'},'url':{'type':'string'}}, ['html','url']),
    definition('clean_pdf','Locally extract at most 30 PDF pages and 180000 text characters with page spans, table context and coordinates. Optional local OCR preserves method/confidence warnings. No LLM; table associations need review.',
               {'pdf_base64':{'type':'string'},'url':{'type':'string'},'ocr':{'type':'boolean'}}, ['pdf_base64','url']),
    definition('discover_links','Rank public link labels and URL paths using word/phrase boundaries, excluding navigation and non-workplace inspection topics; ignore hostnames and query strings. The runner applies depth/page/link budgets; not global web search.',
               {'links':{'type':'array','items':{'type':'object'}},'keywords':{'type':'array','items':{'type':'string'}},'limit':{'type':'integer','minimum':0,'maximum':32}}, ['links','keywords','limit']),
    definition('verify_evidence','Validate exact continuous excerpts and organisation mentions against a supplied clean document. Returns supported signals with provenance and rejected signals; never performs lead scoring.',
               {'signals':{'type':'array','items':{'type':'object'}},'document':{'type':'object'}}, ['signals','document'])
]
TOOLS.append(definition('inspect_public_source',
    'Inspect public SIRIM GET query forms and DOSH same-host embedded/download links. No guessed APIs, hidden/session fields, POST, login or cross-host access. Returns observed targets and connector limitations.',
    {'html':{'type':'string'},'url':{'type':'string'},'source_id':{'type':['string','null']},'query':{'type':['string','null']}},
    ['html','url','source_id','query']))

def canonical_url(url):
    p = urlsplit(url.strip())
    if p.scheme not in ('https','http') or not p.hostname or p.username or p.password:
        raise ValueError('Only public http(s) URLs without credentials are accepted')
    if p.port not in (None,80,443):
        raise ValueError('Non-standard ports are blocked')
    if len(url) > 2000:
        raise ValueError('URL too long')
    return urlunsplit((p.scheme,p.netloc.lower(),p.path or '/',p.query,''))

async def public_ip(host):
    rows = await asyncio.to_thread(socket.getaddrinfo, host, None, 0, socket.SOCK_STREAM)
    addresses = list(dict.fromkeys(row[4][0] for row in rows))
    if not addresses or any(not ipaddress.ip_address(a).is_global for a in addresses):
        raise ValueError('Private, local, reserved or mixed DNS targets are blocked')
    return addresses[0]

def clean_html(html, url):
    soup = BeautifulSoup(html, 'html.parser')
    title = soup.title.get_text(' ',strip=True) if soup.title else ''
    language = soup.html.get('lang') if soup.html else None
    date = None
    for attrs in ({'property':'article:published_time'},{'name':'date'}):
        tag=soup.find('meta',attrs=attrs)
        if tag and tag.get('content'):
            date={'value':tag['content'],'origin':str(attrs),'verified':False}
            break
    if not date:
        tag=soup.find('time',datetime=True)
        if tag: date={'value':tag['datetime'],'origin':'time[datetime]','verified':False}
    modified=soup.find('meta',attrs={'property':'article:modified_time'})
    updated_at=modified.get('content') if modified else None
    for tag in soup(['script','style','nav','footer','noscript','svg','aside','template',
                     'input','select','textarea','button']):
        tag.decompose()
    for tag in soup.select('[role="navigation"], [role="contentinfo"], .daterangepicker, .daterangepicker-visibility-hidden, #mainFooter, #mainHeader, .offcanvas'):
        tag.decompose()
    # ASP.NET sites may wrap the entire article in a form. Remove controls, not evidence.
    for tag in soup.find_all('form'):
        tag.unwrap()
    root = None
    for selector in ('#page-content', '.entry-content', 'article', '[role="main"]', 'main'):
        root = soup.select_one(selector)
        if root is not None:
            break
    if root is None:
        root = soup.body or soup
        for tag in root.find_all('header'):
            tag.decompose()
    headings=[h.get_text(' ',strip=True) for h in root.find_all(['h1','h2','h3'])][:40]
    links=[]
    seen_links=set()
    for a in root.find_all('a',href=True):
        try:
            target=canonical_url(urljoin(url,a['href']))
            if urlsplit(target).hostname == urlsplit(url).hostname and target not in seen_links:
                links.append({'url':target,'label':a.get_text(' ',strip=True)[:200]})
                seen_links.add(target)
        except ValueError:
            pass
    text=re.sub(r'\n{3,}','\n\n',root.get_text('\n',strip=True))
    text=re.sub(r'[^\S\n]+',' ',text).strip()
    warnings=[]
    if re.search(r'\btable not found\b', text, re.I):
        warnings.append('missing_record_table')
    if len(text)<40:
        warnings.append('insufficient_body_text')
    return {'title':title,'language':language,'published_at':date['value'] if date else None,
            'updated_at':updated_at,'date_evidence':date,'headings':headings,'text':text[:180000],
            'text_truncated':len(text)>180000,'links':links[:1000],
            'link_count':len(links),'links_truncated':len(links)>1000,'quality_warnings':warnings}

def discover_links(links, keywords, limit):
    def normalized(value):
        return re.sub(r'[^\w]+', ' ', unquote(value).lower()).strip()
    def contains(hay, phrase):
        phrase=normalized(phrase)
        return bool(phrase) and bool(re.search(r'(?<!\w)' + re.escape(phrase) + r'(?!\w)', hay))
    effective_keywords=list(keywords)
    safety_terms=('workplace safety','occupational safety','safety inspection','safety inspections','safety audit','OSH','HSE','corrective action')
    if any(contains(normalized(k), term) for k in keywords for term in safety_terms):
        # Public Malaysian articles often use acronyms or Malay labels instead of the plan's English phrase.
        effective_keywords=list(dict.fromkeys([*keywords, 'occupational safety', 'OSH', 'HSE',
                                              'keselamatan pekerjaan', 'keselamatan dan kesihatan pekerjaan']))
    def score(link):
        # A keyword such as OSH must not match every URL on dosh.gov.my.
        hay=normalized(urlsplit(link['url']).path+' '+link.get('label',''))
        if any(contains(hay, term) for term in ('privacy', 'disclaimer', 'sitemap', 'login', 'register',
                                               'dasar privasi', 'dasar keselamatan', 'penafian', 'peta laman', 'terms and conditions')):
            return 0
        workplace=any(contains(hay, term) for term in ('occupational', 'workplace', 'worker safety', 'OSH', 'HSE'))
        if not workplace and any(contains(hay, term) for term in ('quarantine', 'customs', 'export of fish', 'food quality', 'product inspection', 'kuarantin')):
            return 0
        return sum((3 if len(normalized(k).split())>1 else 1) for k in effective_keywords if contains(hay,k))
    limit=max(0,min(32,limit))
    ranked=sorted(links,key=score,reverse=True)
    return list(dict.fromkeys(l['url'] for l in ranked if score(l)>0))[:limit]


def clean_pdf(pdf_base64, url, ocr=False):
    from .pdf_extract import extract_pdf
    return extract_pdf(pdf_base64, url, ocr=ocr)


def verify_evidence(signals, document):
    accepted,rejected=[],[]
    for signal in signals:
        reason=None
        if document.get('evidence_scope') == 'context_only':
            reason='context_page_is_not_company_evidence'
        elif signal['document_id'] != document['document_id']:
            reason='unknown_document'
        elif signal['excerpt'] not in document['text']:
            reason='excerpt_not_exact'
        elif document.get('quote_span') and signal['excerpt'] not in document['text'][document['quote_span']['start']:document['quote_span']['end']]:
            reason='quote_outside_target_chunk'
        elif signal['organisation'] and signal['organisation'] not in signal['excerpt']:
            reason='organisation_not_in_cited_excerpt'
        elif signal['organisation_specific'] != bool(signal['organisation']):
            reason='organisation_specific_mismatch'
        elif signal['organisation'] and document['source_class']=='regulatory' and any(
            s in signal['organisation'].lower() for s in ['dosh','jabatan keselamatan','department of occupational']):
            reason='regulator_is_not_buyer'
        if reason:
            rejected.append({'document_id':signal['document_id'],'reason':reason})
            continue
        span=document.get('quote_span',{'start':0,'end':len(document['text'])})
        start=document['text'].index(signal['excerpt'],span['start'],span['end'])
        accepted.append({**signal,'source_url':document['url'],'source_class':document['source_class'],
            'source_requested_url':document.get('requested_url',document['url']),
            'evidence_level':'discovery_only' if signal.get('trigger_type') in ('esg_index','certification') else 'requires_semantic_review',
            'source_id':document.get('source_id'), 'evidence_scope':document.get('evidence_scope', 'records'),
            'source_title':document['title'],'published_at':document['published_at'],
            'captured_at':document['captured_at'],'content_hash':document['content_hash'],
            'evidence_start':start,'evidence_end':start+len(signal['excerpt']),
            'evidence_pages':[p['page'] for p in document.get('page_spans', [])
                              if p['start'] < start+len(signal['excerpt']) and p['end'] > start],
            'evidence_text_method': 'local_ocr' if any(p.get('method')=='local_ocr' and p['start'] < start+len(signal['excerpt']) and p['end'] > start for p in document.get('page_spans',[])) else 'source_text',
            'confidence_kind':'uncalibrated_model_estimate','requires_human_review':True})
    return {'accepted':accepted,'rejected':rejected}

class PublicFetcher:
    def __init__(self, allowed_hosts, permissions_confirmed):
        self.allowed_hosts=set(allowed_hosts)
        self.permissions_confirmed=permissions_confirmed
        self.robots={}
        self.last_request=0
        self.client=httpx.AsyncClient(timeout=20,follow_redirects=False,trust_env=False,
                                      headers={'User-Agent':USER_AGENT})

    async def close(self):
        await self.client.aclose()

    async def request(self,url):
        # A single retry for transient transport/server failures; access denials and 404 are final.
        for attempt in range(2):
            try:
                response=await self._request_once(url)
                if response[0] not in (429,502,503,504) or attempt:
                    return response
            except (httpx.TimeoutException,httpx.ConnectError):
                if attempt:
                    raise
            await asyncio.sleep(2)

    async def _request_once(self,url):
        url=canonical_url(url)
        p=urlsplit(url)
        if p.hostname not in self.allowed_hosts:
            raise ValueError('Host outside explicit task allowlist')
        address=await public_ip(p.hostname)
        await asyncio.sleep(max(0,1.0-(time.monotonic()-self.last_request)))
        self.last_request=time.monotonic()
        # Pin connection IP after DNS validation; retain original Host and TLS SNI.
        address=f'[{address}]' if ':' in address else address
        connect_url=urlunsplit((p.scheme,address,p.path,p.query,''))
        async with self.client.stream('GET',connect_url,headers={'Host':p.netloc},
                extensions={'sni_hostname':p.hostname}) as response:
            chunks=[]
            size=0
            async for chunk in response.aiter_bytes():
                size+=len(chunk)
                if size>MAX_BYTES: raise ValueError('Response exceeds 2 MB limit')
                chunks.append(chunk)
            body=b''.join(chunks)
            return response.status_code, dict(response.headers), body

    async def robots_allowed(self,url):
        p=urlsplit(url)
        origin=f'{p.scheme}://{p.netloc}'
        if origin not in self.robots:
            robots_url=origin+'/robots.txt'
            for _ in range(4):
                status,headers,body=await self.request(robots_url)
                if status not in (301,302,303,307,308): break
                target=canonical_url(urljoin(robots_url,headers.get('location','')))
                if urlsplit(target).hostname!=p.hostname:
                    raise ValueError('robots redirect outside exact source host')
                if p.scheme=='https' and urlsplit(target).scheme!='https':
                    raise ValueError('robots HTTPS downgrade blocked')
                robots_url=target
            else:
                raise ValueError('Too many robots redirects')
            if status==404:
                self.robots[origin]=(None,1.0,'robots_missing_404')
            elif status==200 and 'html' not in headers.get('content-type','') and not body.lstrip().lower().startswith((b'<!doctype html',b'<html')):
                parser=RobotFileParser()
                parser.parse(body.decode('utf-8',errors='replace').splitlines())
                delay=max(1.0,float(parser.crawl_delay(USER_AGENT) or parser.crawl_delay('*') or 1))
                rate=parser.request_rate(USER_AGENT) or parser.request_rate('*')
                if rate: delay=max(delay,rate.seconds/rate.requests)
                self.robots[origin]=(parser,delay,'robots_checked')
            else:
                raise ValueError(f'robots unavailable or invalid (HTTP {status}); fail closed')
        parser,delay,decision=self.robots[origin]
        if parser and not parser.can_fetch(USER_AGENT,url):
            raise ValueError('robots_disallowed')
        await asyncio.sleep(max(0,delay-(time.monotonic()-self.last_request)))
        return decision

    async def fetch_public_page(self,url):
        if not self.permissions_confirmed:
            raise ValueError('Source terms/access permission review is required')
        initial=canonical_url(url)
        url=initial
        redirects=[]
        for _ in range(4):
            # Validate host/IP before both robots and page; redirects never expand permissions.
            if urlsplit(url).hostname not in self.allowed_hosts:
                raise ValueError('Redirect outside task allowlist')
            robots_decision=await self.robots_allowed(url)
            status,headers,body=await self.request(url)
            if status in (301,302,303,307,308):
                if 'location' not in headers: raise ValueError('Redirect missing location')
                target=canonical_url(urljoin(url,headers['location']))
                if urlsplit(url).scheme=='https' and urlsplit(target).scheme!='https':
                    raise ValueError('HTTPS downgrade blocked')
                redirects.append({'from':url,'to':target,'http_status':status})
                url=target
                continue
            if status!=200: raise ValueError(f'HTTP {status}')
            content_type=headers.get('content-type','').lower()
            is_pdf = body.startswith(b'%PDF-')
            if 'application/pdf' in content_type and not is_pdf:
                raise ValueError('Response claims to be PDF but has no PDF header')
            if not is_pdf and not any(t in content_type for t in ('text/html','application/xhtml+xml','text/plain')):
                raise ValueError(f'Unsupported content type: {content_type}; only HTML/text and text-based PDFs supported')
            encoding='utf-8'
            match=re.search(r'charset=([^;\s]+)',content_type)
            if match: encoding=match[1].strip('"\'')
            try: html=body.decode(encoding,errors='replace')
            except LookupError: html=body.decode('utf-8',errors='replace')
            payload = {'pdf_base64':base64.b64encode(body).decode('ascii')} if is_pdf else {'html':html}
            return {**payload,'format':'pdf' if is_pdf else 'html','url':url,'requested_url':initial,'http_status':status,
                    'captured_at':datetime.now(timezone.utc).isoformat(),
                    'content_hash':hashlib.sha256(body).hexdigest(),
                    'robots_decision':robots_decision,'terms_decision':'operator_confirmed_for_this_run',
                    'content_type':content_type,'redirects':redirects}
        raise ValueError('Too many redirects')

class ToolRunner:
    def __init__(self,fetcher=None):
        self.fetcher=fetcher
        self.trace=[]

    async def call(self,name,**kwargs):
        start=time.monotonic()
        try:
            if name=='fetch_public_page': result=await self.fetcher.fetch_public_page(**kwargs)
            elif name=='clean_html': result=clean_html(**kwargs)
            elif name=='clean_pdf': result=clean_pdf(**kwargs)
            elif name=='discover_links': result=discover_links(**kwargs)
            elif name=='verify_evidence': result=verify_evidence(**kwargs)
            elif name=='inspect_public_source':
                from .connectors import inspect_public_source
                result=inspect_public_source(**kwargs)
            else: raise ValueError('Unknown tool')
            self.trace.append({'tool':name,'status':'ok','url':kwargs.get('url'),
                'duration_ms':round((time.monotonic()-start)*1000)})
            return result
        except Exception as exc:
            self.trace.append({'tool':name,'status':'error','url':kwargs.get('url'),'error':str(exc)[:300]})
            raise
