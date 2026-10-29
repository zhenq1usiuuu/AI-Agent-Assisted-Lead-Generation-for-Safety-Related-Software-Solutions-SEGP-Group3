import hashlib
from datetime import datetime, timezone
from urllib.parse import urlsplit
from uuid import uuid4
from .context import PREFIX_HASH, SOURCES
from .models import Plan, Extraction
from .tools import PublicFetcher, ToolRunner, canonical_url
from .llm import KimiClient
from .sources import SOURCE_BY_ID, source_for_url

DEMO_HTML='''<html lang="en"><head><title>Synthetic safety inspection example</title>
<meta property="article:published_time" content="2026-10-01"></head><body><nav>Menu</nav>
<main><h1>Demo Manufacturing Sdn Bhd</h1><p>Demo Manufacturing Sdn Bhd is piloting digital safety inspections and corrective-action tracking at its Malaysian factory.</p>
<p>The HSE team is reviewing inspection evidence and follow-up records.</p></main></body></html>'''

async def execute(request, llm=None):
    if request.mode == "live":
        from .engine import execute_live
        return await execute_live(request, llm)
    run_id=str(uuid4())
    started=datetime.now(timezone.utc).isoformat()
    runner=ToolRunner()
    output={'run_id':run_id,'started_at':started,'mode':request.mode,
            'is_synthetic':request.mode=='demo','static_prefix_sha256':PREFIX_HASH,
            'pipeline':['receive_prompt','fetch_clean','extract_signals','filter_score','llm_reasoning','customer_review','write_to_crm_or_clarify'],
            'stages':{},'documents':[],'signals':[],'errors':[],
            'not_executed':['filter_score','llm_reasoning','customer_review','write_to_crm'],
            'limitations':['This demo uses a fixed synthetic fixture and performs no network retrieval.',
                            'Live mode has bounded retrieval, chunking and optional local OCR; the demo does not exercise these features.',
                            'Confidence is uncalibrated. Evidence validation checks citations, not every semantic inference.']}
    output['source_decisions'] = []
    output['request'] = request.model_dump(mode='json')
    if request.mode=='demo':
        plan=Plan(status='ready',workflow='safety inspection and corrective-action tracking',country='Malaysia',
                  sectors=['manufacturing'],keywords=['inspection','corrective'],seed_ids=[],questions=[],
                  assumptions=['Synthetic fixed fixture; user prompt is not interpreted by an LLM in demo mode.'])
        output['stages']['receive_prompt']={'status':'completed','executor':'synthetic_fixture','result':plan.model_dump()}
        clean=await runner.call('clean_html',html=DEMO_HTML,url='https://example.com/synthetic-safety')
        output['documents']=[{**clean,'document_id':'doc_demo','url':'https://example.com/synthetic-safety',
            'requested_url':'https://example.com/synthetic-safety','captured_at':started,'source_class':'company_owned',
            'content_hash':hashlib.sha256(DEMO_HTML.encode()).hexdigest(),'http_status':None,
            'robots_decision':'not_applicable_synthetic','terms_decision':'not_applicable_synthetic','synthetic':True}]
        output['stages']['fetch_clean']={'status':'completed','executor':'local_tools_on_fixture','document_count':1}
    rejections=[]
    abstentions=[]
    extraction_errors=0
    for doc in output['documents']:
        try:
            if doc.get('evidence_scope') == 'context_only':
                abstentions.append({'document_id':doc['document_id'],
                    'reason':'Discovery/landing page only. No company-level signal extraction; use an actual public record or article.'})
                continue
            if request.mode=='demo':
                extraction=Extraction(signals=[{'document_id':doc['document_id'],'organisation':'Demo Manufacturing Sdn Bhd',
                    'organisation_specific':True,'sector':'manufacturing','safety_topic':'safety inspection',
                    'trigger_type':'initiative','workflow':plan.workflow,'urgency':None,
                    'excerpt':'Demo Manufacturing Sdn Bhd is piloting digital safety inspections and corrective-action tracking at its Malaysian factory.',
                    'need_hypothesis':'Synthetic example: inspection evidence and corrective-action records may benefit from a shared workflow.',
                    'buyer_role_hypothesis':'HSE manager (hypothesis only)','confidence':0.9,
                    'ambiguity_flags':['synthetic_example'],'recommended_next_step':'human_review'}],abstention_reason=None)
            else:
                extraction=await llm.generate('extract_signals',{'research_request':request.prompt,
                    'plan':plan.model_dump(),'document':{k:v for k,v in doc.items() if k!='links'}},Extraction)
            checked=await runner.call('verify_evidence',signals=[s.model_dump() for s in extraction.signals],document=doc)
            output['signals'].extend(checked['accepted'])
            rejections.extend(checked['rejected'])
            if extraction.abstention_reason:
                abstentions.append({'document_id':doc['document_id'],'reason':extraction.abstention_reason})
        except Exception as exc:
            extraction_errors+=1
            output['errors'].append({'stage':'extract_signals','document_id':doc['document_id'],'message':str(exc)[:400]})
    extractable_count=sum(doc.get('evidence_scope') != 'context_only' for doc in output['documents'])
    output['stages']['extract_signals']={'status':'not_started' if not output['documents'] else 'skipped' if not extractable_count else 'failed' if extraction_errors==extractable_count else 'partial' if extraction_errors or rejections else 'completed',
        'executor':'synthetic_fixture' if request.mode=='demo' else 'not_run' if not extractable_count else 'kimi_with_evidence_validation',
        'eligible_document_count':extractable_count,
        'context_only_document_count':len(output['documents'])-extractable_count,
        'signal_count':len(output['signals']),'rejected_signals':rejections,'abstentions':abstentions}
    output['status']='completed' if not output['errors'] and not rejections else 'partial' if output['signals'] else 'failed'
    output['tool_trace']=runner.trace
    output['llm_usage']=llm.usage if llm else []
    output['finished_at']=datetime.now(timezone.utc).isoformat()
    return output
