"""Application composition and desktop-only RPC surface."""
from __future__ import annotations

import platform
import threading
import time
from .file_lock import try_lock
import base64
import hashlib
import shutil

from .store import Store
from .knowledge import KnowledgeService
from .policy import PolicyService
from .traffic import TrafficService
from .testing import TestingService
from .lab import LabService
from .reports import ReportService
from .script_drafts import ScriptDraftService
from .providers import ProviderSettings, RunManager, claude_status, probe_provider
from .team import TeamService
from .agent_jobs import AgentJobService
from .redteam import RedTeamService
from .training import TrainingService
from .tor_research import TorResearchService
from .chat import ChatService
from .tasks import TaskService
from .orchestrator import Orchestrator
from .skills import SkillService
from .prompt_corpus import PromptCorpusService
from .model_routing import ModelRoutes
from .research import WebResearch
from .commerce_audit import CommerceAudit
from .staging_price import StagingPriceAudit
from .website_audit import WebsiteAudit
from .pointer_notebook import PointerNotebook
from .local_media import LocalMediaRuntime
from .autopilot import Autopilot
from .desktop_planner import DesktopPlanner
from .burp_history import BurpHistory
from .tool_installer import ToolInstaller
from .research_p2 import ResearchP2
from .dataset_p2 import DatasetsP2
from .learning_p4 import LearningP4
from .mission_p5 import MissionP5
from .skill_memory_p6 import SkillMemoryP6
from .readiness_p7 import ReleaseReadinessP7
from .advanced_p8 import AdvancedP8
from .verification_p9 import VerificationP9
from .investigation_p10 import InvestigationP10
from .capabilities import CapabilityService


class Application:
    def __init__(self,root=None,primary=True):
        self.store = Store(root)
        self.primary = primary
        self._owner_file = None
        if primary:
            owner = (self.store.root / "desktop.lock").open("a+")
            if not try_lock(owner):
                owner.close()
                raise ValueError("This data directory is already open in another desktop/backend instance.")
            self._owner_file = owner
        self.settings = ProviderSettings(self.store)
        self.model_routes = ModelRoutes(self.store)
        self.media = LocalMediaRuntime(self.store)
        self.knowledge = KnowledgeService(self.store,start_worker=primary)
        self.knowledge.media_runtime = self.media
        self.policy = PolicyService(self.store)
        self.traffic = TrafficService(self.store)
        self.testing = TestingService(self.store,self.policy,self.knowledge)
        self.lab = LabService(self.store,self.policy,self.traffic)
        self.reports = ReportService(self.store)
        self.scripts = ScriptDraftService(self.store)
        self.runs = RunManager(self)
        self.agent_jobs = AgentJobService(self.store, recover=primary)
        self.tasks = TaskService(self)
        self.orchestrator = Orchestrator(self)
        self.skills = SkillService(self.store)
        self.corpus = PromptCorpusService(self.store)
        self.research = WebResearch(self.store, self.knowledge)
        self.research_p2 = ResearchP2(self.store, self.knowledge, start_worker=primary)
        self.datasets_p2 = DatasetsP2(self.store)
        self.learning_p4 = LearningP4(self)
        self.missions_p5 = MissionP5(self)
        self.skill_memory_p6 = SkillMemoryP6(self)
        self.readiness_p7 = ReleaseReadinessP7(self, recover=primary)
        self.advanced_p8 = AdvancedP8(self, recover=primary)
        self.verification_p9 = VerificationP9(self)
        self.investigation_p10 = InvestigationP10(self)
        self.commerce = CommerceAudit(self)
        self.staging_price = StagingPriceAudit(self)
        self.website_audit = WebsiteAudit(self)
        self.pointer_notebook = PointerNotebook(self.store)
        self.autopilot = Autopilot(self)
        self.desktop_planner = DesktopPlanner(self)
        self.burp_history = BurpHistory(self)
        self.tool_installer = ToolInstaller(self.store)
        self.chat = ChatService(self.store, self.settings, self.knowledge, self.agent_jobs, self.tasks, self.model_routes, self.research)
        self.team = TeamService(self.store)
        self.redteam = RedTeamService(self.store,self.settings,self.knowledge)
        self.training = TrainingService(self.store)
        self.tor = TorResearchService(self.store, self.knowledge)
        self.tor.task_service = self.tasks
        self.capabilities = CapabilityService(self)
        self._tools = None
        self._lock = threading.RLock()
        self._claude_cache = None
        self._claude_cache_time = 0
        if primary:
            # A versioned, review-oriented reference is indexed as library data.
            # It contains no executable prompts and is duplicate-safe by hash.
            from .prompt_archive import CURATED_GUIDE
            self.knowledge.import_source(
                filename='ai-redteam-methods.md',
                content_base64=base64.b64encode(CURATED_GUIDE.encode('utf-8')).decode('ascii'),
            )
            # The bundled corpus is kept out of the main chat context. Index only
            # descriptive model-specific research summaries; raw prompt samples
            # require an explicit operator open/review action in AI Testing.
            for summary in sorted((self.corpus.asset_dir / 'summaries').glob('*.md')):
                self.knowledge.import_source(
                    filename=summary.name,
                    content_base64=base64.b64encode(summary.read_bytes()).decode('ascii'),
                )
            self.policy.recover()
            self.runs.recover()
            self.store.set_setting("lab_runtime",{})

    @property
    def tools(self):
        with self._lock:
            if self._tools is None:
                from .adapters import ToolService
                self._tools = ToolService(self.store,self.policy,self.traffic)
            return self._tools

    def dispatch(self,method: str,params: dict | None = None,role="desktop"):
        if role != "desktop":
            raise ValueError("Use the narrow ModelTools interface for agent operations.")
        if not isinstance(method,str) or not isinstance(params or {},dict):
            raise ValueError("Invalid RPC request.")
        p = params or {}
        if method.startswith("capabilities."):
            return self.capabilities.dispatch(method,p)
        if method.startswith("investigation.p10."):
            return self.investigation_p10.dispatch(method,p)
        if method.startswith("verification.p9."):
            return self.verification_p9.dispatch(method,p)
        if method.startswith("advanced.p8."):
            return self.advanced_p8.dispatch(method,p)
        if method.startswith("readiness.p7."):
            return self.readiness_p7.dispatch(method,p)
        if method.startswith("skill.p6."):
            return self.skill_memory_p6.dispatch(method,p)
        if method.startswith("mission.p5."):
            return self.missions_p5.dispatch(method,p)
        if method.startswith("learning.p4."):
            return self.learning_p4.dispatch(method,p)
        if method.startswith("research.p2."):
            return self.research_p2.dispatch(method,p)
        if method.startswith("dataset.p2."):
            return self.datasets_p2.dispatch(method,p)
        if method.startswith("setup."):
            return self.tool_installer.dispatch(method, p)
        if method.startswith("burp."):
            return self.burp_history.dispatch(method,p)
        if method.startswith("desktop."):
            return self.desktop_planner.dispatch(method, p)
        if method.startswith("autopilot."):
            return self.autopilot.dispatch(method, p)
        if method.startswith("media."):
            return self.media.dispatch(method, p)
        if method.startswith('models.routes'):
            return self.model_routes.dispatch(method,p)
        if method.startswith('memory.pointers.'):
            return self.pointer_notebook.dispatch(method,p)
        if method.startswith('website.audit.'):
            return self.website_audit.dispatch(method,p)
        if method.startswith('commerce.staging.'):
            return self.staging_price.dispatch(method,p)
        if method.startswith('commerce.'):
            return self.commerce.dispatch(method,p)
        if method.startswith('research.'):
            return self.research.dispatch(method,p)
        if method == 'system.sandbox.status':
            from .sandbox import status
            return status()
        if method == 'system.sandbox.run':
            from .sandbox import run
            self.store.require_desktop_owner()
            if self.store.setting('emergency_stop',False):
                raise ValueError('Emergency stop is active.')
            result = run(p.get('workspace',''),p.get('code',''),p.get('approved',False))
            # Do not persist code, output or local absolute paths in the event ledger.
            self.store.event('sandbox.execution',{'sandbox':result['sandbox'],'exit_code':result['exit_code']})
            return result
        if method.startswith("corpus."):
            return self.corpus.dispatch(method,p)
        if method.startswith("plans."):
            return self.orchestrator.dispatch(method,p)
        if method.startswith("skills."):
            return self.skills.dispatch(method,p)
        if method.startswith("tasks."):
            return self.tasks.dispatch(method,p)
        if method.startswith("agents."):
            return self.agent_jobs.dispatch(method,p)
        if method.startswith("chat."):
            return self.chat.dispatch(method,p)
        if method.startswith("tor."):
            return self.tor.dispatch(method,p)
        if method.startswith("training."):
            return self.training.dispatch(method,p)
        if method.startswith("redteam."):
            return self.redteam.dispatch(method,p)
        if method.startswith("team."):
            return self.team.dispatch(method,p)
        if method == "projects.delete":
            return self.delete_project(p["id"])
        if method.startswith(("projects.","actions.")):
            result = self.policy.dispatch(method,p)
            if method == "actions.execute":
                self.runs.maybe_auto_resume(result)
            return result
        if method.startswith(("sources.","knowledge.","courses.","experiments.")):
            return self.knowledge.dispatch(method,p)
        if method.startswith("traffic."):
            return self.traffic.dispatch(method,p)
        if method.startswith("tests."):
            return self.testing.dispatch(method,p)
        if method.startswith("lab."):
            return self.lab.dispatch(method,p)
        if method.startswith("tools."):
            return self.tools.dispatch(method,p)
        if method.startswith("scripts."):
            return self.scripts.dispatch(method,p)
        if method == "findings.list":
            return self.store.list("finding",**p)
        if method == "findings.save":
            return self.reports.save(p)
        if method == "findings.export":
            return self.reports.export(**p)
        if method == "findings.learn":
            finding = self.store.get("finding",p["id"])
            if not finding or finding["status"] not in {"confirmed","false_positive"}:
                raise ValueError("Review and resolve the finding before saving it as a lesson.")
            report = self.reports.export(p["id"])
            source = self.knowledge.import_source(filename=report["filename"],content_base64=base64.b64encode(report["content"].encode()).decode())
            source = self.knowledge._update(source["id"],curated_from_finding=finding["id"])
            status = "verified" if finding["status"] == "confirmed" else "imported"
            with self.store.connection(immediate=True) as db:
                node = "observation_"+finding["id"]
                self.knowledge._node(db,node,"observation",finding["title"],{"source_id":source["id"],"finding_id":finding["id"],"status":status,
                    "origin":"reviewed_investigation","text":finding.get("confirmation",finding["summary"]),"evidence_ids":finding.get("evidence_ids",[])})
                self.knowledge._edge(db,source["id"],node,"documents",source["id"],None,{"origin":"reviewed_investigation","status":status})
            return source
        if method == "settings.get":
            return self.settings.get()
        if method == "settings.memory_bridge":
            # This desktop-only RPC is used to provision the native bridge in memory.
            # Never persist, log, or include its credentials in task traces.
            from .providers import keyring
            configured = self.settings.get()
            provider = str(configured.get('provider') or '')
            model = str(configured.get('model') or '').strip()
            if provider not in {'ollama', 'remote_ollama', 'openai_compatible', 'openai', 'deepseek'}:
                return {'supported': False, 'reason': 'This memory helper requires an OpenAI-compatible chat API. Select Kaggle/custom AI, Ollama, OpenAI, or DeepSeek in Settings.'}
            defaults = {'ollama':'http://127.0.0.1:11434/v1', 'openai':'https://api.openai.com/v1',
                        'deepseek':'https://api.deepseek.com/v1'}
            base = (str(configured.get('api_base_url') or '') or defaults.get(provider, '')).rstrip('/')
            if not base:
                return {'supported':False,'reason':'Your AI server address is missing in Settings.'}
            if provider in {'ollama','remote_ollama'} and not base.endswith('/v1'):
                base += '/v1'
            if provider == 'openai_compatible' and not base.endswith('/v1'):
                return {'supported':False,'reason':'Set the Kaggle/custom model URL ending in /v1 in Settings.'}
            if not model:
                return {'supported':False,'reason':'Choose a model in Settings first.'}
            try:
                secret = keyring.get_password('BugHunterClaudex',provider) or '' if provider != 'ollama' else ''
            except Exception:
                secret = ''
            if provider in {'openai','deepseek','remote_ollama'} and not secret:
                return {'supported':False,'reason':'Save the connection key in Settings first.'}
            return {'supported':True,'base_url':base,'model':model,'api_key':secret or 'local',
                    'system_prompt':str(configured.get('custom_system_prompt') or '')[:12000]}

        if method == "settings.save":
            return self.settings.save(p)
        if method == "settings.test":
            return probe_provider(self.settings.get())
        if method == "settings.secret":
            return self.settings.secret(**p)
        if method == "settings.credentials":
            return self.settings.credentials()
        if method == "runs.list":
            return [{k:v for k,v in r.items() if k != "settings_snapshot"} for r in self.store.list("run",**p)]
        if method == "runs.start":
            return self.runs.start(**p)
        if method == "runs.start_study":
            return self.runs.start_study(**p)
        if method in {"runs.stop","runs.pause"}:
            return self.runs.stop(p["id"],"paused" if method == "runs.pause" else "cancelled")
        if method == "runs.resume":
            return self.runs.resume(p["id"])
        if method == "system.stop":
            self.readiness_p7.cancel_all()
            for job in self.agent_jobs.dispatch("agents.list", {}):
                if job.get("status") in {"starting", "running"}:
                    self.agent_jobs.cancel(job["id"])
            self.policy.stop()
            for run in self.store.list("run"):
                if run.get("status") in {"running","starting","awaiting_approval"}:
                    self.runs.stop(run["id"])
            return {"stopped":True,"message":"No new requests will be sent. Already transmitted requests may have taken effect."}
        if method == "system.resume":
            self.store.set_setting("emergency_stop",False)
            return {"resumed":True,"message":"New requests are enabled. Cancelled actions remain cancelled."}
        if method == "events.list":
            return self.store.events(**p)
        if method == "system.backup.create":
            from .backup import create
            from datetime import datetime, timezone
            from pathlib import Path
            # Only the live desktop owner can create an app backup. Caller cannot
            # supply arbitrary filesystem paths through this RPC.
            self.store.require_desktop_owner()
            folder = self.store.root.parent / "BugHunterClaudex Backups"
            timestamp = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S-%f")
            return create(self.store.root, folder / f"backup-{timestamp}.zip")
        if method == "system.dependencies":
            from .tool_readiness import inventory
            return inventory(self.store.setting('tool_locations', {}))
        if method == 'system.tools.locate':
            from .tool_readiness import validate_tool_path, inventory
            self.store.require_desktop_owner()
            name, path = p.get('name'), p.get('path')
            result = validate_tool_path(name, path)
            locations = dict(self.store.setting('tool_locations', {}))
            locations[name] = result['location']
            self.store.set_setting('tool_locations', locations)
            return {'selected': result, 'tools': inventory(locations)}
        if method == 'system.tools.clear':
            from .tool_readiness import inventory
            self.store.require_desktop_owner()
            name = p.get('name')
            if name not in ('Burp Suite', 'Cheat Engine'):
                raise ValueError('Unsupported tool selection.')
            locations = dict(self.store.setting('tool_locations', {}))
            locations.pop(name, None)
            self.store.set_setting('tool_locations', locations)
            return {'tools': inventory(locations)}
        if method == "system.task_readiness":
            from .task_readiness import inspect_task
            from .tool_readiness import inventory
            project_id = p.get('project_id')
            project = self.store.get('project', str(project_id)) if project_id else None
            return inspect_task(str(p.get('kind','chat')), settings=self.settings.get(),
                credentials=self.settings.credentials(), dependencies=inventory(self.store.setting('tool_locations', {})),
                project=project, gateway=self.store.setting('gateway', {}))
        if method == "system.status":
            if self._claude_cache is None or time.monotonic()-self._claude_cache_time > 30:
                self._claude_cache = claude_status()
                self._claude_cache_time = time.monotonic()
            return {"version":"0.1.0","platform":platform.system(),"architecture":platform.machine(),
                "data_dir":str(self.store.root),"provider":self.settings.get()["provider"],"claude":self._claude_cache,
                "emergency_stop":self.store.setting("emergency_stop",False)}
        if method == "dashboard.get":
            return {"counts":{kind:len(self.store.list(kind)) for kind in ("project","source","traffic","test","action","finding","run")},
                "pending_approvals":len(self.store.list("action",status="pending_approval")),
                "recent_events":self.store.events(max(0,int(p.get("after",0))),30)}
        raise ValueError(f"Unknown application operation: {method}")

    def delete_project(self, id):
        project = self.policy.project(id)
        runs = self.store.list("run",project_id=id)
        actions = self.store.list("action",project_id=id)
        traffic = self.store.list("traffic",project_id=id)
        assets = self.store.list("asset",project_id=id)
        # A deleted project must not leave its local workers running in the background.
        for task in self.store.list('work_task', project_id=id):
            if task.get('status') in {'starting','running','awaiting_approval','queued','cancelling'}:
                try:
                    if task.get('kind') == 'orchestrated':
                        self.orchestrator.cancel(task['id'])
                    elif task.get('kind') not in {'tor_browser','memory_session'}:
                        self.tasks.cancel(task['id'])
                except (ValueError, KeyError):
                    # Cleanup proceeds even if an abandoned runner has vanished.
                    pass
        for job in self.store.list('agent_job',project_id=id):
            if job.get('status') in {'starting','running'}:
                self.agent_jobs.cancel(job['id'])
        for run in runs:
            self.runs.stop(run['id'])
        for action in actions:
            if action["status"] not in {"completed","failed","denied","cancelled","unknown"}:
                self.policy.cancel(action["id"])
        if project.get("lab_project"):
            self.lab.stop()
        kinds = ("traffic","test","action","finding","run","asset","observation","script_draft","reviewed_skill","work_task","work_task_event","research_watch","research_discovery")
        counts = {kind:len(self.store.list(kind,project_id=id)) for kind in kinds}
        with self.store.connection(immediate=True) as db:
            db.execute("INSERT OR REPLACE INTO settings(key,value) VALUES(?,?)",("deleted_project:"+id,"true"))
            db.execute("DELETE FROM events WHERE json_extract(data,'$.project_id')=? OR json_extract(data,'$.run_id') IN (SELECT id FROM entities WHERE json_extract(data,'$.project_id')=?) OR json_extract(data,'$.action_id') IN (SELECT id FROM entities WHERE json_extract(data,'$.project_id')=?) OR json_extract(data,'$.id') IN (SELECT id FROM entities WHERE json_extract(data,'$.project_id')=?)",(id,id,id,id))
            db.execute("DELETE FROM gateway_tickets WHERE action_id IN (SELECT id FROM entities WHERE kind='action' AND json_extract(data,'$.project_id')=?)",(id,))
            db.execute("DELETE FROM adapter_tickets WHERE project_id=?",(id,))
            db.execute("DELETE FROM request_budgets WHERE bucket=?",(f"manual:{id}",))
            for run in runs: db.execute("DELETE FROM request_budgets WHERE bucket=?",(run["id"],))
            for kind in kinds: db.execute("DELETE FROM entities WHERE kind=? AND json_extract(data,'$.project_id')=?",(kind,id))
            db.execute("DELETE FROM entities WHERE kind='project' AND id=?",(id,))
        workspace = (self.store.root/"workspaces").resolve()
        for run in runs:
            folder = (workspace/run["id"]).resolve()
            if folder.parent == workspace: shutil.rmtree(folder,ignore_errors=True)
        for session in {a.get("session_id","") for a in actions+traffic}:
            if session.startswith("browser:"):
                (workspace/("browser-"+hashlib.sha256(f"{id}:{session}".encode()).hexdigest()+".json")).unlink(missing_ok=True)
        for asset in assets:
            if asset.get("path"):
                path = self.store.root/"artifacts"/asset["path"]
                if path.resolve().parent == (self.store.root/"artifacts").resolve(): path.unlink(missing_ok=True)
        self.store.event("project.deleted",{"project_id":id,"removed":counts})
        return {"id":id,"deleted":True,"removed":counts,"library_preserved":True,"exported_files_preserved":True}

    def shutdown(self):
        self.readiness_p7.shutdown()
        try: self.capabilities.shutdown()
        except Exception: pass
        self.agent_jobs.shutdown()
        # A clean exit reports a real browser shutdown; crashes are reconciled
        # as interrupted on the next launch instead.
        try:
            if self.tor.browser.status().get('running'):
                self.tor.dispatch('tor.browser.close', {})
            else:
                self.tor.browser.close()
        except Exception:
            self.tor.browser.close()
        if self.primary:
            self.runs.shutdown()
            self.policy.stop()
            self.lab.stop()
        if self._tools:
            self._tools.close()
        self.research_p2.shutdown()
        self.knowledge.shutdown()
        self.media.shutdown()
        if self._owner_file:
            self._owner_file.close()
            self._owner_file = None

    def close(self):
        """Compatibility entry point for callers using the standard resource API."""
        self.shutdown()
