"""Single source of truth for source selection and context metadata."""
import json
from enum import Enum
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SOURCES = json.loads((ROOT / 'config/sources.json').read_text(encoding='utf-8-sig'))
SOURCE_BY_ID = {source['id']: source for source in SOURCES}
if len(SOURCE_BY_ID) != len(SOURCES):
    raise ValueError('Duplicate source IDs in config/sources.json')

# Derive the schema enum from the registry so prompts and runtime cannot drift.
SeedId = Enum('SeedId', {s['id']: s['id'] for s in SOURCES if s['purpose'] == 'lead_evidence'}, type=str)


def source_for_url(url):
    """Keep landing/manual restrictions on query variants; query-specific PDF URLs stay exact."""
    from urllib.parse import urlsplit, urlunsplit
    def key(value):
        p = urlsplit(value)
        return urlunsplit((p.scheme, p.netloc.lower(), p.path.rstrip('/'), p.query, ''))
    for source in SOURCES:
        if key(source['url']) == key(url):
            return source
        # A query string does not turn a dashboard/landing route into verified records.
        if not urlsplit(source['url']).query:
            candidate = urlsplit(url)
            without_query = urlunsplit((candidate.scheme, candidate.netloc, candidate.path, '', ''))
            if key(source['url']) == key(without_query):
                return source
    return None
