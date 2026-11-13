"""Observable execution summaries and monotonic timing; no private model reasoning."""
import asyncio
from contextlib import contextmanager
from datetime import datetime, timezone
import json
import time
import sys
import threading

STAGES = {
    'module_1': 'Interpret the request, fetch permitted sources, clean text and extract evidence-backed signals.',
    'module_2': 'Apply deterministic entity grouping, evidence gates and the scoring rubric.',
    'module_3': 'Explain the supplied evidence and scores, then validate the model response.',
    'module_4': 'Present evidence and recommendations; record the human reviewer decisions.',
    'crm': 'Check completed approvals and write the approved queue transactionally to local SQLite.',
    'clarify_rerun': 'Collect review feedback and clarify the next bounded research round; feedback is not evidence.',
}


LABELS = {
    'module_1': 'Fetching and extracting signals...',
    'module_2': 'Filtering and scoring companies...',
    'module_3': 'Analyzing evidence and preparing recommendations...',
    'module_4': 'Reviewing recommendations with you...',
    'crm': 'Preparing approved leads for CRM...',
    'clarify_rerun': 'Preparing follow-up research...',
}


def duration(seconds):
    seconds = max(0, int(seconds))
    minutes, seconds = divmod(seconds, 60)
    hours, minutes = divmod(minutes, 60)
    return f'{hours:02d}:{minutes:02d}:{seconds:02d}' if hours else f'{minutes:02d}:{seconds:02d}'


class Stopwatch:
    """Single-line terminal timer; never redraw over an input prompt."""
    def __init__(self, start, enabled, stream=None):
        self.stream = stream or sys.stdout
        self.enabled = enabled and self.stream.isatty()
        self.start = start
        self.label = ''
        self.width = 0
        self.lock = threading.Lock()
        self.stop = threading.Event()
        self.thread = None

    def clear(self):
        if self.width:
            self.stream.write('\r' + ' ' * self.width + '\r')
            self.stream.flush()
            self.width = 0

    def render(self):
        with self.lock:
            line = f'{self.label}  Elapsed {duration(time.perf_counter()-self.start)}'
            self.stream.write('\r' + line.ljust(self.width))
            self.stream.flush()
            self.width = len(line)

    def resume(self):
        if not self.enabled or not self.label or self.thread is not None:
            return
        self.stop.clear()
        self.render()
        def refresh():
            while not self.stop.wait(0.5):
                self.render()
        self.thread = threading.Thread(target=refresh, daemon=True)
        self.thread.start()

    def pause(self):
        self.stop.set()
        if self.thread is not None:
            self.thread.join()
            self.thread = None
        with self.lock:
            self.clear()


def timestamp():
    return datetime.now(timezone.utc).isoformat()


class WorkflowTrace:
    def __init__(self, folder, summary, say, ask):
        self.path = folder / 'progress.jsonl'
        self.summary, self.say, self.ask = summary, say, ask
        self.start = time.perf_counter()
        self.wait = 0.0
        self.timer = Stopwatch(self.start, say is print)
        summary['started_at'] = timestamp()
        summary['timing_note'] = 'UTC timestamps; monotonic durations. Processing time excludes time inside input prompts, but includes network/model waits. Setup before workflow launch is excluded.'

    def emit(self, event, **details):
        row = {'timestamp': timestamp(), 'event': event, **details}
        with self.path.open('a', encoding='utf-8') as stream:
            stream.write(json.dumps(row, ensure_ascii=False) + '\n')

    def message(self, text):
        self.timer.pause()
        self.say(text)
        self.timer.resume()

    def prompt(self, message):
        self.timer.pause()
        start = time.perf_counter()
        try:
            return self.ask(message)
        finally:
            self.wait += time.perf_counter() - start
            self.timer.resume()

    @contextmanager
    def stage(self, record, name):
        row = {'stage': name, 'status': 'running', 'started_at': timestamp(),
               'process_summary': STAGES[name], 'result_summary': {}}
        record.setdefault('modules', []).append(row)
        start, wait = time.perf_counter(), self.wait
        self.emit('stage_started', round=record['round'], stage=name, process_summary=STAGES[name])
        self.timer.label = LABELS[name]
        if not self.timer.enabled:
            self.say(LABELS[name])
        self.timer.resume()
        try:
            yield row
            if row['status'] == 'running':
                row['status'] = 'completed'
        except (KeyboardInterrupt, EOFError, asyncio.CancelledError):
            row['status'] = 'interrupted'
            raise
        except Exception as exc:
            row.update(status='failed', error_type=type(exc).__name__)
            raise
        finally:
            self.timer.pause()
            self.timer.label = ''
            elapsed, waiting = time.perf_counter() - start, self.wait - wait
            row.update(finished_at=timestamp(), elapsed_seconds=round(elapsed, 6),
                       user_wait_seconds=round(waiting, 6), processing_seconds=round(max(0,elapsed-waiting), 6))
            self.emit('stage_finished', round=record['round'], **row)
            if row['status'] in ('failed', 'interrupted'):
                self.say(f"{LABELS[name]} {row['status']}. Completed work is saved.")

    def finish(self):
        self.timer.pause()
        for record in self.summary['rounds']:
            present = {r['stage'] for r in record.get('modules', [])}
            for name in STAGES:
                if name not in present:
                    record.setdefault('modules', []).append({'stage':name,'status':'not_executed',
                        'started_at':None,'finished_at':None,'elapsed_seconds':0,
                        'user_wait_seconds':0,'processing_seconds':0,'process_summary':STAGES[name]})
        elapsed = time.perf_counter() - self.start
        self.summary.update(finished_at=timestamp(), elapsed_seconds=round(elapsed,6),
                            user_wait_seconds=round(self.wait,6), processing_seconds=round(max(0,elapsed-self.wait),6))
        self.emit('workflow_finished', status=self.summary['status'], elapsed_seconds=self.summary['elapsed_seconds'],
                  user_wait_seconds=self.summary['user_wait_seconds'], processing_seconds=self.summary['processing_seconds'])

        self.say('')
        self.say(f"Workflow finished ({self.summary['status'].replace('_', ' ')}). Total time: {duration(elapsed)}")
        self.say(f"Processing: {duration(elapsed-self.wait)} | Waiting for input: {duration(self.wait)}")
        for record in self.summary['rounds']:
            self.say(f"Round {record['round']}:")
            for row in record['modules']:
                if row['status'] != 'not_executed':
                    self.say(f"  {LABELS[row['stage']].rstrip('.')} - {duration(row['elapsed_seconds'])} ({row['status'].replace('_', ' ')})")
