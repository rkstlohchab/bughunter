"""Private stdio helper with serialized native work and responsive control frames."""
from __future__ import annotations
import os
import queue
import re
import sys
import threading
import time
from .protocol import DEFAULT_MAX_MESSAGE_SIZE, Request, Response, ProcessIdentity, decode_frame, encode_frame, read_process_start_time_ns
from .server import MemoryEngineServer


def main():
    server=MemoryEngineServer();bootstrapped=False
    from .runtime import RuntimeOperations
    from .analysis import AnalysisOperations
    runtime=RuntimeOperations(server)
    analysis=AnalysisOperations(server)
    output_lock=threading.Lock();state_lock=threading.Lock()
    jobs=queue.Queue(maxsize=64)
    state={'quiescing':False,'active':0,'closing':False,'last_op_latency_ms':0.0,'backlog':0}
    def respond(ident,result=None,error=None):
        with output_lock:
            sys.stdout.buffer.write(encode_frame(Response(id=ident,result=result,error=error)))
            sys.stdout.buffer.flush()
    def _cleanup_phase():
        with state_lock:
            if state['closing']:return 'terminated'
            if state['quiescing']:
                if state['active']==0 and jobs.unfinished_tasks==0:return 'cleaning_up'
                return 'quiescing'
            return 'idle'
    def _diagnostics():
        sessions=list(server._sessions.values())
        backend=None;lldb_pid=None
        for sess in sessions:
            adapter=sess.adapter
            info=getattr(adapter,'info',None)
            if info is None:continue
            backend=getattr(info,'backend',None)
            lldb_pid=getattr(info,'lldb_session_pid',None)
            if lldb_pid is not None:break
        with state_lock:
            latency=state['last_op_latency_ms']
            active=state['active']
        return {'backend':backend,'lldb_session_pid':lldb_pid,'last_op_latency_ms':latency,
                'in_flight_ops':active,'cleanup_phase':_cleanup_phase()}
    def operation(method,params):
        if method=='gamelab.native.attach':
            pid=int(params['pid']);expected=params.get('identity')
            if expected and (expected['pid']!=pid or expected['start_time_ns']!=read_process_start_time_ns(pid)):raise ValueError('Target birth identity changed before attach.')
            existing=next((s.adapter for s in server._sessions.values() if s.adapter.identity.pid==pid),None)
            if existing is None:
                if sys.platform=='darwin':
                    from .mac_adapter import MacMemoryAdapter as Adapter
                elif sys.platform=='win32':
                    from .windows_adapter import WindowsMemoryAdapter as Adapter
                else:raise ValueError('No qualified native adapter for this OS.')
                adapter=Adapter(pid, identity=ProcessIdentity.from_wire(expected) if expected else None)
            else:adapter=existing
            try:result=server.attach(adapter,session_id=params.get('session_id'),mutating=params.get('mutating',True))
            except BaseException:
                if existing is None:adapter.close()
                raise
            if result['status']!='attached' and existing is None:adapter.close()
            return result
        if method=='gamelab.native.detach':return server.detach(params['session_id'])
        if method=='gamelab.native.diagnostics':return _diagnostics()
        if params.get('analysis_action'):
            if method!='gamelab.native.read':raise ValueError('Analysis runs only as a read-only operation.')
            return analysis.dispatch(params)
        if params.get('runtime_action'):
            return runtime.dispatch(params)
        methods={'gamelab.native.read':'memory.read','gamelab.native.write':'memory.write','gamelab.native.scan':'memory.scan','gamelab.native.pointer_search':'memory.pointer_search','gamelab.native.tool_control':'tool_control'}
        if method not in methods:raise ValueError('Unknown private helper method.')
        return server.dispatch(methods[method],params)
    def worker():
        while True:
            job=jobs.get()
            if job is None:break
            request=job
            op_start=time.monotonic()
            try:
                with state_lock:
                    if state['quiescing'] and not request.params.get('cleanup'):raise ValueError('Operation cancelled before native dispatch.')
                    state['active']+=1
                try:result=operation(request.method,request.params)
                finally:
                    with state_lock:
                        state['active']-=1
                        state['last_op_latency_ms']=(time.monotonic()-op_start)*1000.0
                respond(request.id,result)
            except Exception as exc:
                try:respond(request.id,error={'code':'operation_failed','message':str(exc)[:2000]})
                except (OSError,BrokenPipeError):pass
            finally:jobs.task_done()
        runtime.close()
        analysis.close()
        server.shutdown()
    thread=threading.Thread(target=worker,daemon=True);thread.start()
    try:
        while True:
            line=sys.stdin.buffer.readline(DEFAULT_MAX_MESSAGE_SIZE+1)
            if not line:break
            if len(line)>DEFAULT_MAX_MESSAGE_SIZE:return 2
            ident=0
            try:
                request=decode_frame(line)
                if not isinstance(request,Request):raise ValueError('Helper accepts request frames only.')
                ident=request.id;method=request.method;params=request.params
                if method=='gamelab.native.bootstrap':
                    if bootstrapped or not re.fullmatch('[0-9a-f]{64}',str(params.get('credential_hex',''))):raise ValueError('Invalid or repeated private bootstrap.')
                    bootstrapped=True
                    respond(ident,{'ready':True,'protocol_version':1,'helper_identity':{'pid':os.getpid(),'start_time_ns':read_process_start_time_ns(os.getpid())}})
                elif not bootstrapped:raise ValueError('Private bootstrap required.')
                elif method=='gamelab.native.diagnostics':
                    # Private diagnostic lane — never queued, never counts as work.
                    respond(ident,_diagnostics())
                elif method=='gamelab.native.quiesce':
                    with state_lock:
                        state['quiescing']=True
                    server.cancel_work()
                    with state_lock:
                        result={'quiescent':state['active']==0 and jobs.unfinished_tasks==0}
                    respond(ident,result)
                elif method=='gamelab.native.release_tasks':
                    with state_lock:
                        if state['active'] or jobs.unfinished_tasks:raise ValueError('Task release requires quiescence.')
                    for task in params.get('task_session_ids',[]):
                        server.discovery.close_owner(params['session_id'],task)
                        runtime.release_task(task)
                        analysis.release_task(task)
                    respond(ident,{'released':True})
                elif method=='gamelab.native.resume':
                    with state_lock:
                        if state['active'] or jobs.unfinished_tasks:raise ValueError('Helper is not quiescent.')
                        state['quiescing']=False
                    respond(ident,{'resumed':True})
                elif method=='gamelab.native.shutdown':
                    with state_lock:
                        state['quiescing']=True
                        state['closing']=True
                    server.cancel_work()
                    if state['active'] or jobs.unfinished_tasks:raise ValueError('Helper still has active work.')
                    respond(ident,{'stopped':True});break
                else:
                    with state_lock:
                        if state['quiescing'] and not params.get('cleanup'):raise ValueError('Helper is quiescing.')
                        if params.get('cleanup') and (not state['quiescing'] or state['active'] or jobs.unfinished_tasks):raise ValueError('Cleanup requires quiescence.')
                        jobs.put_nowait(request)
            except Exception as exc:
                respond(ident,error={'code':'operation_failed','message':str(exc)[:2000]})
    except (OSError,BrokenPipeError):return 1
    finally:
        with state_lock:
            state['quiescing']=True
            state['closing']=True
        try:jobs.put_nowait(None)
        except queue.Full:pass
        thread.join(timeout=1.8)
    return 0

if __name__=='__main__':raise SystemExit(main())
