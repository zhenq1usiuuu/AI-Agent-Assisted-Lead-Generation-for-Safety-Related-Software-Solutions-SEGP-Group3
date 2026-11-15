import asyncio
import os
from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from .context import ROOT, PREFIX, PREFIX_HASH, SOURCES, SCHEMAS
from .models import RunRequest
from .pipeline import execute
from .tools import TOOLS

load_dotenv(ROOT / '.env')
app=FastAPI(title='Safety Signal Lab',version='0.1.0',docs_url=None,redoc_url=None)
app.mount('/static',StaticFiles(directory=ROOT/'static'),name='static')
run_lock=asyncio.Lock()

@app.middleware('http')
async def local_requests(request: Request, call_next):
    # Loopback-only launch plus Host/Origin checks prevents browser-driven local API abuse.
    if request.headers.get('host','').split(':')[0] not in ('127.0.0.1','localhost','testserver'):
        from fastapi.responses import JSONResponse
        return JSONResponse({'detail':'Untrusted host'},status_code=403)
    origin=request.headers.get('origin')
    if origin and origin not in ('http://127.0.0.1:8000','http://localhost:8000'):
        from fastapi.responses import JSONResponse
        return JSONResponse({'detail':'Cross-origin requests are blocked'},status_code=403)
    response=await call_next(request)
    response.headers['X-Content-Type-Options']='nosniff'
    response.headers['Referrer-Policy']='no-referrer'
    response.headers['Content-Security-Policy']="default-src 'self'; script-src 'self'; style-src 'self'; connect-src 'self'; object-src 'none'; frame-ancestors 'none'"
    return response

@app.get('/')
async def index():
    return FileResponse(ROOT/'static/index.html')

@app.get('/api/config')
async def config():
    return {'model':os.getenv('MOONSHOT_MODEL','kimi-k3'),'api_key_configured':bool(os.getenv('MOONSHOT_API_KEY','').strip()),
            'sources':SOURCES,'prefix_hash':PREFIX_HASH}

@app.get('/api/context')
async def context():
    return {'system_prefix':PREFIX,'sha256':PREFIX_HASH,'tools':TOOLS,'schemas':SCHEMAS}

@app.post('/api/run')
async def run(payload:RunRequest):
    if run_lock.locked(): raise HTTPException(409,'A task is already running. Please try again later.')
    if payload.mode=='live' and not payload.permissions_confirmed:
        raise HTTPException(400,'Review the terms and access permissions for the selected sources, then confirm the checkbox. Tools still check robots independently.')
    async with run_lock:
        try:
            async with asyncio.timeout(900):
                return await execute(payload)
        except TimeoutError:
            raise HTTPException(504,'The task reached the 15-minute limit. Reduce the page limit and try again.') from None
        except ValueError as exc:
            raise HTTPException(400,str(exc)) from None
        except Exception:
            raise HTTPException(502,'Task execution failed. Check the network and model response. Secrets and internal responses are not returned to the page.') from None
