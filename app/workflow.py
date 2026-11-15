"""One terminal session: research, scoring, reasoning, review and local CRM."""
import argparse
import asyncio
import json
from pathlib import Path
from uuid import uuid4

from dotenv import load_dotenv
from .context import ROOT
from .models import RunRequest
from .selection import build_selection, parse_categories
from .pipeline import execute
from .scoring import score_result, apply_scores
from .reasoning import reason_result, apply_reasoning
from .review import run_review, write_json
from .crm import write_review
from .workflow_trace import WorkflowTrace


def feedback_prompt(base, events, clarification):
    feedback = [{'account': e['display_name'], 'decision': e['decision'],
                 'reason': e.get('reason'), 'edits': e.get('edits', {})}
                for e in events if e['decision'] in ('reject', 'defer', 'merge')]
    return (base + '\nHuman review feedback (research directions, not source evidence):\n'
            + json.dumps(feedback, ensure_ascii=False) + '\nClarification: ' + clarification)


async def run_workflow(request, reviewer, folder, db_path, max_rounds=2,
                       initial=None, ask=input, say=print, contact_manifest=None,
                       auto_contacts=False, contact_budget_usd=1.0):
    """Save each boundary. Each rerun is a new bounded research run, never an approval."""
    folder = Path(folder)
    folder.mkdir(parents=True, exist_ok=False)
    summary = {'workflow_id': folder.name, 'rounds': [], 'status': 'running'}
    trace = WorkflowTrace(folder, summary, say, ask)
    ask = trace.prompt
    say = trace.message
    from .budget import COST_REPORTER
    cost_token = COST_REPORTER.set(say)
    base_prompt = request.prompt
    try:
        for number in range(1, max_rounds + 1):
            prefix = folder / f'round-{number}'
            record = {'round': number}
            summary['rounds'].append(record)
            with trace.stage(record, 'module_1') as progress:
                if initial is not None:
                    result, initial = initial, None
                    progress['status'] = 'reused'
                else:
                    result = await execute(request)
                    progress['status'] = result.get('status', 'completed')
                progress['result_summary'] = {
                    'documents':len(result.get('documents', [])), 'signals':len(result.get('signals', [])),
                    'errors':len(result.get('errors', [])), 'stop_reason':result.get('stop_reason'),
                    'coverage':result.get('coverage', {}),
                    'diagnostics':result.get('signal_diagnostics', {})}
            for category in result.get('category_summary', []):
                say(f"{category['category']}: {category['documents']} documents, "
                    f"{category['processed_chunks']} chunks processed, {category['remaining_chunks']} pending, "
                    f"{category['signals']} signals ({category['status']}).")
            write_json(Path(str(prefix)+'-research.json'), result)
            record['run_id'] = result.get('run_id')
            if result.get('status') in ('failed', 'needs_clarification'):
                with trace.stage(record, 'clarify_rerun') as progress:
                    questions = result.get('stages', {}).get('receive_prompt', {}).get('result', {}).get('questions', [])
                    say('Research needs attention: ' + json.dumps(questions or result.get('errors', [])))
                    clarification = ask('Clarify the request (blank = stop): ').strip() if number < max_rounds else ''
                    if not clarification:
                        summary['status'] = 'research_needs_attention'; break
                    request = RunRequest.model_validate({**request.model_dump(), 'prompt':base_prompt+'\nClarification: '+clarification, 'resume_run_id':None})
                    if ask('Start a fresh paid research round? [y/N]: ').strip().lower() != 'y':
                        summary['status'] = 'stopped'; break
                    continue
            with trace.stage(record, 'module_2') as progress:
                if 'filter_score' not in result:
                    result = apply_scores(result, score_result(result))
                else:
                    progress['status'] = 'reused'
                progress['result_summary'] = {'accounts':len(result.get('filter_score', {}).get('accounts', []))}
            write_json(Path(str(prefix)+'-scored.json'), result)
            with trace.stage(record, 'module_3') as progress:
                if 'llm_reasoning' not in result:
                    say('Module 3 makes paid model calls, including retries; its cost is outside the research dollar cap.')
                    if ask('Run paid reasoning now? [y/N]: ').strip().lower() != 'y':
                        progress['status'] = 'deferred'
                        summary['status'] = 'reasoning_deferred'; break
                    result = apply_reasoning(result, await reason_result(result))
                else:
                    progress['status'] = 'reused'
                reasoning = result['llm_reasoning']
                progress['result_summary'] = {'accounts':len(reasoning.get('accounts', [])),
                    'validation':reasoning.get('validation', {}), 'summary':reasoning.get('summary', '')}
            write_json(Path(str(prefix)+'-reasoned.json'), result)
            with trace.stage(record, 'module_4') as progress:
                review = run_review(result, reviewer, ask=ask, say=say)
                progress['status'] = 'completed' if review['complete'] else 'interrupted'
                progress['result_summary'] = {'decisions':review.get('summary', {}),
                    'reviewed':review.get('accounts_reviewed', len(review['events'])),
                    'approved_queue':len(review['crm_queue'])}
            if review['complete'] and review['crm_queue'] and (contact_manifest is not None or auto_contacts):
                from .contacts import discover_contacts, review_contacts
                with trace.stage(record, 'contacts') as progress:
                    if auto_contacts and contact_manifest is None:
                        say(f'Contact discovery: up to 5 paid searches and 20 pages; model cap USD {contact_budget_usd:.2f} per round. Search fees are separate from model/research caps.')
                        if ask('Search public business contacts now? [y/N]: ').strip().lower() == 'y':
                            from .contact_discovery import discover_automatic
                            from .budget import EvaluationBudget
                            budget = EvaluationBudget(Path(str(prefix)+'-contact-budget.json'), contact_budget_usd)
                            discovery = await discover_automatic(review['crm_queue'], budget, ask=ask, say=say)
                            write_json(Path(str(prefix)+'-contacts.json'), discovery)
                            say(f"Found {len(discovery['candidates'])} contact candidates; {len(discovery['errors'])} retrieval/extraction issues. See the contacts JSON for details.")
                            review['contact_reviews'] = review_contacts(discovery, reviewer, ask, say)
                            progress['result_summary'] = {'candidates':len(discovery['candidates']), 'approved':len(review['contact_reviews']), 'errors':discovery['errors']}
                        else:
                            progress['status'] = 'deferred'
                    elif contact_manifest.get('sources') and ask('Reviewed contact-page terms and public access permissions? [y/N]: ').strip().lower()!='y':
                        progress['status']='deferred'
                    else:
                        discovery=await discover_contacts(contact_manifest,review['crm_queue'])
                        write_json(Path(str(prefix)+'-contacts.json'),discovery)
                        review['contact_reviews']=review_contacts(discovery,reviewer,ask,say)
                        progress['result_summary']={'candidates':len(discovery['candidates']),'approved':len(review['contact_reviews']),'errors':discovery['errors']}
            review_path = Path(str(prefix)+'-reviewed.json')
            write_json(review_path, review)
            record['review_file'] = str(review_path)
            if not review['complete']:
                summary['status'] = 'review_incomplete'; break
            with trace.stage(record, 'crm') as progress:
                if review['crm_queue']:
                    say(f"Approved queue: {len(review['crm_queue'])}; SQLite database: {db_path}")
                    if ask('Write these approved records to local CRM? [y/N]: ').strip().lower() == 'y':
                        written, skipped = write_review(review, db_path)
                        record['crm'] = {'written':written,'skipped':skipped,'database':str(db_path)}
                        write_json(Path(str(prefix)+'-crm-receipt.json'), record['crm'])
                    else:
                        record['crm'] = {'status':'deferred'}
                progress['status'] = 'completed' if record.get('crm', {}).get('status') != 'deferred' else 'deferred'
                if not review['crm_queue']:
                    progress['status'] = 'skipped'
                progress['result_summary'] = record.get('crm', {'reason':'No approved queue items.'})
            with trace.stage(record, 'clarify_rerun') as progress:
                pending = [e for e in review['events'] if e['decision'] in ('reject','defer','merge')]
                if not pending:
                    progress['status'] = 'skipped'
                    summary['status'] = 'completed'; break
                progress['result_summary'] = {'feedback_items':len(pending), 'next_round':None}
                write_json(Path(str(prefix)+'-feedback.json'), {'events':pending})
                say('Reject/defer/merge decisions saved. Merge does not automatically combine entities.')
                if number == max_rounds:
                    summary['status'] = 'round_limit'; break
                if ask('Clarify and research these decisions again? [y/N]: ').strip().lower() != 'y':
                    summary['status'] = 'feedback_saved'; break
                clarification = ask('What evidence or correction should the next research round seek? ').strip()
                if not clarification:
                    summary['status'] = 'feedback_saved'; break
                extra = ask('Optional public evidence URL for the next round (blank = keep sources): ').strip()
                values = {**request.model_dump(), 'prompt':feedback_prompt(base_prompt,pending,clarification),
                          'resume_run_id':None}
                if extra:
                    values['additional_urls'] = list(dict.fromkeys([*request.additional_urls,extra]))
                request = RunRequest.model_validate(values)
                say('Next round uses the same selected source categories plus any added URL. Prior approvals are not reused.')
                if ask('Confirm source access permissions and start a fresh paid round? [y/N]: ').strip().lower() != 'y':
                    summary['status'] = 'feedback_saved'; break
                progress['result_summary'] = {'next_round':number+1, 'feedback_items':len(pending)}
        return summary
    except (KeyboardInterrupt, EOFError, asyncio.CancelledError):
        summary['status'] = 'interrupted'
        raise
    except Exception as exc:
        summary.update(status='failed', error=str(exc))
        raise
    finally:
        COST_REPORTER.reset(cost_token)
        trace.finish()
        write_json(folder/'workflow.json', summary)
        say(f'Workflow records: {folder.resolve()}')


def main(argv=None):
    parser = argparse.ArgumentParser(description='Run all modules in one terminal session, with human review and optional local CRM.')
    parser.add_argument('--contacts-file', type=Path, help='Optional contact evidence / explicit company contact-page manifest.')
    parser.add_argument('--skip-contacts', action='store_true', help='Skip automatic public contact discovery.')
    parser.add_argument('--contact-budget-usd', type=float, default=1.0, help='Per-round contact model reservation cap; search fees are separate.')
    parser.add_argument('--categories')
    parser.add_argument('--reviewer')
    parser.add_argument('--from-result', type=Path, help='Continue from an exported research/scored/reasoned JSON (not a checkpoint wrapper).')
    parser.add_argument('--budget-usd', type=float, default=1)
    parser.add_argument('--max-rounds', type=int, choices=range(1,4), default=2)
    parser.add_argument('--max-pages', type=int, default=20)
    parser.add_argument('--max-chunks', type=int, default=24)
    parser.add_argument('--ocr', action='store_true')
    parser.add_argument('--db', type=Path, default=ROOT/'data/crm.db')
    args = parser.parse_args(argv)
    if not 0 < args.contact_budget_usd <= 10:
        parser.error('--contact-budget-usd must be greater than 0 and at most 10.')
    load_dotenv(ROOT/'.env')
    try:
        initial = json.loads(args.from_result.read_text(encoding='utf-8-sig')) if args.from_result else None
        categories = args.categories or (None if initial and initial.get('request') else input('Categories [DOSH,SIRIM,ESG,FMM,UNIONS,ASSOCIATIONS,OSH or ALL]: '))
        if categories:
            prompt, sources, _ = build_selection(parse_categories(categories))
            request = RunRequest(prompt=prompt, source_ids=sources, budget_usd=args.budget_usd,
                                 max_pages=args.max_pages, max_chunks=args.max_chunks, ocr=args.ocr)
        else:
            request = RunRequest.model_validate({**initial['request'], 'resume_run_id':None})
        reviewer = (args.reviewer or input('Reviewer name: ')).strip()
        if not reviewer:
            raise ValueError('Reviewer name is required.')
        if initial is None:
            from .sources import SOURCE_BY_ID
            for source in request.source_ids:
                print(SOURCE_BY_ID[source]['url'])
            if input('Reviewed source terms and public access permissions? [y/N]: ').strip().lower() != 'y':
                return 0
        request = request.model_copy(update={'permissions_confirmed':True})
        folder = ROOT/'data/workflows'/str(uuid4())
        asyncio.run(run_workflow(request, reviewer, folder, args.db, args.max_rounds, initial, contact_manifest=json.loads(args.contacts_file.read_text(encoding='utf-8-sig')) if args.contacts_file else None, auto_contacts=not args.skip_contacts, contact_budget_usd=args.contact_budget_usd))
        return 0
    except (KeyboardInterrupt, EOFError):
        print('Stopped. Completed stage files are retained.'); return 130
    except Exception as exc:
        print(f'Workflow failed: {exc}'); return 1


if __name__ == '__main__':
    raise SystemExit(main())
