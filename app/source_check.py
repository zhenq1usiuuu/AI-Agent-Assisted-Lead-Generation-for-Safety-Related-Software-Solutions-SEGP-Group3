"""Read-only source diagnostics using the production fetcher, without model calls."""
import argparse
import asyncio
import json
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlsplit

from .connectors import inspect_public_source
from .sources import SOURCE_BY_ID
from .tools import PublicFetcher


async def check_sources(ids):
    results = []
    for source_id in ids:
        source = SOURCE_BY_ID[source_id]
        fetcher = PublicFetcher([urlsplit(source['url']).hostname], True)
        row = {'source_id':source_id, 'url':source['url']}
        try:
            raw = await fetcher.fetch_public_page(source['url'])
            observed = inspect_public_source(raw.get('html', ''), raw['url'], source_id)
            row.update(status='page_reachable', final_url=raw['url'],
                       robots_decision=raw.get('robots_decision'), **observed)
            if observed['external_embeds']:
                row['status'] = 'requires_external_connector'
            elif any('login_required:' in reason for reason in observed['limitations']):
                row['status'] = 'login_required'
            elif any('interactive_directory:' in reason for reason in observed['limitations']):
                row['status'] = 'interactive_directory_not_supported'
            elif any('missing records' in reason for reason in observed['limitations']):
                row['status'] = 'source_records_missing'
        except (ValueError, OSError) as exc:
            row.update(status='fetch_failed', error=str(exc))
        finally:
            await fetcher.close()
        results.append(row)
    return {'checked_at':datetime.now(timezone.utc).isoformat(), 'model_calls':0,
            'note':'Page reachability does not establish record availability or source completeness.', 'sources':results}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--out', type=Path, default=Path('data/source-health.json'))
    parser.add_argument('--permissions-confirmed', action='store_true')
    args = parser.parse_args()
    if not args.permissions_confirmed:
        parser.error('Review public source access terms and pass --permissions-confirmed.')
    result = asyncio.run(check_sources(['dosh_prosecution', 'dosh_enforcement_dashboard', 'dosh_fatal_cases', 'sirim_directory']))
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding='utf-8')
    for row in result['sources']:
        print(f"{row['source_id']}: {row['status']}")
        for reason in row.get('limitations', []):
            print('  '+reason)
        if row.get('error'):
            print('  '+row['error'])
    print(f'Report: {args.out.resolve()} (no model calls)')


if __name__ == '__main__':
    main()
