import asyncio
import copy
import json
import sqlite3
import httpx
import pytest
from app.contacts import validate_contact, review_contacts, discover_contacts
from app.crm import write_review
from app.twenty import build_records, synchronize
from test_workflow_crm import review_record


def contact():
    text='Company: Alice Smith, Safety Manager, alice@example.com, +60 12 345 6789'
    return dict(account_key='company',kind='person',name='Alice Smith',job_title='Safety Manager',work_email='alice@example.com',phone='+60 12 345 6789',source_url='https://example.com/team',captured_at='2026-11-13T00:00:00+00:00',source_text=text,excerpt=text,company_excerpt='Company')


def approved():
    candidate=validate_contact(contact(),'Company')
    answers=iter(['y','Company website; approved for research storage','unknown'])
    return review_contacts({'candidates':[candidate]},'Tester',lambda _:next(answers),lambda _:None)


def test_contact_requires_verbatim_evidence():
    bad=contact();bad['work_email']='invented@example.com'
    with pytest.raises(ValueError):validate_contact(bad,'Company')
    with pytest.raises(ValueError):validate_contact(contact(),'Other company')


def test_contact_migration_and_unknown_permission(tmp_path):
    db=tmp_path/'crm.db';r=review_record()
    write_review(r,db)
    r['contact_reviews']=approved()
    assert r['contact_reviews'][0]['do_not_contact'] is True
    assert write_review(r,db)==(0,1)
    assert write_review(r,db)==(0,1)
    with sqlite3.connect(db) as c:
        assert c.execute('select count(*) from leads').fetchone()[0]==1
        assert c.execute('select name,do_not_contact from contacts').fetchone()==('Alice Smith',1)
        assert c.execute('select count(*) from contact_evidence').fetchone()[0]==1
        assert c.execute('pragma foreign_key_check').fetchall()==[]


def test_missing_consent_reference_does_not_allow_contact():
    answers=iter(['y','Research storage','consented',''])
    row=review_contacts({'candidates':[validate_contact(contact(),'Company')]},'Tester',lambda _:next(answers),lambda _:None)[0]
    assert row['contact_permission']=='unknown' and row['do_not_contact']


def test_unapproved_or_unsupported_contact_rejected(tmp_path):
    r=review_record();r['contact_reviews']=approved();r['contact_reviews'][0]['do_not_contact']=False
    with pytest.raises(ValueError):write_review(r,tmp_path/'bad.db')
    assert not (tmp_path/'bad.db').exists()


def test_twenty_mock_create_then_update_and_links(tmp_path):
    from pathlib import Path
    r=review_record();r['contact_reviews']=approved()
    mapping=json.loads(Path('config/twenty.mapping.example.json').read_text())
    records=build_records(r,mapping)
    company_id=records[0]['payload']['id']
    assert next(x for x in records if x['kind']=='person')['payload']['companyId']==company_id
    stored={};methods=[]
    def handle(req):
        methods.append(req.method)
        key=req.url.path.rsplit('/',1)[-1]
        if req.method=='GET':return httpx.Response(200 if key in stored else 404,json={'data':stored.get(key)})
        body=json.loads(req.content)
        if req.method=='POST':key=body['id']
        stored[key]={**body,'id':key}
        return httpx.Response(201 if req.method=='POST' else 200,json={'data':{'record':stored[key]}})
    with httpx.Client(transport=httpx.MockTransport(handle)) as client:
        synchronize(records,'https://crm.example.com','fake',tmp_path/'receipt.json',client)
        synchronize(records,'https://crm.example.com','fake',tmp_path/'receipt2.json',client)
    assert methods.count('POST')==len(records) and methods.count('PATCH')==len(records)
    assert len(stored)==len(records)


def test_public_company_route_fetch_uses_explicit_host(monkeypatch):
    import app.contacts as module
    class Fetcher:
        def __init__(self,hosts,permission):
            assert hosts==['example.com'] and permission is True
        async def fetch_public_page(self,url):
            return {'format':'html','html':'<main>Company Contact us info@example.com</main>', 'url':url,'captured_at':'2026-11-13T00:00:00+00:00'}
        async def close(self):pass
    monkeypatch.setattr(module,'PublicFetcher',Fetcher)
    result=asyncio.run(discover_contacts({'sources':[{'account_key':'company','url':'https://example.com/contact'}]},[{'account_key':'company','display_name':'Company'}]))
    assert len(result['candidates'])==1
    assert result['candidates'][0]['kind']=='organisation_route'
    assert result['candidates'][0]['name'] is None


def test_twenty_failure_leaves_uncertain_receipt(tmp_path):
    def handle(req):return httpx.Response(500,json={})
    path=tmp_path/'failed.json'
    with httpx.Client(transport=httpx.MockTransport(handle)) as client:
        with pytest.raises(ValueError):
            synchronize([{'kind':'company','resource':'companies','payload':{'id':'id'}}],'https://crm.example.com','fake',path,client)
    assert json.loads(path.read_text())['status']=='failed_or_uncertain'
