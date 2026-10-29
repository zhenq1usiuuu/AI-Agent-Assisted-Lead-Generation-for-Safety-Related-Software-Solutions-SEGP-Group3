"""Local chunking and conservative document deduplication; no model calls."""
import hashlib
import re
from difflib import SequenceMatcher
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit


def normalized_url(url):
    p = urlsplit(url)
    # Keep identifiers, filters and pagination. Remove tracking parameters only.
    query = [(k, v) for k, v in parse_qsl(p.query, keep_blank_values=True)
             if not k.lower().startswith('utm_') and k.lower() not in ('gclid', 'fbclid')]
    return urlunsplit((p.scheme, p.netloc.lower(), p.path or '/', urlencode(query), ''))


def text_fingerprint(text):
    return hashlib.sha256(re.sub(r'\s+', ' ', text).strip().encode('utf-8')).hexdigest()


def duplicate_of(document, previous):
    """Only exact normalized bodies are suppressed; near matches remain evidence."""
    fingerprint = text_fingerprint(document['text'])
    near = []
    for other in previous:
        if document.get('evidence_scope') != other.get('evidence_scope'):
            continue
        if document.get('source_class') != other.get('source_class'):
            continue
        if fingerprint == text_fingerprint(other['text']):
            return other, near
        # Bound comparison work. Similar pages with changed dates/names/prices are NOT merged.
        if len(document['text']) < 20000 and len(other['text']) < 20000:
            left, right = document['text'], other['text']
            if min(len(left), len(right)) >= .8 * max(len(left), len(right)):
                if SequenceMatcher(None, left, right, autojunk=True).ratio() >= .90:
                    near.append(other['document_id'])
    return None, near


def chunk_document(document, size=4500, max_lines=12):
    """Partition full text into contiguous spans; context never changes quote offsets.

    Short line batches avoid the former six-company sampling behavior in lists.
    Neighbor text is supplied as context, not as a second extraction target.
    """
    text = document['text']
    chunks = []
    start = 0
    while start < len(text):
        end = min(start + size, len(text))
        breaks = [m.end() + start for m in re.finditer(r'\n', text[start:end])]
        if len(breaks) >= max_lines:
            end = breaks[max_lines - 1]
        elif end < len(text) and breaks:
            end = breaks[-1]
        if end <= start:
            end = min(start + size, len(text))
        if text[start:end].strip():
            index = len(chunks)
            chunks.append({'chunk_id': f"{document['document_id']}:{start}:{end}",
                           'document_id': document['document_id'], 'index': index,
                           'start': start, 'end': end, 'status': 'pending', 'split_depth': 0})
        start = end
    return chunks


def split_chunk(chunk, text):
    start, end = chunk['start'], chunk['end']
    if end - start < 200 or chunk.get('split_depth', 0) >= 4:
        return []
    middle = (start + end) // 2
    boundary = text.find('\n', middle, end - 50)
    middle = boundary + 1 if boundary >= 0 else middle
    return [{**chunk, 'chunk_id':f"{chunk['document_id']}:{a}:{b}", 'start':a,'end':b,
             'status':'pending','split_depth':chunk.get('split_depth',0)+1}
            for a,b in ((start,middle),(middle,end))]
