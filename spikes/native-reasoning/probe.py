"""Run the pinned OpenCode v1 API through a loopback gateway recorder.

Only synthetic NOTE files are exposed. The recorder keeps structure and counts,
never thinking text, signatures, encrypted state, credentials, or tool results.
Credentials stay in this process, outside OpenCode's environment and config.
"""
from __future__ import annotations

import argparse
import fcntl
import json
import os
import socket
import subprocess
import tempfile
import threading
import time
from collections import Counter
from contextlib import contextmanager
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import httpx
from dotenv import dotenv_values


def shape(value):
    counts = Counter()
    def walk(obj):
        if isinstance(obj, dict):
            if isinstance(obj.get('type'), str):
                counts['type:' + obj['type']] += 1
            for key, val in obj.items():
                if key in ('signature', 'thought_signature', 'encrypted_content') and val:
                    counts[key] += 1
                walk(val)
        elif isinstance(obj, list):
            for val in obj:
                walk(val)
    walk(value)
    return dict(counts)


@contextmanager
def harness(binary, runtime, config):
    cfg = runtime / 'opencode.json'
    cfg.write_text(json.dumps(config))
    env = {k:v for k,v in os.environ.items()
           if not any(s in k for s in ('API_KEY','TOKEN','SECRET','PASSWORD'))}
    env.update(OPENCODE_CONFIG=str(cfg), OPENCODE_DISABLE_AUTOUPDATE='true',
               OPENCODE_CONFIG_DIR=str(runtime/'config'/'opencode'),
               XDG_CONFIG_HOME=str(runtime/'config'), XDG_DATA_HOME=str(runtime/'data'),
               XDG_CACHE_HOME=str(runtime/'cache'), XDG_STATE_HOME=str(runtime/'state'))
    with socket.socket() as sock:
        sock.bind(('127.0.0.1',0)); port=sock.getsockname()[1]
    with (runtime/'opencode.log').open('a') as log:
        proc = subprocess.Popen([str(binary),'serve','--hostname','127.0.0.1','--port',str(port)],
                                cwd=runtime, env=env, stdout=log, stderr=log)
        try:
            with httpx.Client(base_url=f'http://127.0.0.1:{port}',timeout=180) as client:
                deadline=time.monotonic()+75
                while time.monotonic()<deadline:
                    try:
                        if client.get('/global/health',timeout=1).status_code==200:break
                    except httpx.HTTPError:pass
                    if proc.poll() is not None:raise RuntimeError('OpenCode exited during start')
                    time.sleep(.2)
                else:raise TimeoutError('OpenCode startup')
                yield client
        finally:
            proc.terminate()
            try:proc.wait(timeout=10)
            except subprocess.TimeoutExpired:proc.kill();proc.wait(timeout=10)


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--env',type=Path,required=True)
    parser.add_argument('--binary',type=Path,required=True)
    parser.add_argument('--output',type=Path,required=True)
    parser.add_argument('--mux', type=Path)
    parser.add_argument('--transition', action='store_true',
                        help='Change protocol after the first tool request; requires --mux')
    parser.add_argument('--protocol',choices=['messages','responses','chat'],required=True)
    args=parser.parse_args()
    if args.transition and not args.mux:
        parser.error('--transition requires --mux')
    env=dotenv_values(args.env)
    root=env['GATEWAY_BASE_URL'].rstrip('/').removesuffix('/v1')
    protocol=args.protocol
    alias={'messages':'Opus-4.8','responses':'gpt-5.4','chat':'domino/gemini-3.7-flash'}[protocol]
    paths={'messages':'/anthropic/v1/messages','responses':'/v1/responses','chat':'/v1/chat/completions'}
    aliases={'messages':'Opus-4.8','responses':'gpt-5.4','chat':'domino/gemini-3.7-flash'}
    records=[]
    decisions=[]
    class Handler(BaseHTTPRequestHandler):
        def log_message(self,*args):pass
        def do_POST(self):
            if self.path == '/route':
                body=json.loads(self.rfile.read(int(self.headers['Content-Length'])))
                # The sequence is an experiment, not Sage's Auto policy. It forces
                # the same codec boundary that a real Auto/rescue decision needs.
                selected=protocol
                if args.transition and body.get('tools'):
                    selected=('messages','responses')[sum(bool(r['tool_count']) for r in records) % 2]
                decisions.append({'protocol':selected,'prompt_shape':shape(body)})
                result=json.dumps({'protocol':selected,'model':aliases[selected]}).encode()
                self.send_response(200);self.send_header('Content-Type','application/json')
                self.send_header('Content-Length',str(len(result)));self.end_headers();self.wfile.write(result);return
            path=self.path.split('?')[0]
            selected=next((key for key,val in paths.items() if val==path),None)
            if selected is None or (not args.transition and selected!=protocol):
                self.send_error(404);return
            body=json.loads(self.rfile.read(int(self.headers['Content-Length'])))
            if body.get('model')!=aliases[selected]:
                self.send_error(400,'Wrong model');return
            # Probe selects settings here, at the same-model local boundary.
            # This does not prove dynamic provider selection in Sage.
            if selected=='messages':
                body['thinking']={'type':'adaptive'}
                body['output_config']={'effort':'high'}
                body['max_tokens']=4096
                for key in ('temperature','top_p','top_k'):body.pop(key,None)
            elif selected=='responses':
                body['store']=False
                body['include']=['reasoning.encrypted_content']
                body['reasoning']={'effort':'low'}
                body['max_output_tokens']=4096
            else:body['reasoning_effort']='low'
            row={'path':path,'model':aliases[selected],'request_keys':sorted(body),'request_shape':shape(body),
                 'tool_count':len(body.get('tools',[])), 'events':{}, 'usage':None}
            records.append(row)
            started=time.monotonic()
            try:
                headers={'Authorization':'Bearer '+env['GATEWAY_API_KEY'],
                         'X-LLM-Tag-sage-component':'native-probe-479','anthropic-version':'2023-06-01'}
                with httpx.stream('POST',root+path,json=body,headers=headers,timeout=120) as response:
                    row['status']=response.status_code
                    self.send_response(response.status_code)
                    self.send_header('Content-Type',response.headers.get('content-type','application/json'))
                    self.send_header('Connection','close');self.end_headers()
                    event_counts=Counter(); response_counts=Counter()
                    for line in response.iter_lines():
                        self.wfile.write((line+'\n').encode());self.wfile.flush()
                        if line.startswith('data:'):
                            try:data=json.loads(line[5:].strip())
                            except ValueError:continue
                            event_counts[data.get('type','chat')]+=1
                            response_counts.update(shape(data))
                            if data.get('usage'):row['usage']=data['usage']
                            if (data.get('response') or {}).get('usage'):row['usage']=data['response']['usage']
                            if data.get('error'):row['error_type']=str(data['error'].get('type','unknown'))
                    row['events']=dict(event_counts); row['response_shape']=dict(response_counts)
            except (httpx.HTTPError, OSError, ValueError) as error:row['exception']=type(error).__name__
            finally:row['seconds']=round(time.monotonic()-started,3)
            self.close_connection=True
    server=ThreadingHTTPServer(('127.0.0.1',0),Handler)
    thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start()
    runtime=Path(tempfile.mkdtemp(prefix='sage-479-'+protocol+'-'))
    (runtime/'NOTE.txt').write_text('The marker is cobalt.\n')
    (runtime/'OTHER.txt').write_text('The second marker is amber.\n')
    npm={'messages':'@ai-sdk/anthropic','responses':'@ai-sdk/openai','chat':'@ai-sdk/openai-compatible'}[protocol]
    if args.mux:npm=args.mux.resolve().as_uri()
    local=f'http://127.0.0.1:{server.server_port}'
    options={'baseURL':local+('/anthropic/v1' if protocol=='messages' else '/v1'),'apiKey':'local-only'}
    if protocol=='chat':options['name']='google'
    config={'$schema':'https://opencode.ai/config.json','plugin':[],'mcp':{},
            'provider':{'sage-probe':{'npm':npm,'options':options,'models':{alias:{
                'name':alias,'reasoning':True,'limit':{'context':200000,'output':4096}}}}},
            'model':'sage-probe/'+alias,'small_model':'sage-probe/'+alias,
            'permission':{'*':'deny','read':'allow'},
            'agent':{'probe':{'mode':'primary','prompt':'Read only the two named files. Give a short answer.'}}}
    report={'gateway':root,'protocol':protocol,'alias':alias,'runtime':str(runtime),
            'transition':args.transition,'requests':records,'decisions':decisions,'turns':[]}
    try:
        with open('/tmp/sage-opencode-test.lock','w') as lock:
            fcntl.flock(lock,fcntl.LOCK_EX)
            sid=None
            for restart in range(2):
                with harness(args.binary,runtime,config) as client:
                    if sid is None:
                        r=client.post('/api/session',json={'location':{'directory':str(runtime)}});r.raise_for_status()
                        sid=r.json().get('data',r.json())['id']
                    prompts=(['Before reading, work out how many integers from 1 to 100 are divisible by 3 or 7 but not both. Then read NOTE.txt and OTHER.txt using read, in parallel if possible. Tell me the count and both markers.',
                              'Read NOTE.txt again. Is its marker unchanged?'] if restart==0 else
                             ['After this restart, read OTHER.txt again and tell me both markers.'])
                    for prompt in prompts:
                        before=len(records)
                        r=client.post(f'/session/{sid}/message',params={'directory':str(runtime)},json={
                            'model':{'providerID':'sage-probe','modelID':alias},'agent':'probe',
                            'parts':[{'type':'text','text':prompt}]})
                        data=r.json();info=data.get('info',{})
                        text=''.join(p.get('text','') for p in data.get('parts',[]) if p.get('type')=='text')
                        report['turns'].append({'restart':restart,'status':r.status_code,'request_count':len(records)-before,
                            'finish':info.get('finish'),'error_type':(info.get('error') or {}).get('name'),'answer_has_marker':any(x in text.lower() for x in ('cobalt','amber')),
                            'part_shape':shape(data.get('parts',[]))})
                        args.output.write_text(json.dumps(report,indent=2)+'\n')
                        print(json.dumps(report['turns'][-1]),flush=True)
                    if restart==1:
                        before=len(records)
                        r=client.post(f'/session/{sid}/summarize',params={'directory':str(runtime)},
                                      json={'providerID':'sage-probe','modelID':alias,'auto':False})
                        report['compaction']={'status':r.status_code,'request_count':len(records)-before}
                        before=len(records)
                        r=client.post(f'/session/{sid}/message',params={'directory':str(runtime)},json={
                            'model':{'providerID':'sage-probe','modelID':alias},'agent':'probe',
                            'parts':[{'type':'text','text':'After compaction, read NOTE.txt again. Tell me both markers.'}]})
                        data=r.json()
                        text=''.join(p.get('text','') for p in data.get('parts',[]) if p.get('type')=='text')
                        report['after_compaction']={'status':r.status_code,'request_count':len(records)-before,
                            'both_markers':all(x in text.lower() for x in ('cobalt','amber')),
                            'part_shape':shape(data.get('parts',[]))}
    except (httpx.HTTPError, OSError, ValueError, RuntimeError, TimeoutError) as error:
        report['exception']=type(error).__name__; print('probe failed:',type(error).__name__,flush=True)
    finally:
        server.shutdown();server.server_close();thread.join(timeout=5)
        args.output.write_text(json.dumps(report,indent=2)+'\n')
    passed=(len(report['turns'])==3 and all(t['answer_has_marker'] and not t['error_type']
            and t['request_count']>=2 for t in report['turns'])
            and report.get('compaction',{}).get('status')==200
            and report.get('after_compaction',{}).get('both_markers')
            and all(r.get('status')==200 for r in records))
    return 0 if passed else 1

if __name__=='__main__':raise SystemExit(main())
