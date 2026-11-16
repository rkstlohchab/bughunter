"""Durable game operations. Native execution belongs to the private desktop bridge."""
from __future__ import annotations
from functools import wraps
import hashlib
import hmac
import json
import math
import os
import re
import threading
import time
from pathlib import Path

from .broker import GameRuntimeBroker, BrokerError, TERMINAL
from .targets import discover_installed, verify_target_files
from .contracts import GameTarget
from ..memory_engine.protocol import read_process_start_time_ns
from ..store import new_id


def fingerprint(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(',', ':')).encode()).hexdigest()


def birth(pid):
    if type(pid) is not int or pid <= 0:
        raise ValueError('Choose a running process ID.')
    value = read_process_start_time_ns(pid)
    if value <= 0:
        raise ValueError('Process birth identity is unavailable.')
    return f'{pid}:{value}'


def alive(identity):
    try:
        pid, _ = identity.split(':', 1)
        return birth(int(pid)) == identity
    except (ValueError, OSError, RuntimeError):
        return False


def helper_alive(identity):
    if alive(identity):
        return True
    if os.name == 'posix':
        try:
            os.killpg(int(identity.split(':', 1)[0]), 0)
            return True
        except ProcessLookupError:
            pass
        except PermissionError:
            return True
    return False


def _serialized_service(method):
    @wraps(method)
    def call(self,*args,**kwargs):
        with self._lock:
            return method(self,*args,**kwargs)
    return call


class GameRuntimeService:
    def __init__(self, app, credential=None, recover=False):
        self.app, self.store = app, app.store
        self.broker = GameRuntimeBroker(self.store)
        self._credential = credential if isinstance(credential,str) and re.fullmatch('[0-9a-f]{64}',credential) else None
        self._bootstrapped = False
        self._lock = threading.RLock()
        self._fixtures = {}
        self._last_poll = 0.0
        if recover:
            self.broker.stop()
            self.reconcile()

    @property
    def available(self):
        return self._bootstrapped and time.monotonic()-self._last_poll < 5

    def reconcile(self):
        for executor in self.broker.list_executors():
            if executor['state'] != 'dead' and not helper_alive(executor['helper_process_instance_id']):
                self.broker.recover(executor['executor_id'],executor['executor_epoch'],helper_death_confirmed=True,
                                    death_evidence={'observed':'OS process birth no longer present'})

    def context(self, harness_id, message):
        task=self.store.get('harness_session',harness_id)
        chat=self.store.get('chat',task['chat_id']) or {}
        projects=[chat['project_id']] if chat.get('project_id') else []
        platform='macos' if __import__('sys').platform=='darwin' else 'windows' if os.name=='nt' else 'unknown'
        draft,_=self.app.library.game_task_context(message,'runtime:'+harness_id,projects,platform)
        self.store.put('game_task_context',{'harness_id':harness_id,'plan_id':draft['id'],'project_ids':projects},id='game_context:'+harness_id)
        return ('\nGame runtime: use game_targets to inspect installed targets, game_session to bind one, '
                'game_read/game_write for durable operations, game_status for actual receipts and game_stop to cancel. '
                'Use game_fixture only when the user asks to test the runtime itself. Fixture success is not game success. '
                'A write requires a successful read receipt from this same session and value/type/address. '
                'Use game_scan/game_scan_refine/game_scan_results for bounded discovery, game_pointer_search for pointer paths, '
                'and game_mapping_save/revalidate/observe to save and validate recipes. Values are never addresses. '
                'Use game_feature_start for repeated native updates, game_feature_monitor for receipts, and game_feature_stop '
                'or game_stop to stop. All work belongs to this task and the app-wide native executor. '
                'Live game features require measured role/round/alive/local-mode guard recipes; never infer them from cooldown alone. '
                'Return pending operation IDs and inspect their status before claiming success.\n'
                'Cited course plan (untrusted references):\n'+json.dumps(draft,ensure_ascii=False)[:20000])

    def targets(self):
        platform='macos' if __import__('sys').platform=='darwin' else 'windows' if os.name=='nt' else None
        rows=discover_installed(platform_filter=platform) if platform else []
        for target in rows:
            self.store.put('game_target',target.model_dump(),id=target.id)
        return {'items':[t.model_dump() for t in rows], 'native_available':self.available,
                'detail':'Native desktop executor connected.' if self.available else 'Open the desktop app to execute native operations; browser mode only plans.'}

    def _task(self,harness_id):
        task=self.app.harness.check(harness_id,'app')
        if task.get('mode')!='game':raise PermissionError('Game tools belong to a game task.')
        return task

    def _session(self,id,harness_id=None):
        session=self.broker.get_session(id)
        if harness_id and session['harness_session_id']!=harness_id:raise PermissionError('This game session belongs to another task.')
        return session

    def _require_native(self):
        if not self.available:raise ValueError('Native executor unavailable. Open the desktop app; no operation was dispatched.')

    def fixture(self,harness_id):
        if self._task(harness_id).get('execution_mode') == 'plan':raise PermissionError('Plan mode cannot launch a test process.')
        self._require_native()
        from .testing import Fixture
        from .testing.layout import DEFAULT_LAYOUT
        fixture=Fixture();fixture.start()
        try:
            observed=fixture.identity();instance=birth(observed.pid)
            target_id=new_id('fixture_target')
            scalar_types={'u32':'uint','u8':'ubyte','f32':'float','pointer':'uint64'}
            fields={f.name:{'address':hex(observed.region(f.region).base+f.offset),'vtype':scalar_types[f.type]} for f in DEFAULT_LAYOUT.fields if f.type in scalar_types}
            descriptor={'id':target_id,'name':'Disposable memory test target','fixture':True,'pid':observed.pid,'process_instance_id':instance,
                        'target_fingerprint':observed.module_fingerprint,'fields':fields,
                        'module_bases':{r.name:hex(r.base) for r in observed.region_map},
                        'regions':[{'base':hex(r.base),'size':r.size} for r in observed.region_map]}
            self.store.put('game_runtime_target',descriptor,id=target_id)
            self._fixtures[target_id]=fixture
            session=self.start_session(harness_id,target_id,observed.pid)
            return {'session':session,'target':descriptor,'limitation':'Disposable fixture only; this does not change a game.'}
        except BaseException:
            fixture.shutdown();raise

    def start_session(self,harness_id,target_id,pid=None):
        task=self._task(harness_id);self._require_native()
        if self.store.setting('emergency_stop',False):raise InterruptedError('Tasks are stopped.')
        target=self.store.get('game_runtime_target',target_id)
        if target and target.get('fixture'):
            if target_id not in self._fixtures:raise ValueError('Test process expired. Start a new test target.')
            pid=target['pid'];instance=birth(pid)
            if instance!=target['process_instance_id']:raise ValueError('Test process identity changed.')
            digest=target['target_fingerprint']
        else:
            record=self.store.get('game_target',target_id)
            if not record:raise ValueError('Inspect installed game targets first.')
            target_model=GameTarget.model_validate({k:v for k,v in record.items() if k not in ('created_at','updated_at')})
            inputs=verify_target_files(target_model)
            if target_model.platform!='macos':raise ValueError('This controller currently qualifies macOS sessions; Windows needs its own runtime acceptance.')
            from .targets import _mac_find_pid
            executable=next(r['path'] for r in inputs if r['role']=='main')
            pid=pid if pid is not None else _mac_find_pid(Path(executable).name)
            if not pid:raise ValueError('The selected game is not running. Start it before creating a native session.')
            import ctypes
            lib=ctypes.CDLL('/usr/lib/libproc.dylib');lib.proc_pidpath.argtypes=[ctypes.c_int,ctypes.c_void_p,ctypes.c_uint32];lib.proc_pidpath.restype=ctypes.c_int
            buf=ctypes.create_string_buffer(4096)
            instance=birth(pid)
            if lib.proc_pidpath(pid,buf,len(buf))<=0:raise ValueError('Process executable path unavailable.')
            from .live_target import verify_running_bundle
            measured_inputs=verify_running_bundle(Path(os.fsdecode(buf.value)),Path(target_model.executable_or_package),inputs)
            if birth(pid)!=instance:raise ValueError('Process changed while qualifying its running bundle.')
            digest=fingerprint(target_model.build_fingerprint.model_dump())
            target={'id':target_id,'name':target_model.game_id,'fixture':False,'pid':pid,'process_instance_id':instance,
                    'target_fingerprint':digest,'running_inputs':measured_inputs}
        for existing in self.broker.list_sessions():
            if existing['harness_session_id']==harness_id and existing['process_instance_id']==instance and existing['state']=='running':return existing
        context=self.store.get('game_task_context','game_context:'+harness_id) or {}
        session=self.broker.create_session(target_id,harness_id,instance,digest,context.get('project_ids',[]))
        self.store.put('game_runtime_session',{'descriptor':target,'knowledge_plan_id':context.get('plan_id')},id=session['id'])
        return session

    def read(self,session_id,address,vtype='uint',harness_id=None):
        session=self._session(session_id,harness_id);self._task(session['harness_session_id']);self._require_native()
        args=self._value_args(address,vtype)
        return self.broker.enqueue(session_id,'read','read_only',args)

    @staticmethod
    def _value_args(address,vtype):
        from ..memory_engine.mac_adapter import _SCALAR_FMT
        if not isinstance(address,str) or not re.fullmatch(r'0x[0-9a-fA-F]{1,16}',address) or int(address,16)==0:raise ValueError('Use a measured nonzero hexadecimal address string.')
        if vtype not in _SCALAR_FMT:raise ValueError('Choose a supported native value type.')
        return {'address':hex(int(address,16)),'vtype':vtype}

    def write(self,session_id,read_operation_id,value,harness_id=None,restore_on_stop=False):
        session=self._session(session_id,harness_id);task=self._task(session['harness_session_id']);self._require_native()
        if task.get('execution_mode')=='plan':raise PermissionError('Plan mode cannot change memory.')
        if any(self.broker.get_operation(row['operation_id'])['status'] not in TERMINAL for row in self.broker.cleanup_operations(session_id)):
            raise ValueError('This test value already has a pending restoration. Stop this session before another write.')
        if type(value) not in (int,float) or not math.isfinite(value):raise ValueError('Enter a finite numeric value.')
        read=self.broker.get_operation(read_operation_id)
        if read['session_id']!=session_id or read['kind']!='read' or read['status']!='succeeded' or not read['result'] or 'value' not in read['result']:raise ValueError('Use a successful read receipt from this session.')
        if not alive(session['process_instance_id']):raise ValueError('Target process changed; old reads cannot authorize a write.')
        args={**self._value_args(read['arguments']['address'],read['arguments']['vtype']),'value':value,'expected_value':read['result']['value'],'verify':True}
        descriptor=(self.store.get('game_runtime_session',session_id) or {}).get('descriptor',{})
        if restore_on_stop and not descriptor.get('fixture'):raise ValueError('Automatic restoration is defined only for the static test target, not game countdowns.')
        if restore_on_stop:
            return self.broker.enqueue_with_cleanup(session_id,args,{**args,'value':args['expected_value'],'expected_value':value},'manual_value')
        return self.broker.enqueue(session_id,'write','mutating',args,['same_process_birth','value_equals_read_receipt'],feature_id='manual_value')

    def status(self,harness_id=None,session_id=None):
        sessions=[s for s in self.broker.list_sessions() if (not harness_id or s['harness_session_id']==harness_id) and (not session_id or s['id']==session_id)]
        # Advertise implemented mechanics separately from qualified game behavior.
        return {'native_available':self.available, 'capabilities': {'durable_read_write':self.available, 'chat_scans':self.available, 'chat_pointer_discovery':self.available, 'chat_feature_lifecycle':self.available, 'chat_no_cooldown_lifecycle':False, 'chat_mappings':True, 'chat_candidate_search':self.available, 'scan_result_values_implemented':True, 'pointer_search_result_values_implemented':True}, 'sessions':[dict(s,operations=self.broker.operations(s['id']),cleanup_operation_ids=[r['operation_id'] for r in self.broker.cleanup_operations(s['id'])],features=self.broker.features(s['id']),mappings=self._mappings_for_session(s['id']),feature_runs=self.store.list('game_feature_schedule',session_id=s['id']),**(self.store.get('game_runtime_session',s['id']) or {})) for s in sessions]}

    # --- P05/P06 broker-owned chat surfaces --------------------------------

    def _mapping_store(self):
        from .mappings import MappingStore
        return MappingStore(self.store)

    def _mappings_for_session(self, session_id: str) -> list[dict]:
        try:
            fingerprint = self.broker.session_fingerprint(session_id)
        except Exception:
            return []
        try:
            return [m.to_contract() for m in self._mapping_store().list_for_target(fingerprint)]
        except Exception:
            return []

    def mapping_save(self, session_id: str, *, name: str, root: str, steps: list,
                     value_type: str, preconditions=(), evidence_ids=(),
                     harness_id: str | None = None) -> dict:
        """Persist a MappingRecipe bound to this session's target fingerprint.

        In-process operation (no native work). Still task-scoped so the
        chat-visible activity shows which task authored the recipe.
        """
        session = self._session(session_id, harness_id)
        self._task(session['harness_session_id'])
        if self._task(session['harness_session_id']).get('execution_mode')=='plan':raise PermissionError('Plan mode cannot save mappings.')
        if session['state']!='running':raise ValueError('Game session is stopped.')
        fingerprint = self.broker.session_fingerprint(session_id)
        if root.startswith('abs:'):
            preconditions=list(preconditions)+['process_instance:'+session['process_instance_id']]
        recipe = self._mapping_store().save_new(
            name=name, target_fingerprint=fingerprint,
            value_type=value_type, root=root, steps=list(steps),
            preconditions=list(preconditions), evidence_ids=list(evidence_ids),
        )
        self.store.event('game_runtime.mapping_save', {
            'session_id': session_id, 'recipe_id': recipe.id, 'revision': recipe.revision,
        })
        return recipe.to_contract()

    def mapping_list(self, session_id: str, *, harness_id: str | None = None) -> list[dict]:
        session = self._session(session_id, harness_id)
        self._task(session['harness_session_id'])
        return self._mappings_for_session(session_id)

    def _recipe_args(self, session, recipe):
        if recipe.target_fingerprint != self.broker.session_fingerprint(session['id']):
            raise ValueError('recipe was authored for a different build fingerprint')
        if recipe.verification_status == 'stale':
            raise ValueError('recipe is stale; revise it before use')
        descriptor=(self.store.get('game_runtime_session',session['id']) or {}).get('descriptor',{})
        args={'recipe':recipe.to_contract()}
        if descriptor.get('fixture'):
            args['fixture_module_bases']=descriptor.get('module_bases',{})
        return args

    def _active_task(self, session_id, harness_id=None, *, mutate=False):
        session=self._session(session_id,harness_id)
        task=self._task(session['harness_session_id'])
        if session['state']!='running':raise ValueError('Game session is stopped.')
        if mutate and task.get('execution_mode')=='plan':raise PermissionError('Plan mode cannot change memory.')
        return session

    def mapping_revalidate(self, session_id, recipe_id, *, harness_id=None):
        session=self._active_task(session_id,harness_id);self._require_native()
        recipe=self._mapping_store().get(recipe_id)
        args={**self._recipe_args(session,recipe),'runtime_action':'resolve'}
        op=self.broker.enqueue(session_id,'read','read_only',args)
        return {'operation_id':op['id'],'recipe_id':recipe.id,'revision':recipe.revision}

    def mapping_observe(self, session_id, recipe_id, operation_id, *, harness_id=None):
        session=self._active_task(session_id,harness_id)
        op=self.broker.get_operation(operation_id)
        recipe=self._mapping_store().get(recipe_id)
        result=op.get('result') or {}
        if (op['session_id']!=session_id or op['kind']!='read' or op['status']!='succeeded'
            or op['arguments'].get('runtime_action')!='resolve'
            or result.get('recipe_id')!=recipe_id or result.get('recipe_revision')!=recipe.revision
            or op['arguments'].get('recipe')!=recipe.to_contract()
            or result.get('process_instance_id')!=session['process_instance_id']
            or result.get('target_fingerprint')!=recipe.target_fingerprint or 'value' not in result):
            raise ValueError('Use a successful current-revision native resolver receipt from this session.')
        updated=self._mapping_store().observe(recipe_id,session_id=session_id,
            target_fingerprint=self.broker.session_fingerprint(session_id),
            process_instance_id=session['process_instance_id'],resolved_value=result['value'],expected_revision=recipe.revision,
            notes='Native resolver receipt '+operation_id+' at '+str(result.get('address')))
        self.store.put('game_mapping_binding',{'session_id':session_id,'recipe_id':recipe_id,'recipe_revision':recipe.revision,
            'operation_id':operation_id,'process_instance_id':session['process_instance_id'],
            'address':result['address'],'path':result['intermediate_addresses']},id=session_id+':'+recipe_id)
        return updated.to_contract()

    def mapping_export(self, session_id, recipe_ids=None, *, harness_id=None):
        session=self._active_task(session_id,harness_id)
        ms=self._mapping_store()
        ids=recipe_ids if recipe_ids is not None else [r.id for r in ms.list_for_target(self.broker.session_fingerprint(session_id))]
        for recipe_id in ids:
            if ms.get(recipe_id).target_fingerprint!=self.broker.session_fingerprint(session_id):
                raise ValueError('mapping belongs to a different build')
        return ms.export_recipes(ids)

    def mapping_import(self, session_id, payload, *, harness_id=None):
        session=self._active_task(session_id,harness_id,mutate=True)
        return [r.to_contract() for r in self._mapping_store().import_recipes(payload,
                target_fingerprint=self.broker.session_fingerprint(session_id))]

    def _discovery_args(self, session):
        descriptor=(self.store.get('game_runtime_session',session['id']) or {}).get('descriptor',{})
        return {'regions':descriptor['regions']} if descriptor.get('fixture') and descriptor.get('regions') else {}

    def scan_start(self, session_id, *, value_type, mode, needle=None, low=None, high=None,
                   byte_pattern=None, harness_id=None):
        session=self._active_task(session_id,harness_id);self._require_native()
        from .scans import _SCALAR_FMT
        if value_type not in {*_SCALAR_FMT,'bytes'}:raise ValueError('unsupported scan value type')
        if mode not in ('exact','range','unknown_initial','byte_pattern'):raise ValueError('unsupported scan mode')
        if mode=='byte_pattern':
            if value_type!='bytes' or not isinstance(byte_pattern,str):raise ValueError('byte pattern requires bytes type and hex text')
            raw=bytes.fromhex(byte_pattern)
            if not 1<=len(raw)<=4096:raise ValueError('pattern size must be 1..4096 bytes')
        else:
            import struct
            if value_type=='bytes':raise ValueError('bytes type requires byte_pattern mode')
            fmt,_=_SCALAR_FMT[value_type]
            values=[needle] if mode=='exact' else [low,high] if mode=='range' else []
            for value in values:
                if type(value) not in (int,float) or not math.isfinite(value):raise ValueError('scan requires finite numeric values')
                struct.pack(fmt,value)
            if mode=='range' and low>high:raise ValueError('low exceeds high')
        args={**self._discovery_args(session),'scan_mode':mode,'value_type':value_type,'needle':needle,
              'low':low,'high':high,'byte_pattern_hex':byte_pattern}
        op=self.broker.enqueue(session_id,'scan','read_only',args)
        return {'operation_id':op['id'],'scan_mode':mode}

    def scan_control(self,session_id,scan_id,*,mode='results',refinement=None,needle=None,byte_pattern=None,cursor=None,harness_id=None):
        session=self._active_task(session_id,harness_id);self._require_native()
        if mode not in ('results','refine','close'):raise ValueError('unsupported scan control')
        if not isinstance(scan_id,str) or not scan_id:raise ValueError('scan_id required')
        # Only IDs returned by a successful discovery in this task can be reused.
        if not any(o['status']=='succeeded' and (o.get('result') or {}).get('scan_id')==scan_id
                   for o in self.broker.operations(session_id)):
            raise ValueError('Scan belongs to another task or has no successful receipt.')
        args={'scan_mode':mode,'scan_id':scan_id,'refinement':refinement,'needle':needle,'byte_pattern_hex':byte_pattern,'cursor':cursor}
        op=self.broker.enqueue(session_id,'scan','read_only',args)
        return {'operation_id':op['id'],'scan_id':scan_id}

    def pointer_search(self,session_id,address,*,depth=6,offset_window=4096,harness_id=None):
        session=self._active_task(session_id,harness_id);self._require_native()
        self._value_args(address,'uint64')
        if type(depth) is not int or not 1<=depth<=12 or type(offset_window) is not int or not 0<=offset_window<=4096:
            raise ValueError('pointer depth is 1..12 and offset window 0..4096')
        args={**self._discovery_args(session),'target_address':address,'depth':depth,'offset_window':offset_window}
        descriptor=(self.store.get('game_runtime_session',session_id) or {}).get('descriptor',{})
        if descriptor.get('fixture'):
            args['module_ranges']=[{'name':name,'base':base,'size':next(r['size'] for r in descriptor['regions'] if r['base']==base)}
                                  for name,base in descriptor.get('module_bases',{}).items()]
        op=self.broker.enqueue(session_id,'pointer_search','read_only',args)
        return {'operation_id':op['id']}

    def candidate_search(self,session_id,*,kind='player_control',cooldown_hint=None,max_candidates=32,harness_id=None):
        session=self._active_task(session_id,harness_id);self._require_native()
        if kind!='player_control':raise ValueError('unsupported candidate kind')
        if type(max_candidates) is not int or not 1<=max_candidates<=64:raise ValueError('max_candidates must be 1..64')
        if cooldown_hint is not None and (type(cooldown_hint) not in (int,float) or not math.isfinite(cooldown_hint) or not 0<cooldown_hint<=120):
            raise ValueError('invalid cooldown hint')
        op=self.broker.enqueue(session_id,'pointer_search','read_only',{'search_mode':'among_us_candidates','cooldown_hint':cooldown_hint,'max_candidates':max_candidates})
        return {'operation_id':op['id'],'candidate_kind':kind}

    @_serialized_service
    def feature_start(self,session_id,*,recipe_id,feature_id,apply_value,disable_value=None,guards=None,harness_id=None):
        session=self._active_task(session_id,harness_id,mutate=True);self._require_native()
        ms=self._mapping_store();recipe=ms.get(recipe_id)
        args=self._recipe_args(session,recipe)
        if not recipe.observations or recipe.observations[-1].process_instance_id!=session['process_instance_id']:
            raise ValueError('recipe has not been observed on this process; revalidate and observe first')
        if not isinstance(feature_id,str) or not re.fullmatch('[A-Za-z0-9_-]{1,80}',feature_id):raise ValueError('invalid feature id')
        for value in (apply_value,disable_value):
            if value is not None and (type(value) not in (int,float) or not math.isfinite(value)):raise ValueError('feature values must be finite')
        if apply_value is None:raise ValueError('apply_value required')
        observed=self.store.get('game_mapping_binding',session_id+':'+recipe_id)
        if not observed or observed['recipe_revision']!=recipe.revision or observed['process_instance_id']!=session['process_instance_id']:
            raise ValueError('recipe needs a current native resolver observation before feature start')
        args['observed_path']=observed['path']
        descriptor=(self.store.get('game_runtime_session',session_id) or {}).get('descriptor',{})
        validated=[]
        if not descriptor.get('fixture'):
            required={'local_mode','impostor_role','alive','round_identity'}
            if not isinstance(guards,list) or {g.get('name') for g in guards}!=required:
                raise ValueError('Live features require measured local_mode, impostor_role, alive and round_identity guards.')
            for guard in guards:
                saved=ms.get(guard['recipe_id']);self._recipe_args(session,saved)
                if not saved.observations or saved.observations[-1].process_instance_id!=session['process_instance_id']:
                    raise ValueError('runtime guards must be observed on this process')
                binding=self.store.get('game_mapping_binding',session_id+':'+saved.id)
                if not binding or binding['recipe_revision']!=saved.revision:raise ValueError('guard requires a current native resolver observation')
                validated.append({'name':guard['name'],'recipe':saved.to_contract(),'expected':guard['expected'],'observed_path':binding['path']})
            if disable_value is not None:raise ValueError('Live countdown Stop uses natural reset, not a captured disable value.')
        existing=self.store.get('game_feature_schedule',session_id+':'+feature_id)
        if existing:
            feature=next((f for f in self.broker.features(session_id) if f['feature_id']==feature_id),None)
            if existing['enabled'] or not feature or feature['state']!='stopped' or feature['cleanup_status']!='pass':
                raise ValueError('Stop the previous feature and confirm cleanup before starting again.')
            self.broker.restart_feature(session_id,feature_id)
        args.update(runtime_action='feature_start',feature_id=feature_id,value=apply_value,disable_value=disable_value,guards=validated,lifecycle_id=new_id('lifecycle'))
        cleanup={**args,'runtime_action':'feature_stop'}
        op=self.broker.enqueue_with_cleanup(session_id,args,cleanup,feature_id)
        self.store.put('game_feature_schedule',{'session_id':session_id,'feature_id':feature_id,'recipe_id':recipe.id,
            'recipe_revision':recipe.revision,'arguments':args,'last_operation_id':op['id'],'enabled':True,'next_tick':0},id=session_id+':'+feature_id)
        return {'operation_id':op['id'],'feature_id':feature_id,'cleanup_declared':True,'repeat_interval_ms':500}

    def feature_monitor(self,session_id,*,recipe_id,feature_id,harness_id=None):
        session=self._active_task(session_id,harness_id);self._require_native()
        schedule=self.store.get('game_feature_schedule',session_id+':'+feature_id)
        if not schedule or not schedule['enabled'] or schedule['recipe_id']!=recipe_id:raise ValueError('feature is not running')
        args={**schedule['arguments'],'runtime_action':'feature_monitor'}
        op=self.broker.enqueue(session_id,'read','read_only',args,feature_id=feature_id)
        return {'operation_id':op['id'],'feature_id':feature_id}

    def _pump_features(self):
        # Poll comes only from the app-owned native executor. Each tick has a
        # durable intent/receipt and no timer thread can outlive task ownership.
        with self._lock:
            for schedule in self.store.list('game_feature_schedule',enabled=True):
                session=self.broker.get_session(schedule['session_id'])
                feature=next((f for f in self.broker.features(session['id']) if f['feature_id']==schedule['feature_id']),None)
                if session['state']!='running' or not feature or feature['state'] in ('stopped','stopping','interrupted'):
                    schedule['enabled']=False
                else:
                    op=self.broker.get_operation(schedule['last_operation_id'])
                    if op['status'] in ('failed','outcome_unknown','cancelled_before_dispatch') or feature['state']=='failed':
                        schedule['enabled']=False
                        self.broker.stop(session['id'],schedule['feature_id'])
                    elif op['status']=='succeeded':
                        ms=self._mapping_store()
                        bindings=[schedule['arguments']['recipe']]+[g['recipe'] for g in schedule['arguments'].get('guards',[])]
                        def changed(binding):
                            try:
                                current=ms.get(binding['id'])
                                return current.revision!=binding['revision'] or current.verification_status=='stale'
                            except KeyError:return True
                        if self.store.setting('emergency_stop',False) or any(changed(binding) for binding in bindings):
                            schedule['enabled']=False;self.broker.stop(session['id'],schedule['feature_id'])
                        elif schedule.get('last_completed_operation_id')!=op['id']:
                            schedule.update(last_completed_operation_id=op['id'],next_tick=time.monotonic()+0.5)
                        elif time.monotonic()>=schedule['next_tick']:
                            args={**schedule['arguments'],'runtime_action':'feature_tick'}
                            try:
                                tick=self.broker.enqueue(session['id'],'write','mutating',args,feature_id=schedule['feature_id'])
                                schedule.update(last_operation_id=tick['id'])
                            except BrokerError:schedule['enabled']=False
                self.store.put('game_feature_schedule',schedule,id=schedule['id'])

    @_serialized_service
    def stop(self,session_id=None,harness_id=None,feature_id=None):
        if session_id:self._session(session_id,harness_id)
        sessions=[self.broker.get_session(session_id)] if session_id else [s for s in self.broker.list_sessions() if not harness_id or s['harness_session_id']==harness_id]
        for schedule in self.store.list('game_feature_schedule',enabled=True):
            if any(s['id']==schedule['session_id'] for s in sessions) and (feature_id is None or schedule['feature_id']==feature_id):
                self.store.put('game_feature_schedule',{**schedule,'enabled':False},id=schedule['id'])
        global_stop=session_id is None and harness_id is None
        if global_stop:self.broker.stop()
        for session in sessions:
            if session['state'] not in ('stopped','interrupted'):
                if not global_stop:self.broker.stop(session['id'],feature_id)
                owners=[e for e in self.broker.list_executors() if e['process_instance_id']==session['process_instance_id'] and e['state']!='dead']
                if not owners:
                    with self.store.connection(immediate=True) as db:db.execute('UPDATE gr_sessions SET quiesced=1 WHERE id=?',(session['id'],))
                    self.broker.finish_stop(session['id'],feature_id)
        return self.status(harness_id,session_id)

    def _auth(self,p):
        supplied=p.get('credential_hex')
        if not self._credential or not isinstance(supplied,str) or not hmac.compare_digest(supplied,self._credential):raise PermissionError('Private desktop credential required.')
        if not self._bootstrapped:raise PermissionError('Private desktop bootstrap required.')

    def private(self,method,p):
        with self._lock:
            if method=='gamelab.native.bootstrap':
                supplied=p.get('credential_hex','')
                if self._bootstrapped or not self._credential or not isinstance(supplied,str) or not hmac.compare_digest(supplied,self._credential):raise PermissionError('Invalid private bootstrap.')
                self._bootstrapped=True;self._last_poll=time.monotonic()
                return {'ready':True,'protocol_version':1}
            self._auth(p)
        args={k:v for k,v in p.items() if k!='credential_hex'}
        if method=='gamelab.native.stop_all':return self.stop()
        if method=='gamelab.native.poll':
            self._last_poll=time.monotonic();self.reconcile();self._pump_features();sessions=[]
            for s in self.broker.list_sessions():
                if s['state'] not in ('running','starting','stopping'):continue
                descriptor=(self.store.get('game_runtime_session',s['id']) or {}).get('descriptor',{})
                sessions.append({**s,'session_id':s['id'],'pid':descriptor.get('pid'),'target_key':s['process_instance_id'],'target_fingerprint':self.broker.session_context(s['id'])['target_fingerprint'],'stop_requested':s['state']=='stopping'})
            return {'sessions':sessions,'executors':self.broker.list_executors(),'stopping':self.store.setting('emergency_stop',False)}
        if method=='gamelab.native.register':
            helper=birth(args['helper_pid'])
            if helper!=f"{args['helper_pid']}:{args['helper_start_time_ns']}" or not alive(args['process_instance_id']):raise ValueError('Helper or target process birth changed.')
            matches=[s for s in self.broker.list_sessions() if s['process_instance_id']==args['process_instance_id'] and s['state']=='running' and self.broker.session_context(s['id'])['target_fingerprint']==args['target_fingerprint']]
            if not matches:raise ValueError('No active session matches this executor target.')
            return self.broker.register_executor(args['executor_id'],'local-controller',helper,args['process_instance_id'],args['target_fingerprint'])
        if method=='gamelab.native.heartbeat':
            value=self.broker.heartbeat(**args)
            sessions=[s for s in self.broker.list_sessions() if s['process_instance_id']==value['process_instance_id']]
            value['stop_requested']=any(s['state']=='stopping' or any(f['state']=='stopping' for f in self.broker.features(s['id'])) for s in sessions) or bool(value['stop_requests']) or value['state']!='active' or not alive(value['process_instance_id'])
            return value
        if method=='gamelab.native.claim':return {'operation':self.broker.claim(**args)}
        if method=='gamelab.native.dispatched':return {'operation':self.broker.dispatched(**args)}
        if method=='gamelab.native.complete':
            # Cancellation may have advanced revision while the native call returned.
            # A receipt still must carry the current revision; Rust refreshes it.
            with self._lock:
                operation=self.broker.complete(**args,accept_cancelled_attempt=True)
                if operation['arguments'].get('runtime_action') in ('feature_start','feature_tick'):
                    key=operation['session_id']+':'+operation['arguments']['feature_id']
                    schedule=self.store.get('game_feature_schedule',key)
                    if schedule and schedule['last_operation_id']==operation['id'] and operation['status']=='succeeded':
                        self.store.put('game_feature_schedule',{**schedule,'last_completed_operation_id':operation['id'],
                                                               'next_tick':time.monotonic()+0.5},id=key)
                return {'operation':operation}
        if method=='gamelab.native.quiescent':
            executor=self.broker.get_executor(args['executor_id'])
            if not executor or executor['executor_epoch']!=args['executor_epoch']:raise ValueError('Stale executor.')
            for s in self.broker.list_sessions():
                if s['process_instance_id']==executor['process_instance_id'] and (s['state']=='stopping' or any(f['state']=='stopping' for f in self.broker.features(s['id']))):self.broker.acknowledge_quiescence(s['id'],args['executor_id'],args['executor_epoch'])
            for row in self.broker.cleanup_operations():
                op=self.broker.get_operation(row['operation_id']);parent=self.broker.get_operation(row['parent_id'])
                if op['process_instance_id']!=executor['process_instance_id'] or op['status']!='queued' or parent['status']!='succeeded' or not any(f['state']=='stopping' for f in self.broker.features(op['session_id'])):continue
                self.broker.authorize_cleanup(op['id'],args['executor_id'],args['executor_epoch'],process_instance_id=executor['process_instance_id'],target_fingerprint=executor['target_fingerprint'],preconditions_passed=alive(executor['process_instance_id']))
                claimed=self.broker.claim(args['executor_id'],args['executor_epoch'],operation_id=op['id'])
                if claimed:return {'cleanup_operation':self.broker.dispatched(claimed['id'],args['executor_id'],args['executor_epoch'],claimed['attempt_id'],claimed['revision'])}
            return {'cleanup_operation':None}
        if method=='gamelab.native.finish_stop':
            executor=self.broker.get_executor(args['executor_id'])
            if not executor or executor['executor_epoch']!=args['executor_epoch']:raise ValueError('Stale executor.')
            for s in self.broker.list_sessions():
                if s['process_instance_id']!=executor['process_instance_id']:continue
                if s['state']=='stopping':self.broker.finish_stop(s['id'])
                else:
                    for f in self.broker.features(s['id']):
                        if f['state']=='stopping':self.broker.finish_stop(s['id'],f['feature_id'])
            resume=executor['state']=='active' and alive(executor['process_instance_id']) and any(s['process_instance_id']==executor['process_instance_id'] and s['state']=='running' for s in self.broker.list_sessions())
            return {'resume':resume,'stopped_task_session_ids':[s['id'] for s in self.broker.list_sessions() if s['process_instance_id']==executor['process_instance_id'] and s['state']=='stopped']}
        if method=='gamelab.native.stopped':
            executor=self.broker.get_executor(args['executor_id'])
            if not executor or executor['executor_epoch']!=args['executor_epoch']:raise ValueError('Stale executor.')
            if not args.get('helper_death_confirmed') or helper_alive(executor['helper_process_instance_id']):raise ValueError('Helper exit was not confirmed; target ownership retained.')
            stopped=[s['id'] for s in self.broker.list_sessions() if s['process_instance_id']==executor['process_instance_id'] and s['state']=='stopping']
            result=self.broker.recover(executor['executor_id'],executor['executor_epoch'],helper_death_confirmed=True,death_evidence={'os_birth_absent':True,'cleanup':args.get('cleanup_result')})
            for id in stopped:
                with self.store.connection(immediate=True) as db:
                    _,s=self.broker._session(db,id);s['state']='stopping';self.broker._put_session(db,s)
                self.broker.finish_stop(id)
            return {'stopped':True,'recovery':result}
        if method=='gamelab.native.failed':
            for s in self.broker.list_sessions():
                if s['process_instance_id']==args.get('process_instance_id') and s['state']=='running':
                    self.broker.stop(s['id'])
                    with self.store.connection(immediate=True) as db:
                        _,r=self.broker._session(db,s['id']);r.update(state='failed',last_error=str(args.get('error','Native attach failed'))[:500]);self.broker._put_session(db,r)
            return {'recorded':True}
        raise ValueError('Unknown private game runtime method.')

    def dispatch(self,method,p):
        if method.startswith('gamelab.native.'):return self.private(method,p)
        if method=='gamelab.targets.list':return self.targets()
        if method=='gamelab.sessions.start':return self.start_session(**p)
        if method=='gamelab.sessions.get':return self.status(session_id=p['session_id'])
        if method=='gamelab.sessions.stop':return self.stop(session_id=p['session_id'])
        if method=='gamelab.features.stop':return self.stop(session_id=p['session_id'],feature_id=p['feature_id'])
        if method=='gamelab.features.start':return self.feature_start(**p)
        if method=='gamelab.features.monitor':return self.feature_monitor(**p)
        if method=='gamelab.mappings.save':return self.mapping_save(**p)
        if method=='gamelab.mappings.list':return self.mapping_list(**p)
        if method=='gamelab.mappings.revalidate':return self.mapping_revalidate(**p)
        if method=='gamelab.mappings.observe':return self.mapping_observe(**p)
        if method=='gamelab.scans.start':return self.scan_start(**p)
        if method=='gamelab.scans.control':return self.scan_control(**p)
        if method=='gamelab.pointers.search':return self.pointer_search(**p)
        if method=='gamelab.mappings.export':return self.mapping_export(**p)
        if method=='gamelab.mappings.import':return self.mapping_import(**p)
        if method=='gamelab.scans.candidate_search':return self.candidate_search(**p)
        if method=='gamelab.runtime.status':return self.status(p.get('harness_id'))
        if method=='gamelab.runtime.fixture':return self.fixture(p['harness_id'])
        if method=='gamelab.runtime.read':return self.read(**p)
        if method=='gamelab.runtime.write':return self.write(**p)
        raise ValueError('Unknown game runtime method.')

    def shutdown(self):
        if self.app.primary:
            self.stop()
        else:
            # Short-lived MCP/RPC clients share the desktop's database. Closing
            # one must not revoke sessions owned by that desktop connection.
            for session in self.broker.list_sessions():
                if session['target_id'] in self._fixtures:self.stop(session['id'])
        for fixture in self._fixtures.values():
            try:fixture.shutdown()
            except Exception:pass
        self._fixtures.clear()
