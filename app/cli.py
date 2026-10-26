"""Interactive terminal and one-shot JSON entry points for the research pipeline."""
import argparse
import asyncio
import json
import os
import sys
from pathlib import Path

from dotenv import load_dotenv
from pydantic import ValidationError

from .context import ROOT
from .models import RunRequest
from .pipeline import execute
from .sources import SOURCES, SOURCE_BY_ID, SeedId
from .selection import CATEGORIES, parse_categories, build_selection


def parser():
    command = argparse.ArgumentParser(description='Run Safety Signal Lab directly in your terminal. No web server required.')
    inputs = command.add_mutually_exclusive_group()
    inputs.add_argument('--resume', help='Resume a saved run ID; budget is cumulative for that run.')
    inputs.add_argument('--categories', help='Generate a prompt from category names or numbers, e.g. DOSH,ESG or ALL; runs once.')
    inputs.add_argument('--prompt', help='Run one research task and print its JSON result.')
    inputs.add_argument('--prompt-file', type=Path, help='Read a one-shot prompt from a UTF-8 text file.')
    inputs.add_argument('--list-sources', action='store_true', help='Print the registered source URLs, roles and connector limits as JSON; no API key or network required.')
    command.add_argument('--source', action='append', default=[], choices=[s.value for s in SeedId],
                         help='Explicit registered source ID; repeat up to sixteen times. Incompatible with --categories. Overrides model seed selection, not connector limits.')
    command.add_argument('--mode', choices=('live', 'demo'), help='Default: ask in interactive mode; live in one-shot mode.')
    command.add_argument('--url', action='append', default=[], help='Additional permitted public URL; repeat up to four times.')
    command.add_argument('--source-class', choices=('company_owned', 'industry_procurement'), default='company_owned')
    command.add_argument('--max-pages', type=int, choices=range(1, 33), default=None, help='Fetch-attempt limit (1-32). Default 20; all sources share this cap.')
    command.add_argument('--budget-usd', type=float, default=None, help='Cumulative per-run USD cap, default 1.00; resume retains the previous cap unless specified.')
    command.add_argument('--max-depth', type=int, choices=range(4), default=3)
    command.add_argument('--links-per-page', type=int, choices=range(1,6), default=3)
    command.add_argument('--max-chunks', type=int, default=None, help='Maximum extraction calls per invocation, default 24.')
    command.add_argument('--semantic-review', choices=('off','risk'), default='risk')
    command.add_argument('--ocr', action='store_true', help='Use optional local OCR for scanned PDF pages.')
    command.add_argument('--source-query', help='Explicit search term for a supported public SIRIM GET form.')
    command.add_argument('--permissions-confirmed', action='store_true', help='Confirm that you reviewed source terms and access permissions. Robots checks still apply.')
    command.add_argument('--output', type=Path, help='Save one-shot JSON to a new UTF-8 file; existing files are never overwritten.')
    return command


def ask(message, default=''):
    value = input(message).strip()
    return value or default


def prompt_text(message):
    value = ask(message)
    if value.lower() in ('quit', 'exit', '/quit', '/exit'):
        raise EOFError
    return value


def validate_live(request):
    if request.mode == 'live':
        key = os.getenv('MOONSHOT_API_KEY', '').strip()
        if not key or key.startswith('YOUR_'):
            raise ValueError('Set MOONSHOT_API_KEY in the project .env file, or use --mode demo.')
        if not request.permissions_confirmed:
            raise ValueError('Review the source terms and access permissions, then use --permissions-confirmed or confirm interactively.')


def processing_options(args):
    return dict(budget_usd=args.budget_usd if args.budget_usd is not None else 1.0, max_depth=args.max_depth, links_per_page=args.links_per_page, max_chunks=args.max_chunks if args.max_chunks is not None else 24, semantic_review=args.semantic_review, ocr=args.ocr, source_query=args.source_query)


async def run_task(request):
    validate_live(request)
    print('Running: Receive prompt -> Fetch + clean -> Extract signals', file=sys.stderr, flush=True)
    async with asyncio.timeout(900):
        return await execute(request)


def emit(result, output=None):
    encoded = json.dumps(result, ensure_ascii=False, indent=2)
    if output:
        # Exclusive creation prevents accidental overwrites of research records.
        with output.open('x', encoding='utf-8', newline='\n') as destination:
            destination.write(encoded + '\n')
        print(f'Saved JSON: {output.resolve()}', file=sys.stderr)
    print(encoded, flush=True)


def interactive(args):
    print('Safety Signal Lab | Interactive terminal')
    print('Select source categories to generate a research prompt. Enter quit or exit to leave.')
    mode = args.mode
    while mode not in ('live', 'demo'):
        mode = ask('Mode [live/demo, default live]: ', 'live').lower()
    if mode == 'demo':
        print('Demo uses a fixed synthetic fixture: no LLM, no network, and no prompt interpretation.')
    while True:
        print('\nSource categories:')
        for index, (name, (_, guidance)) in enumerate(CATEGORIES.items(), 1):
            print(f'{index}. {name}: {guidance}')
        print('0. ALL (all categories). Example: 1,3,4 or DOSH ESG FMM')
        try:
            categories = parse_categories(prompt_text('Select categories: '))
        except ValueError as exc:
            print(f'Error: {exc}', file=sys.stderr)
            continue
        prompt, source_ids, default_budget = build_selection(categories)
        budget = args.max_pages or default_budget
        print('\nGenerated research prompt:\n' + prompt)
        print(f'Fetch-attempt budget: {budget}. Selected sources:')
        for source_id in source_ids:
            source = SOURCE_BY_ID[source_id]
            print(f"- {source_id} [{source['connector']}, {source['evidence_scope']}]: {source['url']}")
        if budget < sum(SOURCE_BY_ID[s]['connector'] != 'manual' for s in source_ids):
            print('Coverage warning: the page budget cannot attempt every selected seed.')
        print(f'Processing limits: depth {args.max_depth}, {args.links_per_page} links/page, USD {args.budget_usd if args.budget_usd is not None else 1.0:.2f} cumulative run budget; review={args.semantic_review}, OCR={args.ocr}.')
        print('Selection does not guarantee records: form/dashboard connectors and access limits still apply.')
        try:
            urls = list(args.url)
            if mode == 'live' and not urls:
                print('Optional additional public URLs: one per line, blank line to finish (maximum 4).')
                for _ in range(4):
                    url = ask('URL: ')
                    if not url:
                        break
                    urls.append(url)
            source_class = args.source_class
            if mode == 'live' and urls:
                source_class = ask('Source class [company_owned/industry_procurement, default company_owned]: ', source_class)
            confirmed = args.permissions_confirmed
            if mode == 'live' and not confirmed:
                print('Sources: the registered candidates above and any additional URLs. Robots rules are checked independently.')
                confirmed = ask('Have you reviewed their terms and public access permissions? [y/N]: ').lower() in ('y', 'yes')
            request = RunRequest(prompt=prompt, mode=mode, additional_urls=urls,
                                 additional_source_class=source_class, permissions_confirmed=confirmed,
                                 max_pages=budget, source_ids=source_ids, **processing_options(args))
            while True:
                result = asyncio.run(run_task(request))
                emit(result)
                if result['status'] != 'needs_clarification':
                    break
                questions = result['stages']['receive_prompt']['result'].get('questions', [])
                print('\nClarification required:')
                for question in questions:
                    print(f'- {question}')
                details = prompt_text('Additional details (blank to return to a new task): ')
                if not details:
                    break
                # Carry the original request and all answers forward, without inventing memory.
                request = RunRequest(**{**request.model_dump(), 'prompt':request.prompt + '\nAdditional details: ' + details})
            path = ask('Save the last JSON result to a new file? [path or Enter to skip]: ')
            if path:
                encoded = json.dumps(result, ensure_ascii=False, indent=2)
                with Path(path).expanduser().open('x', encoding='utf-8', newline='\n') as destination:
                    destination.write(encoded + '\n')
                print(f'Saved JSON: {Path(path).expanduser().resolve()}')
        except TimeoutError:
            print('Error: Task reached the 15-minute limit. Reduce --max-pages.', file=sys.stderr)
        except (ValueError, ValidationError, OSError) as exc:
            print(f'Error: {exc}', file=sys.stderr)
        except Exception:
            print('Error: Task execution failed. Check network connectivity and model configuration.', file=sys.stderr)


def main(argv=None):
    args = parser().parse_args(argv)
    load_dotenv(ROOT / '.env')
    # Preserve original-language evidence when writing UTF-8 JSON in PowerShell.
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, 'reconfigure'):
            stream.reconfigure(encoding='utf-8')
    try:
        if args.list_sources:
            emit({'sources':SOURCES, 'note':'Parser support is not proof of access or company-level record availability.'}, args.output)
            return 0
        if args.resume:
            from .storage import run_folder
            if args.source or args.url or args.source_query:
                raise ValueError('Resume preserves the saved source scope; start a new run to change it')
            if args.output and args.output.exists():
                raise FileExistsError(f'Output file already exists: {args.output}')
            state = json.loads((run_folder(args.resume) / 'checkpoint.json').read_text(encoding='utf-8'))
            saved = state['result']['request']
            saved.update(resume_run_id=args.resume, permissions_confirmed=args.permissions_confirmed)
            for name, value in [('budget_usd', args.budget_usd), ('max_pages', args.max_pages), ('max_chunks', args.max_chunks)]:
                if value is not None:
                    saved[name] = value
            result = asyncio.run(run_task(RunRequest.model_validate(saved)))
            emit(result, args.output)
            return 1 if result['status']=='failed' else 0
        one_shot = args.categories is not None or args.prompt is not None or args.prompt_file is not None
        if args.source and (args.categories is not None or not one_shot):
            raise ValueError('--source requires a custom --prompt or --prompt-file; category selection sets the sources automatically.')
        if not one_shot:
            if args.output:
                raise ValueError('--output requires --categories, --prompt or --prompt-file. Interactive mode asks where to save each result.')
            interactive(args)
            return 0
        if args.output and args.output.exists():
            raise FileExistsError(f'Output file already exists: {args.output}')
        if args.categories is not None:
            prompt, source_ids, default_budget = build_selection(parse_categories(args.categories))
        else:
            prompt = args.prompt if args.prompt is not None else args.prompt_file.read_text(encoding='utf-8-sig').strip()
            source_ids, default_budget = args.source, 20
        request = RunRequest(prompt=prompt, mode=args.mode or 'live', additional_urls=args.url,
                             additional_source_class=args.source_class,
                             permissions_confirmed=args.permissions_confirmed, max_pages=args.max_pages or default_budget,
                             source_ids=source_ids, **processing_options(args))
        result = asyncio.run(run_task(request))
        emit(result, args.output)
        return 1 if result['status'] == 'failed' else 0
    except EOFError:
        print('\nGoodbye.', file=sys.stderr)
        return 0
    except KeyboardInterrupt:
        print('\nCancelled.', file=sys.stderr)
        return 130
    except TimeoutError:
        message = 'Task reached the 15-minute limit. Reduce --max-pages.'
    except (ValueError, ValidationError, OSError) as exc:
        message = str(exc)
    except Exception:
        message = 'Task execution failed. Check network connectivity and model configuration.'
    print(json.dumps({'status':'error', 'message':message}, ensure_ascii=False), file=sys.stderr)
    return 1


if __name__ == '__main__':
    raise SystemExit(main())
