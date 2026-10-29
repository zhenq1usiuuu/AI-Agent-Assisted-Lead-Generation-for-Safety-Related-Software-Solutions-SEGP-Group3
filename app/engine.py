"""Bounded live workflow with durable queues, chunk coverage and optional review."""
import asyncio
import hashlib
import json
import time
from datetime import datetime, timezone
from urllib.parse import urlsplit
from uuid import uuid4

from .budget import BudgetExceeded, EvaluationBudget
from .content import normalized_url, duplicate_of, chunk_document, split_chunk
from .context import PREFIX_HASH
from .llm import KimiClient
from .models import Plan, ChunkExtraction, SemanticReview, RunRequest
from .sources import SOURCES, SOURCE_BY_ID, source_for_url
from .storage import run_folder, run_lock, save_state
from .tools import PublicFetcher, ToolRunner, canonical_url


def selected_seeds(request, plan):
    seeds=[dict(SOURCE_BY_ID[s]) for s in dict.fromkeys(request.source_ids or plan.seed_ids)]
    for url in request.additional_urls:
        url=canonical_url(url)
        registered=source_for_url(url)
        same_host=next((s for s in SOURCES if urlsplit(s['url']).hostname==urlsplit(url).hostname),None)
        if same_host and same_host['purpose']!='lead_evidence':
            raise ValueError('Reference-only source hosts cannot be used as lead evidence')
        seeds.append(dict(registered) if registered else {'url':url,'source_class':same_host['source_class'] if same_host else request.additional_source_class,
                     'connector':'auto','evidence_scope':'records','follow_links':True})
    return seeds


def result_template(run_id, request):
    return {'run_id':run_id,'started_at':datetime.now(timezone.utc).isoformat(),'mode':'live','is_synthetic':False,
            'static_prefix_sha256':PREFIX_HASH,'request':request.model_dump(mode='json'),
            'pipeline':['receive_prompt','fetch_clean','extract_signals','filter_score','llm_reasoning','customer_review','write_to_crm_or_clarify'],
            'stages':{},'documents':[],'signals':[],'errors':[],'source_decisions':[],
            'not_executed':['filter_score','llm_reasoning','customer_review','write_to_crm'],
            'limitations':['Bounded public retrieval is not exhaustive web search.',
                           'Semantic review checks source support, not legal truth or buying intent.',
                           'Form POST, login, paywalls and opaque dashboard APIs are not bypassed.'],
            'duplicates':[],'connector_observations':[],'llm_usage':[],'tool_trace':[]}


async def execute_live(request, llm=None):
    if not request.permissions_confirmed:
        raise ValueError('Source terms/access permission review is required')
    run_id=request.resume_run_id or str(uuid4())
    folder=run_folder(run_id)
    with run_lock(folder):
        checkpoint=folder/'checkpoint.json'
        if request.resume_run_id:
            state=json.loads(checkpoint.read_text(encoding='utf-8'))
            if state.get('version')!=1 or state['result']['static_prefix_sha256']!=PREFIX_HASH:
                raise ValueError('Checkpoint version/context differs; start a new run to avoid mixing extraction contracts')
            old=RunRequest.model_validate(state['result']['request'])
            mutable={'budget_usd','max_pages','max_chunks','resume_run_id','permissions_confirmed'}
            if {k:v for k,v in old.model_dump().items() if k not in mutable}!={k:v for k,v in request.model_dump().items() if k not in mutable}:
                raise ValueError('Resume must preserve source scope and processing settings')
        else:
            state={'version':1,'result':result_template(run_id,request),'queue':None,'seen':[],
                   'attempted':0,'chunks':None,'abstentions':[],'rejections':[],'review_done':[]}
        output=state['result']; output['request']=request.model_dump(mode='json')
        output.pop('stop_reason',None)
        output['checkpoint_path']=str(checkpoint)
        client=llm or KimiClient(EvaluationBudget(folder/'budget.json',request.budget_usd,settle=True),max_output_tokens=4000)
        output['model']=client.model
        runner=ToolRunner(); runner.trace=output['tool_trace']
        previous_usage=output['llm_usage'][:]; usage_start=len(client.usage)
        started=time.monotonic()

        def persist():
            output['llm_usage']=previous_usage+client.usage[usage_start:]
            if getattr(client,'budget',None):
                output['cost']=dict(client.budget.data)
            save_state(checkpoint,state)

        def timed_out():
            return time.monotonic()-started>840

        try:
            if 'receive_prompt' not in output['stages']:
                plan=await client.generate('receive_prompt',{'prompt':request.prompt,'additional_urls':request.additional_urls,
                    'requested_source_ids':request.source_ids,'max_pages':request.max_pages,'source_query':request.source_query,
                    'current_time':datetime.now(timezone.utc).isoformat()},Plan)
                output['stages']['receive_prompt']={'status':'completed','executor':'kimi','result':plan.model_dump(mode='json')}
                persist()
            plan=Plan.model_validate(output['stages']['receive_prompt']['result'])
            if plan.status=='needs_clarification' or plan.questions:
                output.update(status='needs_clarification')
                output['stages'].update(fetch_clean={'status':'not_started'},extract_signals={'status':'not_started'})
                persist(); return output
            seeds=selected_seeds(request,plan)
            if not seeds:
                raise ValueError('No fetchable sources. Add a permitted public URL.')
            if state['queue'] is None:
                state['queue']=[{**s,'depth':0} for s in seeds]
                output['source_decisions']=[{'source_id':s.get('id'),'url':s['url'],'connector':s['connector'],
                    'evidence_scope':s['evidence_scope'],'status':'scheduled', 'access_note':s.get('access_note')} for s in seeds]
                persist()
            fetcher=PublicFetcher([urlsplit(s['url']).hostname for s in seeds],True)
            runner.fetcher=fetcher
            seen=set(state['seen'])
            try:
                while state['queue'] and state['attempted']<request.max_pages and not timed_out():
                    source=state['queue'][0]
                    url=normalized_url(canonical_url(source['url']))
                    if url in seen:
                        state['queue'].pop(0); continue
                    try:
                        raw=await runner.call('fetch_public_page',url=url)
                        observed={'targets':[],'limitations':[],'observed_table_count':0}
                        if raw.get('format')=='pdf':
                            clean=await runner.call('clean_pdf',pdf_base64=raw.pop('pdf_base64'),url=raw['url'],ocr=request.ocr)
                        else:
                            html=raw.pop('html')
                            clean=await runner.call('clean_html',html=html,url=raw['url'])
                            observed=await runner.call('inspect_public_source',html=html,url=raw['url'],
                                                      source_id=source.get('id'),query=request.source_query)
                        if observed['limitations'] or observed['targets']:
                            output['connector_observations'].append({'url':url,**observed})
                        scope=source['evidence_scope']
                        if source.get('connector')=='manual':
                            scope='context_only'
                            if not observed['targets']:
                                output['errors'].append({'stage':'fetch_clean','url':url,'message':'requires_connector: no usable same-host public dashboard document found'})
                        if 'missing_record_table' in clean.get('quality_warnings',[]):
                            scope='context_only'
                            output['errors'].append({'stage':'fetch_clean','url':url,'message':'Page reports Table not found; retained as context only.'})
                        elif len(clean['text'])<40 and not observed['targets']:
                            raise ValueError('Too little usable text; no executable public connector target found')
                        if source.get('target_kind')=='public_get_search':
                            scope='records' if observed['observed_table_count'] else 'context_only'
                        identity=hashlib.sha256((source['source_class']+'|'+scope).encode()).hexdigest()[:8]
                        doc={**raw,**clean,'document_id':'doc_'+raw['content_hash'][:16]+'_'+identity,
                             'source_id':source.get('id'),'source_class':source['source_class'],
                             'evidence_scope':scope,'synthetic':False,'source_note':source.get('note'),
                             'depth':source['depth']}
                        duplicate,near=duplicate_of(doc,output['documents'])
                        if duplicate:
                            duplicate.setdefault('alternate_sources',[]).append({'url':raw['url'],'requested_url':url,'source_id':source.get('id')})
                            output['duplicates'].append({'url':url,'duplicate_of':duplicate['document_id'],'reason':'normalized_body_exact'})
                        else:
                            doc['near_duplicate_candidates']=near
                            output['documents'].append(doc)
                        if source['depth']<request.max_depth and (source.get('follow_links',True) or observed['targets']):
                            link_limit=request.links_per_page
                            links=await runner.call('discover_links',links=clean['links'],keywords=plan.keywords[:20],limit=link_limit) if source.get('follow_links',True) else []
                            direct=[t['url'] for t in observed['targets']]
                            combined=list(dict.fromkeys(direct+links))[:link_limit]
                            for link in combined:
                                registered=source_for_url(link)
                                if registered and (registered['purpose']!='lead_evidence' or registered.get('connector')=='manual'):
                                    continue
                                if normalized_url(link) in seen or any(normalized_url(s['url'])==normalized_url(link) for s in state['queue']):
                                    continue
                                target=next((t for t in observed['targets'] if t['url']==link),{})
                                state['queue'].append({**source,**(registered or {}),'url':link,'depth':source['depth']+1,
                                    'connector':registered['connector'] if registered else 'auto',
                                    'target_kind':target.get('kind'),'follow_links':registered.get('follow_links',True) if registered else True,
                                    'evidence_scope':registered['evidence_scope'] if registered else 'records'})
                    except Exception as exc:
                        output['errors'].append({'stage':'fetch_clean','url':url,'message':str(exc) or type(exc).__name__})
                    state['attempted']+=1; seen.add(url); state['seen']=list(seen)
                    for decision in output['source_decisions']:
                        if normalized_url(decision['url'])==url:
                            failures=[e for e in output['errors'] if e.get('url')==url]
                            decision['status']='requires_connector' if any('requires_connector' in e['message'] for e in failures) else 'failed' if failures else 'fetched'
                    state['queue'].pop(0)
                    persist()
            finally:
                await fetcher.close()
            output['unattempted_urls']=[s['url'] for s in state['queue']]
            output['stages']['fetch_clean']={'status':'partial' if output['errors'] or state['queue'] else 'completed',
                'executor':'deterministic_tools','attempted_pages':state['attempted'],'document_count':len(output['documents'])}
            # New documents can be added when a resumed run receives a larger fetch budget.
            state['chunks']=state['chunks'] or []
            known={c['document_id'] for c in state['chunks']}
            for doc in output['documents']:
                if doc.get('evidence_scope')=='context_only':
                    if not any(a['document_id']==doc['document_id'] for a in state['abstentions']):
                        state['abstentions'].append({'document_id':doc['document_id'],'reason':'Context-only page; no company-level extraction.'})
                elif doc['document_id'] not in known:
                    state['chunks'].extend(chunk_document(doc))
            persist()
            calls=0
            for chunk in state['chunks']:
                if chunk['status'] not in ('pending','error','incomplete'):
                    continue
                if calls>=request.max_chunks or timed_out():
                    output['stop_reason']='chunk_or_time_limit'; break
                doc=next(d for d in output['documents'] if d['document_id']==chunk['document_id'])
                start,end=chunk['start'],chunk['end']
                payload_doc={k:v for k,v in doc.items() if k not in ('text','links','tables')}
                payload_doc['text']=doc['text'][start:end]
                payload_doc['chunk_span']={'start':start,'end':end}
                payload_doc['preceding_context']=doc['text'][max(0,start-800):start]
                pages={p['page'] for p in doc.get('page_spans',[]) if p['start']<end and p['end']>start}
                payload_doc['table_context']=[t for t in doc.get('tables',[]) if t['page'] in pages][:3]
                try:
                    calls+=1
                    extracted=await client.generate('extract_signals',{'research_request':request.prompt,
                        'plan':plan.model_dump(mode='json'),'document':payload_doc},ChunkExtraction)
                    complete=getattr(extracted,'coverage_complete',True)
                    checked=await runner.call('verify_evidence',signals=[s.model_dump() for s in extracted.signals],document={**doc,'quote_span':{'start':start,'end':end}})
                    state['rejections'].extend(checked['rejected'])
                    for signal in checked['accepted']:
                        if signal['excerpt'] not in payload_doc['text']:
                            state['rejections'].append({'document_id':doc['document_id'],'reason':'quote_outside_target_chunk'})
                            continue
                        sid=hashlib.sha256((doc['document_id']+'\0'+str(signal['organisation'])+'\0'+signal['excerpt']).encode()).hexdigest()[:20]
                        if any(s['signal_id']==sid for s in output['signals']):
                            continue
                        signal['signal_id']=sid
                        signal['chunk_id']=chunk['chunk_id']
                        signal['rule_checks']=rule_checks(signal,doc)
                        signal['eligible_for_scoring']=False if signal.get('evidence_level')=='discovery_only' else None
                        signal['semantic_review']={'status':'not_requested' if request.semantic_review=='off' else 'pending' if risky(signal) else 'not_required'}
                        output['signals'].append(signal)
                    if extracted.abstention_reason:
                        state['abstentions'].append({'document_id':doc['document_id'],'chunk_id':chunk['chunk_id'],'reason':extracted.abstention_reason})
                    chunk['status']='completed' if complete else 'incomplete'
                    chunk['coverage_note']=getattr(extracted,'coverage_note',None)
                    if complete:
                        resolved=[e for e in output['errors'] if e.get('chunk_id')==chunk['chunk_id']]
                        output.setdefault('resolved_errors',[]).extend(resolved)
                        output['errors']=[e for e in output['errors'] if e not in resolved]
                    if not complete:
                        children=split_chunk(chunk,doc['text'])
                        if children:
                            chunk['status']='split'; state['chunks'].extend(children)
                except BudgetExceeded:
                    raise
                except Exception as exc:
                    children=split_chunk(chunk,doc['text']) if 'incomplete JSON' in str(exc) else []
                    if children:
                        chunk['status']='split'; state['chunks'].extend(children)
                    else:
                        chunk['status']='error'; chunk['error']=str(exc)
                        output['errors'].append({'stage':'extract_signals','chunk_id':chunk['chunk_id'],'message':str(exc)})
                persist()
            if request.semantic_review=='risk':
                pending=[s for s in output['signals'] if s['semantic_review']['status']=='pending']
                for offset in range(0,len(pending),5):
                    if timed_out():
                        output['stop_reason']='time_limit'; break
                    batch=pending[offset:offset+5]
                    evidence=[]
                    for signal in batch:
                        doc=next(d for d in output['documents'] if d['document_id']==signal['document_id'])
                        evidence.append({'signal':signal,'context':doc['text'][max(0,signal['evidence_start']-400):signal['evidence_end']+400]})
                    reviewed=await client.generate('review_signals',{'items':evidence},SemanticReview)
                    for signal in batch:
                        verdict=next((r for r in reviewed.reviews if r.signal_id==signal['signal_id']),None)
                        signal['semantic_review']=({'status':verdict.verdict,'issues':verdict.issues} if verdict else {'status':'uncertain','issues':['Missing review response']})
                        signal['eligible_for_scoring']=bool(verdict and verdict.verdict=='supported' and not signal['rule_checks'] and signal.get('evidence_level')!='discovery_only')
                    persist()
        except BudgetExceeded as exc:
            output['stop_reason']='budget_limit'
            output['budget_message']=str(exc)
        except asyncio.CancelledError:
            output['stop_reason']='interrupted'; persist(); raise
        except Exception as exc:
            output['errors'].append({'stage':'execution','message':str(exc) or type(exc).__name__})
        finally:
            finish(state,output)
            persist()
        return output


def risky(signal):
    return signal.get('trigger_type') in ('enforcement','incident','certification') or signal.get('evidence_text_method')=='local_ocr' or bool(signal.get('rule_checks'))


def rule_checks(signal, document=None):
    import re
    issues=[]
    if signal.get('evidence_level')=='discovery_only':
        if signal.get('buyer_role_hypothesis') is not None:
            issues.append('Discovery-only evidence cannot establish a buyer role')
        if signal.get('recommended_next_step')!='research_more':
            issues.append('Discovery-only evidence must request further research')
        if 'no_direct_workflow_evidence' not in signal.get('ambiguity_flags',[]):
            issues.append('Missing no_direct_workflow_evidence flag')
    if document:
        claims=' '.join(str(signal.get(k) or '') for k in ('need_hypothesis','urgency','safety_topic'))
        for token in re.findall(r'\bISO\s*\d{4,5}\b|\b(?:19|20)\d{2}\b|\b(?:RM|USD)\s*[\d,]+(?:\.\d+)?',claims,re.I):
            if re.sub(r'\s+','',token).lower() not in re.sub(r'\s+','',document['text']).lower():
                issues.append('Claim value not located in supplied document: '+token)
    return issues


def finish(state,output):
    chunks=state.get('chunks') or []
    leaf=[c for c in chunks if c['status']!='split']
    remaining=[c for c in leaf if c['status']!='completed']
    truncated=any(d.get('text_truncated') or d.get('unreadable_pages') or any('insufficient text' in w or 'dependencies are missing' in w for w in d.get('quality_warnings',[])) for d in output['documents'])
    eligible=sum(d.get('evidence_scope')!='context_only' for d in output['documents'])
    pending_review=any(s.get('semantic_review',{}).get('status')=='pending' for s in output['signals'])
    output['extraction_complete']=not remaining and not truncated and state.get('chunks') is not None and not state.get('queue')
    output['coverage']={'processed_chunks':sum(c['status']=='completed' for c in leaf),'remaining_chunks':len(remaining),
        'total_leaf_chunks':len(leaf),'truncated_documents':truncated,'retrieval_complete':not state.get('queue'),
        'scope_complete':not state.get('queue') and not any(e['stage']=='fetch_clean' for e in output['errors']),
        'pending_review':pending_review,'completion_definition':'All retained eligible text chunks processed; not a guarantee of model recall or whole-site coverage.'}
    output['remaining_chunks']=[{k:c[k] for k in ('chunk_id','document_id','start','end','status')} for c in remaining]
    output['signals'].sort(key=lambda s:(s['document_id'],s['evidence_start'],s['signal_id']))
    output['stages'].setdefault('fetch_clean',{'status':'not_started'})
    output['stages']['extract_signals']={'status':'not_started' if state.get('chunks') is None else 'skipped' if not eligible else 'partial' if remaining or truncated or state['rejections'] else 'completed',
        'executor':'kimi_with_evidence_validation' if eligible else 'not_run','eligible_document_count':eligible,
        'context_only_document_count':len(output['documents'])-eligible,'signal_count':len(output['signals']),
        'abstentions':state['abstentions'],'rejected_signals':state['rejections']}
    if output.get('status')!='needs_clarification':
        partial=bool(output['errors'] or remaining or truncated or state.get('queue') or output.get('stop_reason') or pending_review or state['rejections'])
        output['status']='partial' if partial and (output['documents'] or output.get('stop_reason')) else 'failed' if partial else 'completed'
    output['finished_at']=datetime.now(timezone.utc).isoformat()
