"""Exercise a frozen backend with real stdio, MCP, a shop, and a local TLS probe.

No model inference or external target requests are made. Optional argument:
the packaged executable path (defaults to this Mac's built sidecar).
"""
import json
import select
import socket
import sqlite3
import subprocess
import sys
import tempfile
import time
import zipfile
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def exchange(process, value):
    process.stdin.write(json.dumps(value)+"\n")
    process.stdin.flush()
    if not select.select([process.stdout],[],[],60)[0]:
        raise TimeoutError("Packaged backend did not answer within 60 seconds")
    line = process.stdout.readline()
    if not line:
        raise RuntimeError("Packaged backend exited: "+process.stderr.read()[-4000:])
    result = json.loads(line)
    if result.get("error"): raise RuntimeError(result["error"])
    return result.get("result")


def main():
    executable = Path(sys.argv[1]) if len(sys.argv)>1 else ROOT/"src-tauri/binaries/bughunter-backend-aarch64-apple-darwin"
    with tempfile.TemporaryDirectory(prefix="bughunter-package-smoke-") as root:
        process = subprocess.Popen([str(executable),"--stdio","--data-dir",root],stdin=subprocess.PIPE,stdout=subprocess.PIPE,stderr=subprocess.PIPE,text=True)
        def rpc(method,**params): return exchange(process,{"id":1,"method":method,"params":params})
        try:
            assert rpc("projects.list") == []
            lesson = Path(root)/"lesson.md"
            lesson.write_text("Coupon eligibility must be checked against server product exclusions.")
            source = rpc("sources.import",path=str(lesson))
            for _ in range(150):
                source = rpc("sources.get",id=source["id"])
                if source["status"] in {"ready","partial","error"}: break
                time.sleep(.1)
            assert source["status"] == "ready", source
            assert rpc("knowledge.search",query="coupon eligibility")
            archive = Path(root)/'lessons.zip'
            with zipfile.ZipFile(archive,'w') as z:
                z.writestr('unit1/notes.md','Coupon minimum spend must remain valid after cart changes.')
                z.writestr('example.py','# Source text is indexed but never executed.\nraise Exception("must not execute")')
            zipped = rpc('sources.import',path=str(archive),copy_original=False)
            for _ in range(150):
                zipped = rpc('sources.get',id=zipped['id'])
                if zipped['status'] in {'ready','partial','error'}: break
                time.sleep(.1)
            assert zipped['status']=='ready',zipped
            assert any('unit1/notes.md' in c['locator'] for c in zipped['chunks'])
            chunk = zipped['chunks'][0]
            rpc('knowledge.examples.save',chunk_id=chunk['id'],question='When is minimum spend checked?',answer='After cart changes.',reviewed=True,training_allowed=True)
            rpc('sources.release_original',id=zipped['id'])
            assert archive.exists()  # Linked external originals are not deleted.
            portable = rpc('knowledge.export',include_frames=False)
            with zipfile.ZipFile(portable['path']) as z:
                assert json.loads(z.read('manifest.json'))['schema_version']==1
                assert len(z.read('train.jsonl').decode().splitlines())==1
            print('Packaged knowledge: ZIP citations, original release, and portable training preparation passed.',flush=True)
            setup = rpc("lab.setup")
            traffic = next(t for t in setup["traffic"] if "/vulnerable/" in t["request"]["url"])
            plan = next(t for t in rpc("tests.propose",traffic_id=traffic["id"]) if t["title"] == "Check coupon eligibility on excluded items")
            action = rpc("tests.queue",id=plan["id"])
            assert action["status"] == "pending_approval"
            rpc("actions.approve",id=action["id"])
            assert rpc("actions.execute",id=action["id"])["status"] == "completed"
            finding = rpc("tests.assess",action_id=action["id"])
            assert finding["status"] == "confirmed"
            assert Path(rpc("findings.export",id=finding["id"],format="json")["path"]).is_file()
            print("Packaged stdio: document ingestion, citations, approval, shop execution, and report export passed.",flush=True)

            # Install a local test run record to exercise MCP without calling a provider.
            stamp = datetime.now(timezone.utc).isoformat()
            run = {"id":"smoke-run","status":"running","project_id":setup["project"]["id"]}
            with sqlite3.connect(Path(root)/"workbench.sqlite3") as db:
                db.execute("INSERT INTO entities VALUES(?,?,?,?,?)",("smoke-run","run",json.dumps(run),stamp,stamp))
            mcp = subprocess.Popen([str(executable),"--mcp","--run-id","smoke-run","--data-dir",root],stdin=subprocess.PIPE,stdout=subprocess.PIPE,stderr=subprocess.PIPE,text=True)
            try:
                assert exchange(mcp,{"jsonrpc":"2.0","id":1,"method":"initialize","params":{"protocolVersion":"2025-03-26","capabilities":{},"clientInfo":{"name":"package-smoke","version":"1"}}})["serverInfo"]
                mcp.stdin.write(json.dumps({"jsonrpc":"2.0","method":"notifications/initialized"})+"\n");mcp.stdin.flush()
                available = exchange(mcp,{"jsonrpc":"2.0","id":2,"method":"tools/list","params":{}})
                assert "get_project" in {t["name"] for t in available["tools"]}
                project = exchange(mcp,{"jsonrpc":"2.0","id":3,"method":"tools/call","params":{"name":"get_project","arguments":{}}})
                assert setup["project"]["id"] in json.dumps(project)
                print("Packaged MCP: initialized, listed explicit tools, and read scoped project metadata.",flush=True)
            finally:
                mcp.stdin.close()
                try: mcp.wait(timeout=10)
                except subprocess.TimeoutExpired: mcp.kill();mcp.wait()

            team=rpc('team.create',name='Packaged shared workspace',repository=root)
            task=rpc('team.task_create',team_id=team['id'],title='Improve application',paths=['src'])
            clients=[]
            try:
                for actor in ('claude','codex'):
                    connection=rpc('team.connection',team_id=team['id'],agent_id=actor)['mcpServers']['bughunter_team']
                    child=subprocess.Popen([connection['command'],*connection['args']],stdin=subprocess.PIPE,stdout=subprocess.PIPE,stderr=subprocess.PIPE,text=True)
                    clients.append(child)
                    exchange(child,{'jsonrpc':'2.0','id':1,'method':'initialize','params':{'protocolVersion':'2025-03-26','capabilities':{},'clientInfo':{'name':actor+'-smoke','version':'1'}}})
                    child.stdin.write(json.dumps({'jsonrpc':'2.0','method':'notifications/initialized'})+'\n');child.stdin.flush()
                claimed=exchange(clients[0],{'jsonrpc':'2.0','id':2,'method':'tools/call','params':{'name':'team_claim','arguments':{'id':task['id']}}})
                assert 'claim_key' in json.dumps(claimed)
                denied=exchange(clients[1],{'jsonrpc':'2.0','id':2,'method':'tools/call','params':{'name':'team_claim','arguments':{'id':task['id']}}})
                assert 'not open' in json.dumps(denied)
                print('Packaged team MCP: two client identities share state and cannot claim the same task.',flush=True)
            finally:
                for child in clients:
                    child.stdin.close()
                    try: child.wait(timeout=10)
                    except subprocess.TimeoutExpired: child.kill();child.wait()

            with socket.socket() as s:
                s.bind(("127.0.0.1",0)); port = s.getsockname()[1]
            gateway = rpc("tools.gateway_start",port=port)
            assert rpc("tools.gateway_verify",burp_proxy=f"http://127.0.0.1:{port}",burp_ca=gateway["ca_path"])["verified"]
            print("Packaged gateway: HTTP and HTTPS loopback routing probes passed (Burp not involved).",flush=True)
            preserved_sources = {source['id'] for source in rpc('sources.list')}
            assert source['id'] in preserved_sources and zipped['id'] in preserved_sources
            rpc('projects.delete',id=setup['project']['id'])
            assert not rpc('projects.list')
            assert {source['id'] for source in rpc('sources.list')} == preserved_sources
            print('Packaged project deletion kept the shared library.',flush=True)
        finally:
            process.stdin.close()
            try: process.wait(timeout=15)
            except subprocess.TimeoutExpired: process.kill();process.wait()
            if process.returncode: print(process.stderr.read()[-4000:],file=sys.stderr)


if __name__ == "__main__": main()
