"""Live control → native codec → gateway check; requires the machine test slot."""
import argparse
import collections
import fcntl
import json
import os
import socket
import subprocess
import sys
import tempfile
import threading
import time
from pathlib import Path

import httpx
import pytest
import uvicorn
from dotenv import dotenv_values

parser=argparse.ArgumentParser(description=__doc__)
parser.add_argument('--env',type=Path,required=True)
parser.add_argument('--binary',type=Path,required=True)
parser.add_argument('--output',type=Path,required=True)
args=parser.parse_args()
repo=Path(__file__).resolve().parents[2];sys.path.insert(0,str(repo/'backend'));sys.path.insert(0,str(repo/'spikes/native-reasoning'))
from probe import harness, shape
from sage.driver.opencode import OpenCodeClient
from sage.gateway.client import OpenAICompatibleClient
from sage.gateway.events import StreamEvents
from sage.gateway.protocol import Protocol
from sage.orchestrator import app as appmod
from sage.resources.provider import DominoResourceProvider
from tests.test_native_model_controls import active, running

runtime=Path(tempfile.mkdtemp(prefix='sage-479-production-'));mp=pytest.MonkeyPatch()
_,orch,_=running.__wrapped__(runtime,mp)
env=dotenv_values(args.env)
live_resources=DominoResourceProvider(env['GATEWAY_BASE_URL'],lambda:env['GATEWAY_API_KEY'])
orch._resources.reasoning_capability=live_resources.reasoning_capability
orch._resources.list_llm_aliases=live_resources.list_llm_aliases
records=[]
class Gateway(OpenAICompatibleClient):
 def route(self,request,labels,*,protocol=Protocol.CHAT,cancel=None):
  row={'model':request['model'],'protocol':str(protocol),'phase':labels.phase,'reason':labels.route_reason,'request_shape':shape(request),'tools':len(request.get('tools',[])), 'settings':{k:request[k] for k in ('thinking','output_config','reasoning','reasoning_effort') if k in request},'response_shape':{},'status':'started'};records.append(row)
  parser=StreamEvents(protocol);counts=collections.Counter()
  try:
   for chunk in super().route(request,labels,protocol=protocol,cancel=cancel):
    for frame in parser.feed(chunk):
     for line in frame.splitlines():
      if line.startswith(b'data:'):
       try: counts.update(shape(json.loads(line[5:])))
       except ValueError:pass
    yield chunk
   parser.finish();row.update(status='complete',response_shape=dict(counts),input_tokens=parser.input_tokens,output_tokens=parser.output_tokens,called_tools=sorted(parser.tool_names))
  except Exception as error:row['status']=type(error).__name__;raise
  finally:args.output.with_suffix('.progress.json').write_text(json.dumps(records,indent=2))
gateway=Gateway(env['GATEWAY_BASE_URL'],lambda:env['GATEWAY_API_KEY'],domino_tags=True)
orch._project.shim._gateway=gateway
with socket.socket() as sock:sock.bind(('127.0.0.1',0));port=sock.getsockname()[1]
server=uvicorn.Server(uvicorn.Config(appmod.control_app,host='127.0.0.1',port=port,lifespan='off',log_level='error'))
thread=threading.Thread(target=server.run,daemon=True);thread.start()
while not server.started:time.sleep(.1)
(runtime/'NOTE.txt').write_text('The marker is cobalt.\n');(runtime/'OTHER.txt').write_text('The second marker is amber.\n')
config={'$schema':'https://opencode.ai/config.json','plugin':[],'mcp':{},'provider':{'sage-gateway':{'npm':(repo/'backend/sage/driver/provider.mjs').as_uri(),'options':{'baseURL':f'http://127.0.0.1:{port}/v1','apiKey':'local-only'},'models':{'gpt-5.4':{'name':'Sage routed','reasoning':True,'limit':json.loads((repo/'opencode.json').read_text())['provider']['sage-gateway']['models']['gpt-5.4']['limit']}}}},'model':'sage-gateway/gpt-5.4','small_model':'sage-gateway/gpt-5.4','permission':{'*':'deny','read':'allow','edit':'allow','bash':'allow'},'agent':{'probe':{'mode':'primary','prompt':'Use only the named synthetic files. Follow each requested tool action in order. Keep the final answer short.'}}}
report={'requests':records,'turns':[],'gateway':env['GATEWAY_BASE_URL'].rstrip('/').removesuffix('/v1'),'source_revision':subprocess.check_output(['git','rev-parse','HEAD'],cwd=repo,text=True).strip()}
local=httpx.Client(base_url=f'http://127.0.0.1:{port}',timeout=30)
def prompt(client,sid,text,label,chat=False,parent=None):
 before=len(records)
 with active(orch,chat=chat):
  orch._project.active_session_id=parent or sid
  response=client.post(f'/session/{sid}/message',params={'directory':str(runtime)},json={'model':{'providerID':'sage-gateway','modelID':'gpt-5.4'},'agent':'probe','parts':[{'type':'text','text':text}]})
 data=response.json();answer=''.join(p.get('text','') for p in data.get('parts',[]) if p.get('type')=='text')
 row={'label':label,'status':response.status_code,'error':(data.get('info',{}).get('error') or {}).get('name'),'finish':data.get('info',{}).get('finish'),'marker':any(x in answer.lower() for x in ('cobalt','amber')),'models':[r['model'] for r in records[before:]],'protocols':[r['protocol'] for r in records[before:]]};report['turns'].append(row);print(json.dumps(row),flush=True)
 args.output.write_text(json.dumps(report,indent=2))
 if row['error']:raise RuntimeError('Harness failed: '+row['error'])
try:
 with open('/tmp/sage-opencode-test.lock','w') as lock:
  fcntl.flock(lock,fcntl.LOCK_EX)
  binary=args.binary
  with harness(binary,runtime,config) as client:
   orch._oc_client=OpenCodeClient(str(client.base_url).rstrip('/'))
   for model,effort in ([] if os.environ.get('AUTO_ONLY') else [('Opus-4.8','high'),('gpt-5.4','low'),('domino/gemini-3.7-flash','low')]):
    created=client.post('/api/session',json={'location':{'directory':str(runtime)}}).json();sid=created.get('data',created)['id']
    response=local.post('/api/project/model',json={'chat_model':model,'reasoning_effort':effort});response.raise_for_status()
    orch._oc_client.note_session_dir(sid,str(runtime))
    prompt(client,sid,'Before reading, count integers from 1 to 100 divisible by 3 or 7 but not both. Then read NOTE.txt and OTHER.txt in parallel and tell me both markers.',model,chat=True)
    if model == 'gpt-5.4':
     before=len(records)
     chat_token=orch._project.control.arm_chat('thread_test')
     try:
      with mp.context() as context:
       context.setattr('sage.orchestrator.chat_compact.should_compact',lambda *_:True)
       orch._maybe_compact_chat(orch._oc_client,sid,orch._project)
     finally:orch._project.control.disarm_chat(chat_token)
     report['compaction']={'requests':len(records)-before,'gateway_error':bool(orch._project.last_gateway_error)}
     prompt(client,sid,'After compaction read NOTE.txt and tell me both markers.', 'production-compaction',chat=True)
   response=local.post('/api/project/model',json={'mode':'auto','pick':None,'catalog':{'plan':{'model':'Opus-4.8','effort':'high'},'implement':{'model':'gpt-5.4','effort':'low'},'ask':{'model':'Opus-4.8','effort':None}}});response.raise_for_status()
   created=client.post('/api/session',json={'location':{'directory':str(runtime)}}).json();sid=created.get('data',created)['id']
   prompt(client,sid,'Perform this harmless routing check in order: First read NOTE.txt. Next use the write tool (not bash) to write CHECK.txt with the marker cobalt. In a later tool call after the write finishes, run bash command: printf "SyntaxError: synthetic routing check\\n". This printed error is intentional. Finally read CHECK.txt and tell me its marker. Do not combine the write and bash in the same tool batch. Do not fix anything.', 'auto-and-rescue')
   orch._oc_client.note_session_dir(sid,str(runtime))
   child=client.post('/session',params={'directory':str(runtime)},json={'parentID':sid,'title':'Synthetic child'});child.raise_for_status();child=child.json();child=child.get('data',child)['id']
   prompt(client,child,'Read NOTE.txt and tell me its marker.', 'verified-child-session',parent=sid)
except (httpx.HTTPError, OSError, ValueError, KeyError, RuntimeError, TimeoutError) as error:report['exception']=type(error).__name__;print('FAILED',type(error).__name__,flush=True)
finally:
 server.should_exit=True;thread.join(10);gateway.close();local.close();mp.undo()
 args.output.write_text(json.dumps(report,indent=2))

passed=(not report.get('exception') and all(t['marker'] and not t['error'] for t in report['turns']) and all(r['status']=='complete' for r in records) and any('gpt-5.4' in t['models'] and t['models'][-1]=='Opus-4.8' for t in report['turns'] if t['label']=='auto-and-rescue'))
raise SystemExit(0 if passed else 1)
