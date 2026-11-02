# Internal implementation contract

Desktop sends JSON RPC `{id, method, params}` over the packaged Python sidecar stdin. Stdout contains only JSON responses `{id,result}` or `{id,error:{message,code}}`. Long tasks return durable job/run records and the UI polls. Development HTTP bridge exposes the same dispatcher on loopback with a random session token.

Python package `backend/bughunter`. `Store(root)` in `store.py` provides `.put(kind,data,id=None)`, `.get(kind,id)`, `.list(kind, **filters)`, `.delete(kind,id)`, `.setting(key,default)`, `.set_setting(key,value)`, `.connection(immediate=False)` and `.event(type,data)`. Entity records use snake_case, include id/created_at/updated_at. Each subsystem owns its specialized SQL tables if needed. Main `Application.dispatch(method,params,role='desktop')` composes services. Use ValueError for invalid input; no silent fake success.

## RPC methods and records

- `dashboard.get`, `system.status`, `events.list {after}`
- `projects.list`, `projects.save {name,scope:["https://example.com"], exclusions:[], authorization, allow_private:false, read_only_paths:[], requests_per_second:1,max_concurrency:2,max_requests:1000}`, `projects.get {id}`. Stored kind `project`. Scope must be configured explicitly.
- `sources.list`, `sources.import {path}` or `{filename,content_base64}`, `sources.get {id}`, `sources.delete {id}`, `sources.retry {id}`, `knowledge.search {query,limit:10}`, `knowledge.graph`, `knowledge.correct {id,text}`. Source record title, filename, media_type, status, error, chunk_count. Search result includes text,source_id,title,locator. Graph returns nodes/edges.
- `traffic.list {project_id?}`, `traffic.import {project_id,format:'har'|'burp_xml'|'raw',content,base_url?}`, `traffic.get {id}`. Traffic record request:{method,url,headers,body}, response:{status,headers,body}, session_id.
- `tests.list {project_id?}`, `tests.propose {traffic_id}`, `tests.queue {id,request?}`. Test record title,category,traffic_id,project_id,field,proposed_change,rationale,prerequisites,expected_secure_behavior,confirmation_criteria,status,citations.
- `actions.list {project_id?}`, `actions.get {id}`, `actions.approve {id}`, `actions.deny {id}`, `actions.execute {id}`, `actions.cancel {id}`. Action record project_id,session_id,request,status,effect,reason,request_hash. Approval requires exact request/session binding and single-use consumption.
- `runs.list`, `runs.start {project_id,prompt}`, `runs.stop {id}`, `runs.pause {id}`, `runs.resume {id}`, `system.stop`. Persist kind `run`, fields provider,status,prompt,summary,events,error.
- `findings.list {project_id?}`, `findings.save {project_id,title,status,summary,impact,steps,evidence_ids}`, `findings.export {id,format:'markdown'|'html'|'json'}` returns content,filename,path. Status confirmed requires supported evidence.
- `settings.get`, `settings.save {provider,model,api_base_url?,api_budget_usd,...}`, `settings.secret {provider,value}`. Never return secrets.
- `tools.status`, `tools.burp_history {project_id}`, `tools.repeater {traffic_id}`, `tools.browser {project_id,url}`, `tools.discover {project_id,domain}`, `tools.nuclei {project_id,urls}`. Tools return honest status when dependency or gateway setup missing.
- `lab.setup`: explicit user opt-in creates local test-shop project and fixture traffic; no fake data seeded by default. `lab.start` launches a disposable loopback shop.

Shared TypeScript wire types and RPC result mappings are defined in `src/types.ts` and used by `src/api.ts`. Python services validate and normalize requests at their respective boundaries.

Additional desktop operations include `sources.import {url,download_media:false,language:'en'}`, `knowledge.frames {source_id}`, `knowledge.enrich {source_id,assertions}`, `tests.coverage {project_id}`, `tests.assess {action_id}`, `findings.learn {id}`, `system.resume`, `tools.gateway_start {port}`, and `tools.gateway_verify {burp_proxy,burp_ca}`. Confirmation fields are `confirmation`, `controls`, and `reviewed:true`, alongside trusted project evidence. A saved lesson includes a link to its reviewed finding.

No MCP tool may approve actions, alter scope/settings, access arbitrary paths, export arbitrary files, or execute arbitrary shell commands. MCP calls the separate run-bound `ModelTools` interface; it does not expose the desktop dispatcher. `Application.dispatch` rejects non-desktop roles.
