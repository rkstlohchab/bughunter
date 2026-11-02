from __future__ import annotations

import argparse
import asyncio
from concurrent.futures import ThreadPoolExecutor
import hmac
import json
import os
from pathlib import Path
import signal
import sys
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from .app import Application
from .security import redact_text


def response(app, request):
    id = request.get("id") if isinstance(request,dict) else None
    try:
        if not isinstance(request,dict) or not isinstance(request.get("params",{}),dict):
            raise ValueError("Invalid request object.")
        return {"id":id,"result":app.dispatch(request.get("method"),request.get("params",{}))}
    except (ValueError,KeyError,TypeError) as error:
        return {"id":id,"error":{"code":"invalid_request","message":redact_text(str(error))}}
    except Exception as error:
        print(f"RPC {request.get('method','?') if isinstance(request,dict) else '?'}: {type(error).__name__}: {redact_text(str(error))}",file=sys.stderr)
        return {"id":id,"error":{"code":"operation_failed","message":redact_text(str(error))}}


def stdio(app):
    output_lock = threading.Lock()
    def handle(line):
        try:
            value = response(app,json.loads(line))
        except json.JSONDecodeError:
            value = {"id":None,"error":{"code":"invalid_json","message":"Request is not valid JSON."}}
        with output_lock:
            print(json.dumps(value,ensure_ascii=False,default=str),flush=True)
    pool = ThreadPoolExecutor(max_workers=8,thread_name_prefix="desktop-rpc")
    try:
        for line in sys.stdin:
            if len(line) > 100_000_000:
                print(json.dumps({"id":None,"error":{"code":"too_large","message":"Request exceeds 100 MB. Import large media by path."}}),flush=True)
                continue
            pool.submit(handle,line)
    finally:
        app.shutdown()
        pool.shutdown(wait=True,cancel_futures=True)


def http_server(app,port,token):
    if not token or len(token) < 24:
        raise ValueError("Set BUGHUNTER_RPC_TOKEN to a random token of at least 24 characters for the development HTTP bridge.")
    allowed_origins = {"http://127.0.0.1:1420","http://localhost:1420"}
    class Handler(BaseHTTPRequestHandler):
        def log_message(self,*args):
            pass
        def send(self,code,value):
            body = json.dumps(value,ensure_ascii=False,default=str).encode()
            self.send_response(code)
            self.send_header("Content-Type","application/json")
            self.send_header("Content-Length",str(len(body)))
            self.send_header("Cache-Control","no-store")
            if self.headers.get("Origin") in allowed_origins:
                self.send_header("Access-Control-Allow-Origin",self.headers["Origin"])
                self.send_header("Vary","Origin")
            self.end_headers()
            self.wfile.write(body)
        def do_OPTIONS(self):
            if self.headers.get("Origin") not in allowed_origins:
                return self.send(403,{"error":"Origin is not permitted"})
            self.send_response(204)
            self.send_header("Access-Control-Allow-Origin",self.headers["Origin"])
            self.send_header("Access-Control-Allow-Methods","POST, OPTIONS")
            self.send_header("Access-Control-Allow-Headers","Content-Type, Authorization")
            self.end_headers()
        def do_GET(self):
            self.send(405,{"error":"Use authenticated POST /rpc"})
        def do_POST(self):
            if self.path != "/rpc":
                return self.send(404,{"error":"Not found"})
            if self.headers.get("Origin") and self.headers["Origin"] not in allowed_origins:
                return self.send(403,{"error":"Origin is not permitted"})
            if not hmac.compare_digest(self.headers.get("Authorization",""),"Bearer "+token):
                return self.send(401,{"error":"Authentication required"})
            try:
                size = int(self.headers.get("Content-Length",0))
                if not 0 < size <= 100_000_000:
                    return self.send(413,{"error":"Invalid request size"})
                request = json.loads(self.rfile.read(size))
            except (ValueError,json.JSONDecodeError):
                return self.send(400,{"error":"Invalid JSON request"})
            self.send(200,response(app,request))
    class LocalRPCServer(ThreadingHTTPServer):
        # The UI loads its collections concurrently. The default five-slot
        # accept backlog can reset those local connections under build/load.
        request_queue_size = 64
    server = LocalRPCServer(("127.0.0.1",port),Handler)
    print(f"Local development RPC listening at http://127.0.0.1:{server.server_port}",file=sys.stderr)
    try:
        server.serve_forever()
    finally:
        server.server_close()
        app.shutdown()


def main():
    parser = argparse.ArgumentParser(description="BugHunterClaudex local backend")
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--stdio",action="store_true")
    mode.add_argument("--http",action="store_true")
    mode.add_argument("--mcp",action="store_true")
    mode.add_argument("--team-mcp",action="store_true")
    mode.add_argument("--gateway",action="store_true")
    parser.add_argument("--port",type=int,default=None)
    parser.add_argument("--run-id")
    parser.add_argument("--team-id")
    parser.add_argument("--agent-id")
    parser.add_argument("--data-dir")
    args = parser.parse_args()
    if args.gateway:
        from .gateway import run_gateway
        from .store import Store
        asyncio.run(run_gateway(Store(args.data_dir),args.port or 8091))
        return
    app = Application(args.data_dir,primary=not (args.mcp or args.team_mcp))
    def exit_cleanly(signum,frame):
        app.shutdown()
        raise SystemExit(0)
    signal.signal(signal.SIGTERM,exit_cleanly)
    signal.signal(signal.SIGINT,exit_cleanly)
    if args.team_mcp:
        if not args.team_id or not args.agent_id:
            parser.error("--team-mcp requires --team-id and --agent-id")
        from .mcp_server import serve_capability
        from .team import TeamTools, TEAM_SPECS
        try:
            asyncio.run(serve_capability(TeamTools(app,args.team_id,args.agent_id),TEAM_SPECS,"bughunter-team"))
        finally:
            app.shutdown()
    elif args.mcp:
        if not args.run_id:
            parser.error("--mcp requires --run-id")
        from .mcp_server import serve
        try:
            asyncio.run(serve(app,args.run_id))
        finally:
            app.shutdown()
    elif args.http:
        http_server(app,args.port or 8765,os.environ.get("BUGHUNTER_RPC_TOKEN",""))
    else:
        stdio(app)


if __name__ == "__main__":
    main()
