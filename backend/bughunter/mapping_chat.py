"""Durable conversation coordinator. Native checks execute only in the desktop shell."""
from __future__ import annotations
import re
from decimal import Decimal
from .memory_mappings import numeric
from .security import redact, redact_text
from .store import new_id, now

ACTIVE = {'needs_program', 'researching', 'needs_value', 'checking', 'blocked'}
NUMBER = r'[-+]?(?:\d+(?:\.\d*)?|\.\d+)(?:[eE][-+]?\d+)?'
MAX_ROUNDS = 6


def observed_value(message, initial=False):
    patterns = [rf'\b(?:value|score|health|count|balance|money)\s*(?:is|=|:|now|of)\s*({NUMBER})(?!\w|\.\w)']
    if not initial:
        patterns += [rf'^\s*(?:it(?:\s+is|\x27s)|now|changed\s+to|value\s+is)?\s*({NUMBER})\s*[.!]?\s*$']
    for pattern in patterns:
        match = re.search(pattern, message, re.I)
        if match:
            value = match.group(1).rstrip('.')
            numeric(value)
            return value
    return None


class MappingChat:
    def __init__(self, app):
        self.app, self.store, self.mappings = app, app.store, app.memory_mappings

    def _session(self, id):
        row = self.store.get('chat', id)
        if not row: raise ValueError('Conversation not found.')
        return row

    def _workflow(self, id):
        session = self._session(id)
        row = self.store.get('mapping_chat', session.get('mapping_id', ''))
        if not row or row['chat_id'] != id or row.get('project_id') != session.get('project_id'):
            raise ValueError('Mapping task does not belong to this conversation.')
        return row

    def _put(self, row, **changes):
        return self.store.put('mapping_chat', {**row, **changes}, id=row['id'])

    def _message(self, row, text, role='assistant', key=None, citations=None):
        session = self._session(row['chat_id'])
        messages = session.get('messages', [])
        if key and any(m.get('mapping_event') == key for m in messages): return
        entry = {'role':role,'content':redact_text(text),'mapping_id':row['id']}
        if key: entry['mapping_event'] = key
        if citations: entry['citations'] = redact(citations)
        self.store.put('chat', {**session, 'messages':messages + [entry]}, id=session['id'])

    def _event(self, row, text):
        return self._put(row, progress=text, events=(row.get('events', []) + [{'at':now(),'text':text}])[-100:])

    def start(self, p):
        self.store.require_desktop_owner()
        if self.store.setting('emergency_stop', False): raise ValueError('Requests are stopped.')
        message = p.get('message')
        if not isinstance(message,str) or not 3 <= len(message.strip()) <= 12000: raise ValueError('Describe the mapping to research.')
        session = self._session(p['id']) if p.get('id') else None
        project_id = p.get('project_id', (session or {}).get('project_id')) or None
        if session and session.get('project_id') != project_id: raise ValueError('Conversation belongs to another project.')
        if project_id and not self.store.get('project', project_id): raise ValueError('Project does not exist.')
        if session and session.get('mapping_id'):
            old = self._workflow(session['id'])
            if old['status'] in ACTIVE: raise ValueError('Finish or stop the current mapping task first.')
        if any(m.get('pending') for m in (session or {}).get('messages',[])): raise ValueError('Another task is active in this conversation.')
        source_ids = p.get('source_ids', [])
        if not isinstance(source_ids,list) or len(source_ids)>6 or any(not isinstance(v,str) for v in source_ids):
            raise ValueError('Choose up to six reference sources.')
        for id in source_ids:
            source = self.store.get('source',id)
            if not source or source.get('project_id') not in (None,project_id): raise ValueError('A reference is unavailable in this project.')
        search_web = p.get('search_web', True)
        if type(search_web) is not bool: raise ValueError('Invalid search option.')
        sid = (session or {}).get('id') or new_id('chat')
        wid = new_id('mapping_chat')
        urls = re.findall(r'https://[^\s<>"\]]+', message)[:3]
        urls = [url.rstrip('.,);') for url in urls]
        # The actual value is supplied by the operator, never inferred by a model.
        value = observed_value(message, initial=True)
        query = re.sub(r'https://\S+', '', message).strip()[:300]
        session = self.store.put('chat', {**(session or {}), 'title':(session or {}).get('title') or message[:64],
            'project_id':project_id, 'messages':(session or {}).get('messages',[]), 'mapping_id':wid,
            'run_id':None,'task_id':None,
            'mapping_ids':list(dict.fromkeys([*(session or {}).get('mapping_ids',[]),wid]))}, id=sid)
        row = self.store.put('mapping_chat', {'chat_id':sid,'project_id':project_id,'query':query,'urls':urls,
            'source_ids':source_ids,'search_web':search_web,'status':'needs_program','context':None,
            'research_id':None,'research_ids':[],'expected_value':value,'round':0,'queue':[],'check_id':None,
            'events':[],'progress':'Selecting the program to research.'}, id=wid)
        self._message(row,message.strip(),'user')
        sources = 'saved references and the public web' if search_web else 'saved references'
        self._message(row,f'I’ll research {sources}, propose cited mappings, check the selected program, and save a report here. Choose the program below if one is not already attached.',key=wid+':start')
        return self.status(sid)

    def select(self, p):
        row = self._workflow(p['id'])
        if row['status'] != 'needs_program': raise ValueError('This task already has a selected program.')
        context = p.get('context')
        if not isinstance(context,dict) or type(context.get('pid')) is not int or context['pid'] < 2:
            raise ValueError('Select a running program.')
        if not isinstance(context.get('process_instance_id'),str) or not 4 <= len(context['process_instance_id']) <= 200:
            raise ValueError('Process creation identity is required.')
        if not isinstance(context.get('build_sha256'),str) or not re.fullmatch('[a-f0-9]{64}', context['build_sha256']):
            raise ValueError('The native executable fingerprint is required.')
        program = context.get('program')
        if not isinstance(program,str) or not 2 <= len(program) <= 160: raise ValueError('Program name is required.')
        selected = {k:context[k] for k in ('pid','process_instance_id','build_sha256','program')}
        self._research(row, selected)
        return self.status(p['id'])

    def _research(self, row, selected):
        if row.get('research_id'): self.mappings.stop(row['research_id'])
        job = self.mappings.start(row['query'],selected['program'],project_id=row.get('project_id'),
            search_web=row['search_web'], urls=row['urls'], source_ids=row['source_ids'])
        row = self._put(row, context=selected, research_id=job['id'], status='researching',queue=[],check_id=None,round=0,requires_selection=False,
            research_ids=list(dict.fromkeys([*row.get('research_ids',[]),job['id']])))
        row = self._event(row, 'Researching references for ' + selected['program'] + '.')
        self._message(row,f"Selected {selected['program']} (PID {selected['pid']}). I’m researching its mappings now.",key=job['id']+':selected')
        return row

    def _start_round(self, row, research, value, reset_scan=False):
        if row['round'] >= MAX_ROUNDS: return self._finish(row,research,'limited','Reached the six-round check limit.')
        candidates = [c for c in research['candidates'] if not c.get('parent_id') and c['status'] != 'source_changed'][:3]
        if not candidates: return self._finish(row,research,'blocked','There are no current, source-backed candidates to check.')
        # Validate all requested value types before issuing even the first check.
        for candidate in candidates: numeric(value,candidate['recipe']['value_type'])
        row = self._put(row,status='checking',expected_value=value,round=row['round']+1,
            queue=[c['id'] for c in candidates],check_id=None,reset_scan=reset_scan)
        return self._event(row,f"Checking {len(candidates)} cited candidates against the observed value {value}.")

    def advance(self, id):
        row = self._workflow(id)
        if row['status'] not in {'researching','checking'}: return self.status(id)
        if self.store.setting('emergency_stop',False): return self.stop(id)
        self.store.require_desktop_owner()
        research = self.mappings.get(row['research_id'])
        if row['status'] == 'researching':
            if research['status'] in {'researching','suggesting'}: return self.status(id)
            if research['status'] != 'ready':
                self._finish(row,research,'blocked',research.get('progress','Research could not finish.'))
                return self.status(id)
            self._message(row,f"Found {len(research['references'])} references and {len(research['candidates'])} candidate mappings. These are hypotheses until native readback supports them.",key=research['id']+':researched')
            if row.get('expected_value') is None:
                self._put(row,status='needs_value',progress='Waiting for the current visible value.')
                self._message(row,'What numeric value is currently visible in the selected program? Reply with the number, for example “100”. I’ll check the candidates automatically.',key=research['id']+':value:0')
                return self.status(id)
            try: row = self._start_round(row,research,row['expected_value'])
            except ValueError as exc:
                row = self._put(row,status='needs_value',expected_value=None,progress=str(exc))
                self._message(row,str(exc)+' Reply with a value that fits the proposed numeric type.',key=row['id']+':invalid-initial')
                return self.status(id)
        if row['status'] != 'checking': return self.status(id)
        if row.get('check_id'):
            check = self.store.get('mapping_check',row['check_id'])
            if not check or check['status'] in {'interrupted','superseded'}:
                self._finish(row,research,'blocked','A check was interrupted. Reply “retry” to try again, or choose a program again after reconnecting.')
                return self.status(id)
            if check['status'] == 'pending':
                receipt = self.mappings.receipts / (check['id']+'.json')
                if not receipt.is_file(): return self.status(id, next_check=check)
                try: research = self.mappings.observe(check['id'])
                except ValueError as exc:
                    self._finish(row,research,'blocked',str(exc)); return self.status(id)
            row = self._put(row,check_id=None,queue=row['queue'][1:])
        if row['queue']:
            context = row['context']
            try:
                check = self.mappings.challenge(row['queue'][0],context['pid'],context['process_instance_id'],
                    row['expected_value'], reset_scan=row.get('reset_scan',False))
            except ValueError as exc:
                self._finish(row,research,'blocked',str(exc)); return self.status(id)
            row = self._put(row,check_id=check['id'])
            return self.status(id,next_check=self.store.get('mapping_check',check['id']))
        research = self.mappings.get(row['research_id'])
        if research['verified_count']:
            self._finish(row,research,'completed','Checks finished; cited evidence has been saved.')
        elif any(c['status'] == 'check_failed' for c in research['candidates'] if not c.get('parent_id')):
            errors = [c.get('observations',[])[-1].get('error','Readback failed.') for c in research['candidates'] if c['status']=='check_failed' and c.get('observations')]
            self._finish(row,research,'blocked','Native checks could not finish. ' + ' '.join(errors)[:800])
        elif row['round'] >= MAX_ROUNDS:
            self._finish(row,research,'limited','Reached the six-round check limit without verified results.')
        else:
            self._put(row,status='needs_value',progress='Readbacks saved. Waiting for a different observed value.')
            self._message(row,'I saved the readbacks, but no mapping is verified yet. Change the visible value in the program, then tell me the new number. I’ll narrow and recheck automatically. If no candidates remain, reply “fresh scan, value is 100” with the current number.',key=research['id']+':value:'+str(row['round']))
        return self.status(id)

    def reply(self, p):
        row = self._workflow(p['id'])
        message = p.get('message')
        if not isinstance(message,str) or not message.strip() or len(message)>12000: raise ValueError('Enter a message.')
        source_ids = p.get('source_ids',[])
        if not isinstance(source_ids,list) or any(not isinstance(s,str) for s in source_ids): raise ValueError('Invalid reference sources.')
        urls = [u.rstrip('.,);') for u in re.findall(r'https://[^\s<>"\]]+',message)]
        if source_ids or urls:
            if row['status'] not in {'needs_program','needs_value','blocked'}: raise ValueError('Wait for the current checks before adding references.')
            source_ids = list(dict.fromkeys([*row['source_ids'],*source_ids]))
            urls = list(dict.fromkeys([*row['urls'],*urls]))
            if len(source_ids)>6 or len(urls)>3: raise ValueError('Choose up to six sources and three reference links per task.')
            from .research import _public_https
            if any(not _public_https(u) for u in urls): raise ValueError('Reference links must be public HTTPS URLs.')
            for source_id in source_ids:
                source = self.store.get('source',source_id)
                if not source or source.get('project_id') not in (None,row.get('project_id')): raise ValueError('A reference is unavailable in this project.')
            row = self._put(row,source_ids=source_ids,urls=urls,expected_value=observed_value(message))
            self._message(row,message.strip(),'user')
            if row.get('context'): self._research(row,row['context'])
            else: self._message(row,'References added. Choose the program below to continue.')
            return self.status(p['id'])
        self._message(row,message.strip(),'user')
        if re.fullmatch(r'\s*(?:stop|cancel)(?:\s+(?:this|the)\s+task)?[.!]?\s*',message,re.I): return self.stop(p['id'])
        if re.fullmatch(r'\s*(?:please )?(?:search (?:the )?web|search online|use web search)[.!]?\s*',message,re.I) and row['status'] in {'needs_program','needs_value','blocked'}:
            row=self._put(row,search_web=True)
            if row.get('context'): self._research(row,row['context'])
            else: self._message(row,'Web search enabled. Choose the program below to continue.')
            return self.status(p['id'])
        if re.fullmatch(r'\s*(?:choose|select|change)(?:\s+(?:the|another|selected))?\s+(?:program|process)(?:\s+again)?[.!]?\s*',message,re.I):
            if row.get('research_id'): self.mappings.stop(row['research_id'])
            self._put(row,status='needs_program',context=None,check_id=None,queue=[],expected_value=None,requires_selection=True,progress='Choose the running program to continue.')
            self._message(row,'Choose a program below. Previous evidence remains saved; new checks will use the selected process instance.')
            return self.status(p['id'])
        if row['status'] in {'researching','checking'}:
            self._message(row,'The task is still running. '+row['progress'])
            return self.status(p['id'])
        if row['status'] == 'needs_program':
            value = observed_value(message)
            if value is not None: self._put(row,expected_value=value)
            self._message(row,'Choose the running program below; I’ll continue with the references and checks here.')
            return self.status(p['id'])
        if row['status'] not in {'needs_value','blocked'}: raise ValueError('This task has finished. Start a new mapping request.')
        research = self.mappings.get(row['research_id']) if row.get('research_id') else None
        if not research or research['status'] != 'ready':
            if row.get('context'):
                self._research(row,row['context'])
                return self.status(p['id'])
            self._put(row,status='needs_program',research_id=None,context=None,check_id=None,queue=[])
            self._message(row,'Choose the program to start another research attempt. Add a reference to the Library if none was found.')
            return self.status(p['id'])
        value = observed_value(message)
        if value is None and re.fullmatch(r'\s*(?:retry|continue|try again)[.!]?\s*',message,re.I): value=row.get('expected_value')
        if value is None:
            self._message(row,'Reply with the numeric value visible in the program, for example “90”. It must come from the program so I can independently compare the readback.')
            return self.status(p['id'])
        if row.get('expected_value') is not None and Decimal(value) == Decimal(row['expected_value']) and row['status']=='needs_value' and row['round'] and 'fresh scan' not in message.lower():
            self._message(row,'That is the same value as before. A different visible value is needed to verify the mapping; repeating a read cannot establish it.')
            return self.status(p['id'])
        try: self._start_round(row,research,value,reset_scan='fresh scan' in message.lower())
        except ValueError as exc: self._message(row,str(exc))
        return self.status(p['id'])

    def _finish(self, row, research, status, summary):
        row = self._event(self._put(row,status=status,queue=[],check_id=None),summary)
        verified = [c for c in research.get('candidates',[]) if c['status']=='value_verified']
        candidates = research.get('candidates',[])
        lines = [summary, f"{len(verified)} verified value mappings; {len(candidates)-len(verified)} other candidates saved."]
        if row.get('context'):
            lines.append(f"Program: {row['context']['program']} · PID {row['context']['pid']}. Evidence is scoped to the recorded process instance and executable fingerprint.")
        citations = []
        for candidate in (verified or candidates)[:8]:
            ref = next((r for r in research['references'] if r['id']==candidate['reference_id']),None)
            observations = [o for o in candidate.get('observations',[]) if o.get('matched')][-2:]
            measured = ', '.join(o.get('value_text',str(o.get('value'))) for o in observations)
            lines.append(f"• {candidate['label']}: {candidate['status'].replace('_',' ')}" + (f" (read {measured})" if measured else ''))
            recipe = candidate['recipe']
            if recipe['kind']=='module_pointer':
                location = recipe['module_name']+' + '+recipe['rva']+''.join(' → dereference + '+offset for offset in recipe.get('offsets',[]))
            else: location = recipe.get('base','Observed-value scan; no stable address established')
            lines.append(f"  Mapping: {location} ({recipe['value_type']}).")
            if observations: lines.append('  Latest read address: '+observations[-1]['address']+'.')
            scan = candidate.get('scan_summary')
            if scan and (scan.get('partial') or scan.get('truncated')): lines.append('  Scan coverage was limited; additional candidates may exist.')
            if ref:
                index = len(citations)+1
                citations.append({'title':ref['title'],'url':ref.get('url'),'source_id':ref.get('source_id'),
                    'locator':ref.get('locator'),'quote':candidate['quote'],'reference_id':ref['id']})
                lines.append(f"  [{index}] {ref['title']}" + (f" · {ref['locator']}" if ref.get('locator') else '') + f"\n  “{candidate['quote'][:400]}”")
        if verified: lines.append('Verified means two distinct observed values matched in this process and executable build. Semantic meaning and stability after restart remain unproven.')
        for warning in research.get('warnings',[])[:3]: lines.append('Research note: '+warning)
        self._message(row,'\n\n'.join(lines),key=research['id']+':report:'+str(row['round'])+':'+status,citations=citations)
        return row

    def pause(self, p):
        row = self._workflow(p['id'])
        if row['status'] not in ACTIVE: return self.status(p['id'])
        reason = redact_text(str(p.get('reason') or 'Desktop connection unavailable.'))[:800]
        row = self._put(row,status='blocked',progress=reason)
        self._message(row,reason+' Reply “retry” when the connection is ready.',key=row['id']+':blocked:'+str(row['round']))
        return self.status(p['id'])

    def stop(self, id):
        row = self._workflow(id)
        if row['status'] in {'completed','cancelled','limited'}: return self.status(id)
        if row.get('research_id'): self.mappings.stop(row['research_id'])
        row = self._put(row,status='cancelled',queue=[],check_id=None,progress='Stopped. Existing evidence is saved.')
        self._message(row,'Stopped the mapping task. Completed readbacks and references remain saved in this conversation.',key=row['id']+':stopped')
        return self.status(id)

    def status(self, id, next_check=None):
        row = self._workflow(id)
        research = self.mappings.get(row['research_id']) if row.get('research_id') else None
        progress = research.get('progress') if row['status']=='researching' and research else row['progress']
        result = {'session':self._session(id),'workflow':{**row,'progress':progress},'research':research,'next':None}
        if next_check: result['next']={'kind':'check','check_id':next_check['id'],'request':next_check['request']}
        return result

    def export(self, session):
        result=[]
        for id in session.get('mapping_ids',[]):
            row=self.store.get('mapping_chat',id)
            if row and row['chat_id']==session['id'] and row.get('project_id')==session.get('project_id'):
                research=self.mappings.get(row['research_id']) if row.get('research_id') and self.store.get('mapping_research',row['research_id']) else None
                attempts=[self.mappings.get(rid) for rid in row.get('research_ids',[]) if self.store.get('mapping_research',rid)]
                result.append({'workflow':row,'research':research,'attempts':attempts})
        return result

    def dispatch(self, method, p):
        with self.app.chat._turn_locks[hash(str(p.get('id') or 'new')) % len(self.app.chat._turn_locks)]:
            if method=='chat.mapping.start': return self.start(p)
            if method=='chat.mapping.select': return self.select(p)
            if method=='chat.mapping.advance': return self.advance(p['id'])
            if method=='chat.mapping.status': return self.status(p['id'])
            if method=='chat.mapping.reply': return self.reply(p)
            if method=='chat.mapping.pause': return self.pause(p)
            if method=='chat.mapping.stop': return self.stop(p['id'])
            raise ValueError('Unknown conversation mapping operation.')
