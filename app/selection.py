"""Deterministic terminal categories and research prompt assembly."""
import re

from .sources import ROOT, SOURCES

CATEGORIES = {
    'DOSH': ('regulatory', 'Official prosecution, compound and fatal-accident records. Preserve exact case outcomes and distinguish operators from affected companies.'),
    'SIRIM': ('certification', 'Certification discovery. Record scheme, scope, site and status only from actual certificate records; do not invent form-query results.'),
    'ESG': ('esg_index', 'ESG-index candidate discovery. Index membership is not legal compliance, company size or evidence of buying intent.'),
    'FMM': ('industry_procurement', 'Public FMM newsroom articles and publications. These are not a company directory or accident register.'),
    'UNIONS': ('trade_union', 'Worker-union public sources. Membership and general campaigns alone are not company-specific needs.'),
    'ASSOCIATIONS': ('industry_association', 'Employer and sector associations. Membership alone is not incident, certification or buying evidence.'),
    'OSH': ('professional_body', 'Occupational safety professional bodies. General training and guidance alone are not company-level signals.'),
}


def parse_categories(value):
    tokens = [s.upper() for s in re.split(r'[\s,;]+', value.strip()) if s]
    if not tokens:
        raise ValueError('Select at least one category; use numbers, names, or ALL.')
    names = list(CATEGORIES)
    selected = []
    for token in tokens:
        if token in ('ALL', '0'):
            selected.extend(names)
        elif token.isdigit() and 1 <= int(token) <= len(names):
            selected.append(names[int(token) - 1])
        elif token in CATEGORIES:
            selected.append(token)
        else:
            raise ValueError(f'Unknown category: {token}. Use the displayed numbers or names.')
    return list(dict.fromkeys(selected))


def build_selection(categories):
    sources = [s for name in categories for s in SOURCES
               if s['purpose'] == 'lead_evidence' and s['source_class'] == CATEGORIES[name][0]]
    sources = list({s['id']: s for s in sources}.values())
    template = (ROOT / 'prompts/research_template.txt').read_text(encoding='utf-8')
    prompt = template.format(
        categories=', '.join(categories),
        category_instructions='\n'.join(f'- {name}: {CATEGORIES[name][1]}' for name in categories),
        source_ids=', '.join(s['id'] for s in sources),
    )
    # Shared bounded crawl budget; actual attempts depend on discovered public links.
    budget = 20
    return prompt, [s['id'] for s in sources], budget
