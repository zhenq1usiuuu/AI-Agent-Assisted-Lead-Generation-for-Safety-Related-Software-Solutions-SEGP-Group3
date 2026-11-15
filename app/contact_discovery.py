"""Bounded public business-contact search, retrieval and evidence extraction."""
import os
import re
from urllib.parse import urljoin, urlsplit, urldefrag

import httpx
from bs4 import BeautifulSoup
from pydantic import BaseModel, ConfigDict, Field

from .contacts import validate_contact
from .llm import KimiClient
from .tools import PublicFetcher, clean_pdf


class ExtractedContact(BaseModel):
    model_config = ConfigDict(extra='forbid')
    kind: str
    name: str | None
    job_title: str | None
    work_email: str | None
    phone: str | None
    excerpt: str
    company_excerpt: str


class ContactExtraction(BaseModel):
    model_config = ConfigDict(extra='forbid')
    contacts: list[ExtractedContact] = Field(max_length=10)


def contact_page(html, url):
    """Keep contact footers and explicit mailto/tel values as source evidence."""
    soup = BeautifulSoup(html, 'html.parser')
    for tag in soup(['script', 'style', 'noscript', 'template']):
        tag.decompose()
    links = []
    for a in soup.find_all('a', href=True):
        href = a['href']
        if href.lower().startswith(('mailto:', 'tel:')):
            value = href.split(':', 1)[1].split('?', 1)[0]
            a.append(' ' + value)
        else:
            target = urldefrag(urljoin(url, href))[0]
            if (urlsplit(target).scheme == 'https' and
                    urlsplit(target).hostname == urlsplit(url).hostname and
                    re.search(r'contact|about|team|management|leadership|corporate', target+' '+a.get_text(), re.I)):
                links.append(target)
    text = '\n'.join(line.strip() for line in soup.get_text('\n').splitlines() if line.strip())
    return text[:180000], list(dict.fromkeys(links))[:20]


async def search_company(name):
    """One paid Moonshot Basic search. No model-generated URLs or retry loops."""
    key = os.getenv('MOONSHOT_API_KEY', '').strip()
    base = os.getenv('MOONSHOT_BASE_URL', 'https://api.moonshot.ai/v1').rstrip('/')
    if base not in ('https://api.moonshot.ai/v1', 'https://api.moonshot.cn/v1') or not key:
        raise ValueError('Contact search requires an official Moonshot endpoint and MOONSHOT_API_KEY.')
    async with httpx.AsyncClient(timeout=45, trust_env=False) as client:
        response = await client.post(base+'/tools/search', headers={'Authorization':'Bearer '+key},
            json={'text_query':f'"{name}" Malaysia official company contact email phone',
                  'limit':5, 'timeout_seconds':30, 'include_content':False})
    if response.status_code != 200:
        raise ValueError(f'Contact search HTTP {response.status_code}; check search API access. No fallback data invented.')
    data = response.json()
    if not isinstance(data.get('search_results'), list):
        raise ValueError('Unsupported search response; no URLs inferred.')
    return data['search_results'][:5]


async def discover_automatic(companies, budget, ask=input, say=print, search=None, model=None, fetcher_factory=None):
    """At most five companies, one search and four fetched pages per company.

    Search results are candidates only. The operator approves the exact host
    and access terms before retrieval; contacts require a separate review.
    """
    search = search or search_company
    model = model or KimiClient(budget=budget, max_output_tokens=2500)
    fetcher_factory = fetcher_factory or PublicFetcher
    result = {'candidates':[], 'errors':[], 'searches':[], 'pages':[],
              'unprocessed_accounts':[c['account_key'] for c in companies[5:]],
              'limits':{'companies':5, 'searches_per_company':1, 'pages_per_company':4}}
    for company in companies[:5]:
        key, name = company['account_key'], company['display_name']
        fetcher = None
        try:
            found = await search(name)
            options = [r for r in found if isinstance(r, dict) and
                       urlsplit(r.get('url', '')).scheme == 'https' and
                       not urlsplit(r['url']).username]
            result['searches'].append({'account_key':key, 'results':options})
            if not options:
                raise ValueError('No HTTPS company website candidates found.')
            say(f'Website candidates for {name}:')
            for index, row in enumerate(options, 1):
                say(f"{index}. {row.get('title', '')} — {row['url']}")
            answer = ask('Select the verified company website and confirm its public access terms [number, blank = skip]: ').strip()
            if not answer:
                result['unprocessed_accounts'].append(key)
                continue
            if not answer.isdigit() or not 1 <= int(answer) <= len(options):
                raise ValueError('Invalid website selection; company skipped.')
            seed = options[int(answer)-1]['url']
            host = urlsplit(seed).hostname
            fetcher = fetcher_factory([host], True)
            queue = [seed] + [r['url'] for r in options if urlsplit(r['url']).hostname == host]
            seen = set()
            while queue and len(seen) < 4:
                url = queue.pop(0)
                if url in seen:
                    continue
                seen.add(url)
                try:
                    raw = await fetcher.fetch_public_page(url)
                    if raw.get('format') == 'pdf':
                        text = clean_pdf(raw['pdf_base64'], raw['url'])['text'][:180000]
                        links = []
                    else:
                        text, links = contact_page(raw['html'], raw['url'])
                    queue.extend(link for link in links if link not in seen)
                    result['pages'].append({'account_key':key, 'url':raw['url'], 'captured_at':raw['captured_at']})
                    # Keep the company identity and local communication blocks in a bounded payload.
                    at = text.casefold().find(name.casefold())
                    if at < 0:
                        raise ValueError('Exact company name absent; association cannot be verified on this page.')
                    company_quote = text[max(0, at-100):at+len(name)+100]
                    blocks = []
                    for match in re.finditer(r'@|\b(?:phone|telephone|tel|fax)\b|\+\d', text, re.I):
                        block = text[max(0, match.start()-600):match.end()+600]
                        if block not in blocks:
                            blocks.append(block)
                        if len(blocks) == 8:
                            break
                    if not blocks:
                        continue
                    extracted = await model.generate('extract_contacts',
                        {'company':name, 'company_excerpt':company_quote, 'contact_blocks':blocks}, ContactExtraction)
                    for item in extracted.contacts:
                        raw_contact = {**item.model_dump(), 'account_key':key, 'source_url':raw['url'],
                                       'captured_at':raw['captured_at'], 'source_text':text}
                        try:
                            result['candidates'].append(validate_contact(raw_contact, name))
                        except ValueError as exc:
                            result['errors'].append({'account_key':key, 'url':url, 'message':str(exc)})
                except (ValueError, httpx.HTTPError) as exc:
                    result['errors'].append({'account_key':key, 'url':url, 'message':str(exc)})
        except (ValueError, httpx.HTTPError) as exc:
            result['errors'].append({'account_key':key, 'message':str(exc)})
        finally:
            if fetcher:
                await fetcher.close()
    result['candidates'] = list({c['contact_id']:c for c in result['candidates']}.values())
    result['model_usage'] = model.usage
    return result
