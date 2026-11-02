#!/usr/bin/env python3
"""Local radar-style overlay reading the bundled test game's REAL process memory.

The overlay is browser-based, not injected into another game's graphics pipeline.
Only the child fixture that this script starts is attachable. If macOS denies
access, the page shows an error instead of fabricating entity positions.
"""
from __future__ import annotations

import argparse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
from pathlib import Path
import subprocess
import re
import sys
import threading
import webbrowser

ROOT = Path(__file__).resolve().parents[1]
HTML = r'''<!doctype html><html lang="en"><meta charset="utf-8"><title>BugHunter • Offline ESP Lab</title>
<style>body{font:15px system-ui;background:#111824;color:#e1e8f2;margin:0;display:grid;place-items:center;min-height:100vh}main{width:min(680px,94vw)}h2{font-weight:600}p{color:#aebed4}canvas{background:#1b2a3a;border:1px solid #53647d;border-radius:14px;width:100%;aspect-ratio:1}#status{padding:14px;background:#203044;border-radius:12px;margin:15px 0;white-space:pre-wrap}</style>
<main><h2>BugHunter • Offline entity radar</h2><p>Reads positions from the bundled disposable test process via the Mac memory helper. Uses an operator-supplied layout or the bundled disposable game. Not a game-window overlay.</p><div id="status">Connecting to test process…</div><canvas id="map" width="680" height="680"></canvas></main>
<script>
const ctx=document.querySelector('#map').getContext('2d'),status=document.querySelector('#status');
async function tick(){try{let res=await fetch('/state',{cache:'no-store'});let data=await res.json();if(!res.ok)throw Error(data.error||'read failed');status.textContent='PID '+data.pid+' • '+data.entities.length+' live entities • source: real memory read';ctx.clearRect(0,0,680,680);ctx.strokeStyle='#3d5368';for(let r=100;r<340;r+=100){ctx.beginPath();ctx.arc(340,340,r,0,2*Math.PI);ctx.stroke()}ctx.strokeStyle='#78879c';ctx.beginPath();ctx.moveTo(0,340);ctx.lineTo(680,340);ctx.moveTo(340,0);ctx.lineTo(340,680);ctx.stroke();for(const e of data.entities){let x=340+e.x*3,y=340-e.y*3;ctx.fillStyle=e.team===1?'#53c7af':'#ff8a85';ctx.beginPath();ctx.arc(x,y,8,0,2*Math.PI);ctx.fill();ctx.fillStyle='#f0f5ff';ctx.font='14px system-ui';ctx.fillText('#'+e.id+' HP:'+e.health, x+13,y-9)} }catch(err){status.textContent='Not verified: '+err.message;ctx.clearRect(0,0,680,680)}}
setInterval(tick,350);tick();
</script></html>'''

class Bridge:
    def __init__(self, proc):
        self.proc = proc
        self.seq = 0
        self.lock = threading.Lock()

    def call(self, name, arguments):
        with self.lock:
            self.seq += 1
            self.proc.stdin.write(json.dumps({'jsonrpc': '2.0', 'id': self.seq,
                'method': 'tools/call', 'params': {'name': name, 'arguments': arguments}}) + '\n')
            self.proc.stdin.flush()
            line = self.proc.stdout.readline()
            if not line:
                raise RuntimeError('Memory helper exited. Check access permissions.')
            result = json.loads(line)
            if 'error' in result:
                raise RuntimeError(str(result['error']))
            body = result.get('result', {})
            message = '\n'.join(x.get('text', '') for x in body.get('content', []))
            if body.get('isError'):
                raise RuntimeError(message)
            return message


def load_target_layout(path, override_pid=None):
    """Load a user-verified entity layout. Never infer offsets or entity types."""
    if not path.is_file() or path.stat().st_size > 20_000:
        raise ValueError('Layout must be a JSON file smaller than 20 KB.')
    row = json.loads(path.read_text(encoding='utf-8'))
    if not isinstance(row, dict):
        raise ValueError('Layout root must be a JSON object.')
    pid = override_pid if override_pid is not None else row.get('pid')
    if type(pid) is not int or not 1 <= pid <= 2**31 - 1:
        raise ValueError('Layout must identify an existing positive target PID.')
    base = row.get('entity_base', row.get('base'))
    if not isinstance(base, (str, int)) or not re.fullmatch(r'(?:0[xX][0-9a-fA-F]+|[0-9]+)', str(base)):
        raise ValueError('Supply an observed entity base address as hex or decimal.')
    layout = row.get('entity_layout')
    if not isinstance(layout, dict):
        raise ValueError('Supply an entity_layout object with count, stride, and fields.')
    count, stride, fields = layout.get('count'), layout.get('stride'), layout.get('fields')
    if type(count) is not int or not 1 <= count <= 32 or type(stride) is not int or not 4 <= stride <= 512:
        raise ValueError('Count or stride is outside the supported memory-helper limits.')
    if not isinstance(fields, list) or not 1 <= len(fields) <= 7:
        raise ValueError('Supply 1–7 decoded fields.')
    accepted = {'x', 'y', 'z', 'health', 'team', 'visible', 'id'}
    if len({f.get('name') for f in fields if isinstance(f, dict)}) != len(fields):
        raise ValueError('Fields must have unique names.')
    if any(not isinstance(f, dict) or f.get('name') not in accepted or
           f.get('type') not in ('i32','u32','i64','u64','f32','f64','i16','u16','i8','u8') or
           type(f.get('offset')) is not int or f['offset'] < 0 or f['offset'] >= stride
           for f in fields):
        raise ValueError('Invalid entity field or offset.')
    if not {'x','y'} <= {f['name'] for f in fields}:
        raise ValueError('Radar layout requires x and y fields.')
    return {'pid': pid, 'entity_base': str(base), 'entity_layout': layout}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--port', type=int, default=8787)
    parser.add_argument('--no-browser', action='store_true')
    parser.add_argument('--layout', type=Path, help='JSON layout for a user-selected, accessible offline game process')
    parser.add_argument('--pid', type=int, help='Optional PID override; requires --layout')
    args = parser.parse_args()
    if not 1024 <= args.port <= 65535:
        parser.error('Port must be between 1024 and 65535.')
    if args.pid is not None and not args.layout:
        parser.error('--pid requires an explicit --layout JSON file.')
    fixture = None
    if args.layout:
        config = load_target_layout(args.layout, args.pid)
    else:
        fixture = subprocess.Popen([sys.executable, '-u', str(ROOT/'examples/offline_memory_lab.py')],
                      stdin=subprocess.PIPE, stdout=subprocess.PIPE, text=True)
        config = json.loads(fixture.stdout.readline())
    helper = subprocess.Popen([sys.executable, '-u', str(ROOT/'src-tauri/resources/mac_memory_mcp.py')],
                  stdin=subprocess.PIPE, stdout=subprocess.PIPE, text=True)
    bridge = Bridge(helper)
    try:
        print('Radar target PID:', config['pid'], flush=True)
        try:
            bridge.call('attach_process', {'pid': config['pid']})
            attach_error = None
        except Exception as exc:
            attach_error = str(exc)
            print('Memory attach failed:', attach_error, flush=True)

        class Handler(BaseHTTPRequestHandler):
            def do_GET(self):
                if self.path == '/':
                    data = HTML.encode('utf-8')
                    self.send_response(200);self.send_header('Content-Type', 'text/html; charset=utf-8')
                elif self.path == '/state':
                    try:
                        if attach_error:
                            raise RuntimeError(attach_error)
                        content = bridge.call('read_entity_layout', {
                            'base': config['entity_base'], **config['entity_layout']})
                        observation = json.loads(content)
                        data = json.dumps({'pid': config['pid'],
                                           'entities': observation['entities']}).encode('utf-8')
                        self.send_response(200)
                    except Exception as exc:
                        data = json.dumps({'error': str(exc)[:500]}).encode('utf-8')
                        self.send_response(503)
                    self.send_header('Content-Type', 'application/json')
                else:
                    self.send_response(404);self.end_headers();return
                self.send_header('Cache-Control', 'no-store');self.send_header('Content-Length', str(len(data)))
                self.end_headers();self.wfile.write(data)
            def log_message(self, *_args):
                pass

        server = ThreadingHTTPServer(('127.0.0.1', args.port), Handler)
        url = f'http://127.0.0.1:{args.port}'
        print('Open your offline radar:', url, flush=True)
        if not args.no_browser:
            webbrowser.open(url)
        try:
            server.serve_forever(poll_interval=0.3)
        except KeyboardInterrupt:
            pass
        finally:
            server.server_close()
    finally:
        for p in (helper, fixture):
            if p is None: continue
            try:
                p.terminate();p.wait(timeout=3)
            except Exception:
                p.kill()

if __name__ == '__main__':
    main()
