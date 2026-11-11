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
from .providers import ProviderSettings, RunManager, claude_status, probe_provider, PROVIDERS
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
from .memory_mappings import MemoryMappings
from .mapping_chat import MappingChat
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
        self.memory_mappings = MemoryMappings(self, recover=primary)
        self.autopilot = Autopilot(self)
        self.desktop_planner = DesktopPlanner(self)
        self.burp_history = BurpHistory(self)
        self.tool_installer = ToolInstaller(self.store)
        self.chat = ChatService(self.store, self.settings, self.knowledge, self.agent_jobs, self.tasks, self.model_routes, self.research)
        self.mapping_chat = MappingChat(self)
        self.team = TeamService(self.store)
        self.redteam = RedTeamService(self.store,self.settings,self.knowledge)
        self.training = TrainingService(self.store)
        self.tor = TorResearchService(self.store, self.knowledge)
        self.tor.task_service = self.tasks
        self.capabilities = CapabilityService(self)
        self.memory = None
        try:
            import platform as _plat
            if _plat.system() == "Darwin":
                from .mac_memory import MacMemory
                self.memory = MacMemory()
        except Exception:
            self.memory = None
        self._tools = None
        self._lock = threading.RLock()
        self._claude_cache = None
        self._claude_cache_time = 0
        if primary and not self.store.setting('curated_seeded', False):
            # Seed the bundled reference library once. Gating on a flag means a
            # deliberate knowledge-base wipe is not undone on the next launch.
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
            self.store.set_setting('curated_seeded', True)
        if primary:
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
        if method == "models.available":
            return self.available_models()
        if method == "models.select":
            return self.select_model(str(p.get("provider","")), str(p.get("model","")))
        if method.startswith('models.routes'):
            return self.model_routes.dispatch(method,p)
        if method.startswith('memory.engine.'):
            return self._memory_dispatch(method[len('memory.engine.'):], p)
        if method.startswith('memory.pointers.'):
            return self.pointer_notebook.dispatch(method,p)
        if method.startswith('memory.mappings.'):
            return self.memory_mappings.dispatch(method,p)
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
        if method.startswith('chat.mapping.'):
            return self.mapping_chat.dispatch(method,p)
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
            for task in self.store.list('mapping_chat'):
                if task['status'] in {'needs_program','researching','needs_value','checking','blocked'}:
                    with self.chat._turn_locks[hash(task['chat_id']) % len(self.chat._turn_locks)]:
                        session = self.store.get('chat',task['chat_id'])
                        if session and session.get('mapping_id') == task['id']:
                            self.mapping_chat.stop(task['chat_id'])
                        else:
                            if task.get('research_id'): self.memory_mappings.stop(task['research_id'])
                            self.store.put('mapping_chat',{**task,'status':'cancelled'},id=task['id'])
            self.memory_mappings.stop_all()
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
        if method == "system.autosetup":
            return self.autosetup(bool(p.get("install_missing", False)))
        if method == "system.open_privacy":
            return self.open_privacy(str(p.get("pane", "")))
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

    # Curated, sensible default model lists per provider for the chat picker.
    PROVIDER_CATALOG = {
        "claude_code": {"label": "Claude subscription", "kind": "subscription",
                        "models": ["opus", "sonnet", "haiku"]},
        "anthropic": {"label": "Anthropic API", "kind": "api",
                      "models": ["claude-sonnet-4-5", "claude-opus-4-1", "claude-haiku-4-5"]},
        "openai": {"label": "OpenAI (ChatGPT API)", "kind": "api",
                   "models": ["gpt-4.1", "gpt-4.1-mini", "gpt-4o"]},
        "deepseek": {"label": "DeepSeek", "kind": "api",
                     "models": ["deepseek-chat", "deepseek-reasoner"]},
        "ollama": {"label": "Local model (Ollama)", "kind": "local", "models": []},
        "remote_ollama": {"label": "Remote Ollama", "kind": "api", "models": []},
        "openai_compatible": {"label": "Kaggle / custom model", "kind": "api", "models": []},
        "crucible": {"label": "On-device (CrucibleLLM)", "kind": "local", "models": []},
    }

    def available_models(self) -> dict:
        """List model providers for the chat picker: which are ready to use now,
        which need a key/sign-in added, and the models each offers."""
        settings = self.settings.get()
        creds = self.settings.credentials()
        active_provider = settings.get("provider")
        active_model = settings.get("model")
        if self._claude_cache is None or time.monotonic() - self._claude_cache_time > 30:
            self._claude_cache = claude_status()
            self._claude_cache_time = time.monotonic()
        try:
            media = self.media.status()
            ollama_models = [m for m in media.get("installed_models", []) if ":" not in m or "embed" not in m.lower()]
            ollama_running = media.get("ollama_running", False)
        except Exception:
            ollama_models, ollama_running = [], False
        out = []
        for provider, meta in self.PROVIDER_CATALOG.items():
            ready, needs, models = False, "", list(meta["models"])
            if provider == "claude_code":
                ready = bool(self._claude_cache.get("subscription_ready"))
                needs = "" if ready else "Sign in: run  claude auth login  in Terminal."
            elif provider == "ollama":
                models = ollama_models or meta["models"]
                ready = ollama_running and bool(models)
                needs = "" if ready else ("Start Ollama and pull a model." if not models else "Start Ollama from Settings.")
            elif provider in {"anthropic", "openai", "deepseek", "remote_ollama", "openai_compatible"}:
                ready = bool(creds.get(provider))
                needs = "" if ready else "Add an API key in Settings."
                if provider == "openai_compatible" and settings.get("api_base_url"):
                    models = models or [settings.get("model") or "custom"]
            elif provider == "crucible":
                ready = bool(settings.get("api_base_url")) and active_provider == "crucible"
                needs = "" if ready else "Set your device address in Settings."
            out.append({"provider": provider, "label": meta["label"], "kind": meta["kind"],
                        "ready": ready, "needs": needs, "models": models,
                        "active": provider == active_provider})
        return {"providers": out, "active": {"provider": active_provider, "model": active_model}}

    def select_model(self, provider: str, model: str) -> dict:
        """Switch the active chat model. Fills in safe defaults so a subscription or
        local model never trips the paid-API pricing confirmation."""
        if provider not in PROVIDERS:
            raise ValueError("Unknown provider.")
        settings = self.settings.get()
        patch = {"provider": provider, "model": model or settings.get("model")}
        if provider != settings.get('provider'):
            if provider in {'remote_ollama', 'openai_compatible', 'crucible'}:
                raise ValueError('Configure this provider and its endpoint in Settings before selecting it.')
            # A provider credential must never follow a custom endpoint from the
            # previously selected provider.
            patch['api_base_url'] = ''
            if provider in {'anthropic', 'openai', 'deepseek'}:
                patch['pricing_confirmed'] = False
        from .providers import LOCAL_PROVIDERS as _LOCAL, SELF_HOSTED_PROVIDERS as _SELF
        if provider == "claude_code" or provider in _LOCAL or provider in _SELF:
            patch["pricing_confirmed"] = True
        return self.settings.save({**settings, **patch})

    def _full_disk_access(self) -> bool:
        """Probe whether this process has macOS Full Disk Access by reading a
        TCC-protected path. macOS gates FDA behind a manual toggle; we can only
        detect it and deep-link the user to the pane, never grant it."""
        if platform.system() != "Darwin":
            return True
        from pathlib import Path
        probe = Path.home() / "Library/Application Support/com.apple.TCC/TCC.db"
        try:
            with open(probe, "rb") as handle:
                handle.read(1)
            return True
        except PermissionError:
            return False
        except (FileNotFoundError, OSError):
            return False

    def open_privacy(self, pane: str) -> dict:
        """Open a specific macOS Privacy & Security pane so the user can grant a
        permission that only a human can toggle."""
        panes = {
            "full_disk": "x-apple.systempreferences:com.apple.preference.security?Privacy_AllFiles",
            "accessibility": "x-apple.systempreferences:com.apple.preference.security?Privacy_Accessibility",
            "screen_recording": "x-apple.systempreferences:com.apple.preference.security?Privacy_ScreenCapture",
            "automation": "x-apple.systempreferences:com.apple.preference.security?Privacy_Automation",
        }
        url = panes.get(pane)
        if not url:
            raise ValueError("Unknown privacy pane.")
        if platform.system() != "Darwin":
            return {"opened": False, "detail": "Privacy panes are macOS-only."}
        import subprocess
        subprocess.Popen(["open", url])
        return {"opened": True, "pane": pane}

    def autosetup(self, install_missing: bool = False) -> dict:
        """Autonomously bring the local environment up: detect tools, start the
        local model server, start and verify the policy gateway, enable sensible
        defaults, and report which macOS permissions still need a manual toggle.

        Each step is isolated so one failure never aborts the rest. Returns a
        checklist the UI renders; items marked needs_you require a human OS action
        that macOS does not allow software to perform."""
        from .tool_readiness import inventory
        self.store.require_desktop_owner()
        if self.store.setting('emergency_stop', False):
            raise ValueError('Resume tasks before starting automatic setup.')
        steps = []
        def record(key, label, status, detail, action=None):
            steps.append({"key": key, "label": label, "status": status, "detail": detail, "action": action})

        # 1. Detect installed tools.
        try:
            tools = inventory(self.store.setting("tool_locations", {}))
            found = [t["name"] for t in tools if t.get("installed")]
            missing = [t["name"] for t in tools if not t.get("installed")]
            record("tools", "Detect local tools", "done",
                   f"Found: {', '.join(found) or 'none'}." + (f" Not installed: {', '.join(missing)}." if missing else ""))
        except Exception as exc:
            record("tools", "Detect local tools", "error", str(exc))

        # 2. Local model server + video/vision tools.
        try:
            media = self.media.status()
            cfg = media.get("config", {})
            if media.get("ollama_bin") and not media.get("ollama_running"):
                try:
                    self.media.start()
                    media = self.media.status()
                except Exception:
                    pass
            if media.get("ollama_running"):
                record("ollama", "Start local model server", "done", "Ollama is running.")
            elif media.get("ollama_bin"):
                record("ollama", "Start local model server", "error", "Ollama is installed but did not start. Open it once, then recheck.")
            elif install_missing:
                try:
                    self.media.setup({"target": "ollama", "approved": True})
                    record("ollama", "Install local model server", "installing", "Ollama installation started in the background.")
                except Exception as exc:
                    record("ollama", "Install local model server", "error", str(exc))
            else:
                record("ollama", "Local model server", "optional", "Ollama is not installed. Needed only for local models, embeddings and video analysis.", "install:ollama")
            # ffmpeg/whisper for media
            if media.get("ffmpeg"):
                record("ffmpeg", "Media tools", "done", "FFmpeg is ready.")
            elif install_missing:
                try:
                    self.media.setup({"target": "ffmpeg", "approved": True})
                    record("ffmpeg", "Install media tools", "installing", "FFmpeg installation started in the background.")
                except Exception as exc:
                    record("ffmpeg", "Install media tools", "error", str(exc))
            else:
                record("ffmpeg", "Media tools", "optional", "FFmpeg is not installed. Needed only to analyze audio/video.", "install:ffmpeg")
            # Enable video enrichment defaults without a wall of toggles.
            try:
                self.media.configure({**cfg, "enabled": True, "vision_enabled": True, "speech_enabled": True})
            except Exception:
                pass
        except Exception as exc:
            record("ollama", "Local model server", "error", str(exc))

        # 3. Local semantic search (embeddings) when the server + model are present.
        try:
            media = self.media.status()
            installed = [m.lower() for m in media.get("installed_models", [])]
            settings = self.settings.get()
            embed_model = str(settings.get("embedding_model", "nomic-embed-text")).lower()
            has_embed = any(embed_model.split(":")[0] in m for m in installed)
            if media.get("ollama_running") and has_embed:
                self.settings.save({**settings, "use_local_embeddings": True})
                record("embeddings", "Enable semantic search", "done", "Local embeddings are on; knowledge search is now hybrid (keyword + meaning).")
            elif media.get("ollama_running") and install_missing:
                record("embeddings", "Enable semantic search", "needs_you",
                       f"Pull the embedding model once: run  ollama pull {embed_model}  then recheck.", "pull:embedding")
            else:
                record("embeddings", "Semantic search", "optional", "Keyword search already works. Install Ollama + an embedding model to add meaning-based search.")
        except Exception as exc:
            record("embeddings", "Semantic search", "error", str(exc))

        # 4. Policy gateway (needed for live web testing).
        try:
            import time as _t
            gateway = self.store.setting("gateway", {})
            fresh = gateway.get("verified_at") and _t.time() - gateway["verified_at"] < 300
            if not fresh:
                port = int(self.settings.get().get("gateway_port") or 8091)
                self.tools.gateway_start(port=port)
                self.tools.gateway_verify_direct()
            record("gateway", "Connect web-testing gateway", "done", "Built-in local gateway is running and verified.")
        except Exception as exc:
            record("gateway", "Connect web-testing gateway", "optional",
                   "Could not auto-connect the gateway. It is only needed to send live web requests; everything else works without it.")

        # 5. macOS permissions that only a human can grant.
        if platform.system() == "Darwin":
            if self._full_disk_access():
                record("full_disk", "Full Disk Access", "done", "Granted. The app can read your files for analysis.")
            else:
                record("full_disk", "Full Disk Access", "needs_you",
                       "macOS only lets you grant this by hand. Click Open to jump to the exact pane, enable BugHunterClaudex, then recheck.", "open:full_disk")
            record("ax", "Screen Recording & Accessibility", "needs_you",
                   "Needed only for desktop control and inspecting other apps. Grant in the same Privacy pane when you want those.", "open:accessibility")

        done = sum(1 for s in steps if s["status"] == "done")
        blocking = [s for s in steps if s["status"] == "needs_you"]
        return {"steps": steps, "done": done, "total": len(steps),
                "needs_you": len(blocking),
                "summary": f"Set up {done} of {len(steps)} automatically." +
                           (f" {len(blocking)} item(s) need a one-time macOS permission you must toggle yourself." if blocking else " Everything the app can do on its own is ready.")}

    @staticmethod
    def _addr(value):
        if isinstance(value, str):
            return int(value, 16) if value.lower().startswith("0x") else int(value)
        return int(value)

    def _memory_dispatch(self, op: str, p: dict):
        """Route memory.engine.* RPCs to the native macOS memory engine."""
        if not self.memory:
            raise ValueError("The native memory engine is only available on macOS.")
        m = self.memory
        pid = int(p["pid"]) if "pid" in p else None
        if op == "processes":
            return m.list_processes(p.get("filter"))
        if op == "attach":
            return m.attach(pid)
        if op == "detach":
            return m.detach(pid)
        if op == "scan":
            return m.scan(pid, p.get("type", "int"), value=p.get("value"),
                          between=p.get("between"), unknown=bool(p.get("unknown", False)))
        if op == "next_scan":
            return m.next_scan(pid, p.get("comparison", "exact"), value=p.get("value"))
        if op == "results":
            return m.results(pid, int(p.get("limit", 100)))
        if op == "read":
            return m.read_value(pid, self._addr(p["address"]), p.get("type", "int"))
        if op == "write":
            return m.write_value(pid, self._addr(p["address"]), p["value"], p.get("type", "int"))
        if op == "freeze":
            return m.freeze(pid, self._addr(p["address"]), p["value"], p.get("type", "int"),
                            enable=bool(p.get("enable", True)))
        if op == "modules":
            return m.modules(pid, p.get("filter"))
        if op == "resolve_chain":
            return m.resolve_chain(pid, self._addr(p["base"]), [self._addr(o) for o in p.get("offsets", [])],
                                   p.get("type", "int"))
        raise ValueError(f"Unknown memory engine operation: {op}")

    def delete_project(self, id):
        project = self.policy.project(id)
        for job in self.store.list('mapping_research', project_id=id):
            self.memory_mappings.stop(job['id'])
        mapping_receipts = [self.memory_mappings.receipts / (check['id'] + '.json')
                            for check in self.store.list('mapping_check', project_id=id)]
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
        kinds = ("traffic","test","action","finding","run","asset","observation","script_draft","reviewed_skill","work_task","work_task_event","research_watch","research_discovery","mapping_research","mapping_candidate","mapping_check","mapping_chat")
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
        for receipt in mapping_receipts:
            receipt.unlink(missing_ok=True)
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
        self.memory_mappings.stop_all()
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
        if self.memory:
            self.memory.shutdown()
        if self._owner_file:
            self._owner_file.close()
            self._owner_file = None

    def close(self):
        """Compatibility entry point for callers using the standard resource API."""
        self.shutdown()
