"""Inspect public SIRIM/DOSH routes without inventing private API endpoints."""
from urllib.parse import urljoin, urlsplit, urlencode, parse_qsl, urlunsplit
from bs4 import BeautifulSoup
import re


def inspect_public_source(html, url, source_id=None, query=None):
    soup = BeautifulSoup(html, 'html.parser')
    host = urlsplit(url).hostname
    targets = []
    limitations = []
    for node in soup.select('iframe[src], embed[src], object[data]'):
        target = urljoin(url, node.get('src') or node.get('data') or '')
        parsed = urlsplit(target)
        if parsed.scheme in ('http','https') and parsed.hostname == host and not parsed.username:
            targets.append({'url':target,'kind':'embedded_public_document','label':'Embedded public document'})
        else:
            limitations.append('Embedded content is outside the selected exact host; no automatic cross-host access.')
    if source_id == 'sirim_directory':
        submitted = False
        for form in soup.find_all('form'):
            action = urljoin(url, form.get('action') or url)
            if form.get('method','get').lower() != 'get' or urlsplit(action).hostname != host:
                limitations.append('SIRIM form requires POST or another host; a verified dedicated adapter is still required.')
                continue
            controls = form.find_all(['input','select','textarea'])
            fields = [n for n in controls if n.get('name') and n.name=='input'
                      and n.get('type','text').lower() in ('text','search')]
            allowed = {'q','query','search','keyword','keywords','company','companyname','company_name'}
            candidates = [n for n in fields if n['name'].lower() in allowed]
            unsupported = any(n.name != 'input' or n.get('type','text').lower() in ('password','file','hidden')
                              for n in controls if n.get('name'))
            if not query or len(candidates)!=1 or len(fields)!=1 or unsupported:
                limitations.append('Public search form requires an explicit --source-query and a supported unambiguous GET form without hidden/session fields.')
                continue
            parts = urlsplit(action)
            parameters = [(k,v) for k,v in parse_qsl(parts.query,keep_blank_values=True) if k != candidates[0]['name']]
            parameters.append((candidates[0]['name'],query))
            target = urlunsplit((parts.scheme,parts.netloc,parts.path,urlencode(parameters),''))
            targets.append({'url':target,'kind':'public_get_search','label':'Public certificate query'})
            submitted = True
        if not submitted:
            limitations.append('No verified executable public certificate query was discovered; certificate records are unavailable from this landing page.')
    if source_id and source_id.startswith('dosh'):
        if soup.select('.display-table-error'):
            limitations.append('DOSH page reports missing records; never infer hidden case rows.')
        for anchor in soup.select('a[href]'):
            target = urljoin(url,anchor['href'])
            if urlsplit(target).hostname == host and urlsplit(target).path.lower().endswith('.pdf'):
                targets.append({'url':target,'kind':'public_download','label':anchor.get_text(' ',strip=True)})
    if source_id and (source_id.startswith('dosh') or source_id=='sirim_directory') and soup.find('table'):
        for anchor in soup.select('a[href]'):
            label=anchor.get_text(' ',strip=True)+' '+str(anchor.get('aria-label',''))
            if 'next' not in anchor.get('rel',[]) and not re.search(r'\b(next|berikut|seterusnya)\b',label,re.I):
                continue
            target=urljoin(url,anchor['href'])
            if urlsplit(target).hostname==host and (source_id!='sirim_directory' or query and query in dict(parse_qsl(urlsplit(target).query)).values()):
                targets.append({'url':target,'kind':'public_get_search' if source_id=='sirim_directory' else 'public_pagination','label':label})
    unique = list({item['url']:item for item in targets}.values())
    return {'targets':unique[:20], 'limitations':list(dict.fromkeys(limitations)),
            'observed_table_count':len(soup.find_all('table'))}
