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


def feedback_prompt(base, events, clarification):
    feedback = [{'account': e['display_name'], 'decision': e['decision'],
                 'reason': e.get('reason'), 'edits': e.get('edits', {})}
                for e in events if e['decision'] in ('reject', 'defer', 'merge')]
    return (base + '\nHuman review feedback (research directions, not source evidence):\n'
            + json.dumps(feedback, ensure_ascii=False) + '\nClarification: ' + clarification)


async def run_workflow(request, reviewer, folder, db_path, max_rounds=2,
                       initial=None, ask=input, say=print):
    """Save each boundary. Each rerun is a new bounded research run, never an approval."""
    folder = Path(folder)
    folder.mkdir(parents=True, exist_ok=False)
    summary = {'workflow_id': folder.name, 'rounds': [], 'status': 'running'}
    base_prompt = request.prompt
    try:
        for number in range(1, max_rounds + 1):
            prefix = folder / f'round-{number}'
            record = {'round': number}
            summary['rounds'].append(record)
            if initial is not None:
                result, initial = initial, None
            else:
                result = await execute(request)
            write_json(Path(str(prefix)+'-research.json'), result)
            record['run_id'] = result.get('run_id')
            if result.get('status') in ('failed', 'needs_clarification'):
                questions = result.get('stages', {}).get('receive_prompt', {}).get('result', {}).get('questions', [])
                say('Research needs attention: ' + json.dumps(questions or result.get('errors', [])))
                clarification = ask('Clarify the request (blank = stop): ').strip() if number < max_rounds else ''
                if not clarification:
                    summary['status'] = 'research_needs_attention'; break
                request = RunRequest.model_validate({**request.model_dump(), 'prompt':base_prompt+'\nClarification: '+clarification, 'resume_run_id':None})
                if ask('Start a fresh paid research round? [y/N]: ').strip().lower() != 'y':
                    summary['status'] = 'stopped'; break
                continue
            if 'filter_score' not in result:
                result = apply_scores(result, score_result(result))
            write_json(Path(str(prefix)+'-scored.json'), result)
            if 'llm_reasoning' not in result:
                say('Module 3 makes paid model calls, including retries; its cost is outside the research dollar cap.')
                if ask('Run paid reasoning now? [y/N]: ').strip().lower() != 'y':
                    summary['status'] = 'reasoning_deferred'; break
                result = apply_reasoning(result, await reason_result(result))
            write_json(Path(str(prefix)+'-reasoned.json'), result)
            review = run_review(result, reviewer, ask=ask, say=say)
            review_path = Path(str(prefix)+'-reviewed.json')
            write_json(review_path, review)
            record['review_file'] = str(review_path)
            if not review['complete']:
                summary['status'] = 'review_incomplete'; break
            if review['crm_queue']:
                say(f"Approved queue: {len(review['crm_queue'])}; SQLite database: {db_path}")
                if ask('Write these approved records to local CRM? [y/N]: ').strip().lower() == 'y':
                    written, skipped = write_review(review, db_path)
                    record['crm'] = {'written':written,'skipped':skipped,'database':str(db_path)}
                    write_json(Path(str(prefix)+'-crm-receipt.json'), record['crm'])
                else:
                    record['crm'] = {'status':'deferred'}
            pending = [e for e in review['events'] if e['decision'] in ('reject','defer','merge')]
            if not pending:
                summary['status'] = 'completed'; break
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
        return summary
    except (KeyboardInterrupt, EOFError, asyncio.CancelledError):
        summary['status'] = 'interrupted'
        raise
    except Exception as exc:
        summary.update(status='failed', error=str(exc))
        raise
    finally:
        write_json(folder/'workflow.json', summary)
        say(f'Workflow records: {folder.resolve()}')


def main(argv=None):
    parser = argparse.ArgumentParser(description='Run all modules in one terminal session, with human review and optional local CRM.')
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
        asyncio.run(run_workflow(request, reviewer, folder, args.db, args.max_rounds, initial))
        return 0
    except (KeyboardInterrupt, EOFError):
        print('Stopped. Completed stage files are retained.'); return 130
    except Exception as exc:
        print(f'Workflow failed: {exc}'); return 1


if __name__ == '__main__':
    raise SystemExit(main())
