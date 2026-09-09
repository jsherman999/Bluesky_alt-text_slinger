"""Local-only browser regression fixture. No credentials or network calls.
Run: .venv/bin/uvicorn tests.ui_generation_fixture:app --host 127.0.0.1 --port 8001
Point a separate Vite instance at VITE_API_BASE=http://127.0.0.1:8001.
"""
import json
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse
app = FastAPI()
app.add_middleware(CORSMiddleware, allow_origins=['http://127.0.0.1:5174'], allow_methods=['*'], allow_headers=['*'])
stopped = False
calls = 0
polls = 0
uri = 'at://did:plc:test/app.bsky.feed.post/test'

@app.post('/api/scan/stream')
def scan():
    data = {'handle':'test.bsky.social','total_posts':1,'total_images':2,'alt_generation_enabled':True,'posts':[
        {'uri':uri,'cid':'test','text':'Regression fixture','created_at':None,'images':[
            {'index':i,'thumb_url':'data:image/gif;base64,R0lGODlhAQABAIAAAAAAAP///ywAAAAAAQABAAACAUwAOw==','fullsize_url':'https://example.test/image','alt':'','generated_alt':'Old suggestion' if i == 0 else None}
            for i in range(2)]}]}
    return StreamingResponse(iter([json.dumps({'type':'result','data':data})+'\n']), media_type='application/x-ndjson')

@app.post('/api/generate/start')
def start():
    global stopped, calls, polls
    stopped, calls, polls = False, 0, 0
    return {'job_id':'fixture','total_items':1}

@app.get('/api/generate/events/fixture')
def events():
    global polls
    polls += 1
    # A transient failure must not unlock regeneration while the batch runs.
    if polls == 2:
        from fastapi.responses import JSONResponse
        return JSONResponse({'detail':'Temporary polling failure'},status_code=503)
    return {'events':[{'seq':1,'type':'complete','stop_requested':True}] if stopped else [],'done':stopped,'stop_requested':stopped,'processed_items':0,'generated_items':0,'total_items':1}

@app.post('/api/generate/stop/fixture')
def stop():
    global stopped
    stopped = True
    return {'status':'stopping'}

@app.post('/api/generate/one')
def regenerate():
    global calls
    calls += 1
    return {'error':'Provider quota reached.', 'generated_alt':None} if calls == 1 else {'generated_alt':'New suggestion','error':None}

@app.post('/api/generate/reset-drafts')
def reset():
    return {'images': [{'uri': uri, 'image_index': 0, 'alt': ''},
                       {'uri': uri, 'image_index': 1, 'alt': 'Saved on Bluesky'}]}
