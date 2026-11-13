import asyncio
import json
import pytest
from app.workflow_trace import WorkflowTrace


def test_monotonic_timing_separates_input_wait(tmp_path, monkeypatch):
    clock=[10.0]
    monkeypatch.setattr('app.workflow_trace.time.perf_counter',lambda:clock[0])
    def ask(_):
        clock[0]+=3
        return 'y'
    summary={'rounds':[],'status':'completed'}
    trace=WorkflowTrace(tmp_path,summary,lambda _:None,ask)
    record={'round':1};summary['rounds'].append(record)
    with trace.stage(record,'module_4'):
        clock[0]+=2
        trace.prompt('Review')
        clock[0]+=1
    trace.finish()
    stage=record['modules'][0]
    assert stage['elapsed_seconds']==6
    assert stage['user_wait_seconds']==3
    assert stage['processing_seconds']==3
    assert all(x['status']=='not_executed' for x in record['modules'][1:])
    assert summary['elapsed_seconds']==6
    events=[json.loads(line) for line in (tmp_path/'progress.jsonl').read_text().splitlines()]
    assert [e['event'] for e in events]==['stage_started','stage_finished','workflow_finished']


@pytest.mark.parametrize('error,status',[(ValueError('failure'),'failed'),(asyncio.CancelledError(),'interrupted')])
def test_failures_and_cancellation_leave_finished_stage(tmp_path,error,status):
    trace=WorkflowTrace(tmp_path,{'rounds':[]},lambda _:None,lambda _:None)
    record={'round':1}
    with pytest.raises(type(error)):
        with trace.stage(record,'module_3'):
            raise error
    assert record['modules'][0]['status']==status
    assert record['modules'][0]['finished_at']
    assert len((tmp_path/'progress.jsonl').read_text().splitlines())==2


def test_console_is_readable_and_details_stay_in_log(tmp_path):
    messages=[]
    summary={'rounds':[],'status':'completed'}
    trace=WorkflowTrace(tmp_path,summary,messages.append,lambda _:'y')
    record={'round':1};summary['rounds'].append(record)
    with trace.stage(record,'module_1') as row:
        row['result_summary']={'signals':10}
    trace.finish()
    output='\n'.join(messages)
    assert 'Fetching and extracting signals...' in output
    assert 'Total time:' in output
    assert 'stage_started' not in output
    assert 'result_summary' not in output
    assert '+00:00' not in output
    assert 'result_summary' in (tmp_path/'progress.jsonl').read_text()


def test_stopwatch_redraws_and_cleans_up(monkeypatch):
    import io
    from app.workflow_trace import Stopwatch
    class Terminal(io.StringIO):
        def isatty(self): return True
    stream=Terminal()
    monkeypatch.setattr('app.workflow_trace.time.perf_counter',lambda:75)
    watch=Stopwatch(0,True,stream)
    watch.label='Fetching...'
    watch.resume()
    assert 'Elapsed 01:15' in stream.getvalue()
    watch.pause()
    assert watch.thread is None
    assert watch.width == 0
