"""Authenticated, scope-checked bridge to a local Burp Montoya extension.

History reading and issues reporting are passive. An approved Burp audit can send network requests.
Read the secret locally, require an authorized project, filter all records to its
scope, and redact anything the extension didn't explicitly preserve.
"""
from __future__ import annotations
import os
import re
from pathlib import Path
from urllib.parse import urlunsplit, urlsplit
import httpx
from .policy import validate_scope, classify_effect, normalized_request
from .web_pentest import PRICING_FIELDS, review_traffic

PORT = 18765
BASE = f'http://127.0.0.1:{PORT}'


def _token(path:Path|None=None)->str:
    path=path or Path.home()/'.bughunter-claudex'/'burp-token'
    if not path.is_file():raise ValueError('Install and start the BugHunter read-only extension in Burp first.')
    if os.name=='posix' and (path.stat().st_mode & 0o077):
        raise ValueError('Burp token file permissions are too broad. Set chmod 600 on the token file.')
    token=path.read_text(encoding='ascii').strip()
    if len(token)<30 or len(token)>120:raise ValueError('Invalid Burp bridge token.')
    return token


def safe_item(item:dict,project:dict)->dict|None:
    if not isinstance(item,dict):return None
    method=item.get('method','')
    scheme=item.get('scheme','')
    host=item.get('host','')
    path=item.get('path','')
    if method not in {'GET','HEAD','OPTIONS','POST','PUT','PATCH','DELETE'}:
        return None
    if scheme not in {'https','http'} or not isinstance(host,str) or len(host)>255 or not isinstance(path,str) or len(path)>1500:
        return None
    if '?' in path or '#' in path:return None
    port=item.get('port',-1)
    if type(port) is not int or port != -1 and not 1<=port<=65535:return None
    authority=('['+host+']' if ':' in host and not host.startswith('[') else host) + ((':'+str(port)) if port!=-1 else '')
    url=urlunsplit((scheme,authority,path or '/', '', ''))
    try:validate_scope(project,url,resolve=False)
    except (ValueError,TypeError):return None
    status=item.get('status')
    if type(status) is not int or not 0<=status<=599:return None
    checks=item.get('response_checks')
    if not isinstance(checks,dict):checks={}
    flags={key:checks[key] for key in ('hsts','csp','frame_protection','nosniff') if type(checks.get(key)) is bool}
    rid=item.get('burp_history_id')
    if type(rid) is not int or rid<1:return None
    names=item.get('pricing_fields',[])
    if not isinstance(names,list) or len(names)>32 or any(type(name) is not str for name in names):
        names=[]
    # Only known public schema field names, never parameters or values in full.
    names=sorted(set(names).intersection(PRICING_FIELDS))
    return {'method':method,'url':url,'status':status,'burp_history_id':rid,
            'pricing_fields':names,'response_checks':flags,
            'source':'Burp HTTP history (metadata and header-presence checks only)'}


def safe_issue(item:dict, project:dict)->dict|None:
    """Burp Scanner's own notification, never an independently verified exploit."""
    if not isinstance(item,dict):return None
    url=item.get('url')
    if not isinstance(url,str) or len(url)>1600:return None
    parsed=urlsplit(url)
    if parsed.query or parsed.fragment or parsed.username or parsed.password:return None
    try:validate_scope(project,url,resolve=False)
    except (ValueError,TypeError):return None
    name=item.get('name')
    severity=item.get('severity')
    confidence=item.get('confidence')
    if not isinstance(name,str) or not (1<=len(name)<=120):return None
    if not isinstance(severity,str) or severity.upper() not in {'HIGH','MEDIUM','LOW','INFORMATION','INFO','FALSE_POSITIVE'}:return None
    if not isinstance(confidence,str) or confidence.upper() not in {'CERTAIN','FIRM','TENTATIVE'}:return None
    return {'url':url, 'name':name,'severity':severity.upper(),'confidence':confidence.upper(),
            'source':'Burp Scanner notification','verification':'Reported by Burp; independently unverified'}


class BurpHistory:
    def __init__(self,app):self.app=app
    def dispatch(self,method,params):
        if method not in {'burp.history','burp.health','burp.repeater.open','burp.issues','burp.audit.start','burp.audit.status','burp.audit.stop','burp.web.review'}:raise ValueError('Unknown Burp action.')
        self.app.store.require_desktop_owner()
        if self.app.store.setting('emergency_stop',False):raise ValueError('Task execution is paused.')
        project=None
        if method in {'burp.history','burp.repeater.open','burp.issues','burp.audit.start','burp.web.review'}:
            project=self.app.policy.project(str(params.get('project_id','')))
            if not project.get('authorization') or not project.get('scope'):
                raise ValueError('Select an authorized website project first.')
        if method in {'burp.audit.status','burp.audit.stop'}:
            # Stop only the locally authenticated Burp task; no user-supplied target URL.
            if method=='burp.audit.stop' and params.get('approved') is not True:
                raise ValueError('Confirm cancelling the current Burp audit.')
            token=_token()
            endpoint='/audit/status' if method=='burp.audit.status' else '/audit/stop'
            with httpx.Client(timeout=7,trust_env=False,follow_redirects=False) as client:
                response=(client.get(BASE+endpoint,headers={'X-BugHunter-Token':token})
                          if method=='burp.audit.status' else client.post(BASE+endpoint,headers={'X-BugHunter-Token':token}))
                response.raise_for_status()
                data=response.json()
            if method=='burp.audit.stop':
                if data.get('stopped') is not True:raise ValueError('Burp did not confirm cancellation.')
                return {'stopped':True,'source':'Authenticated local Burp extension'}
            return safe_audit_status(data)
        if method=='burp.web.review':
            health=self.dispatch('burp.health',{})
            if not health.get('connected'):
                raise ValueError('Burp extension is not connected.')
            hist=self.dispatch('burp.history',{'project_id':project['id']})
            issues=self.dispatch('burp.issues',{'project_id':project['id']})
            result=review_traffic(hist['items'],issues['issues'],hist.get('passive_review_leads') or [])
            return {'project_id':project['id'],'bridge_health':health, 'history':hist['items'],
                    'issues':issues['issues'], 'passive_review_leads':hist.get('passive_review_leads',[]),
                    'review':result,'read_only':True,
                    'source':'Authenticated project-scoped Burp Montoya history and Scanner feed'}
        if method=='burp.audit.start':
            if params.get('approved') is not True:
                raise ValueError('Active auditing sends requests and may modify data. Explicit authorization is required.')
            target=params.get('history_id')
            if type(target) is not int or target<1 or target>999999999:
                raise ValueError('Provide a Burp history number, such as #42.')
            # Only previously observed, authorized GET/HEAD paths. Do not take
            # arbitrary URLs from the model. Project excludes take priority.
            listing=self.dispatch('burp.history',{'project_id':project['id']})
            record=next((x for x in listing['items'] if x['burp_history_id']==target),None)
            if record is None:raise ValueError('History item is unavailable or outside the approved scope.')
            if record['method'] not in {'GET','HEAD'} or '?' in record['url']:
                raise ValueError('Only query-free GET/HEAD requests can be used as scan seeds.')
            effect,_=classify_effect(project,normalized_request({'method':record['method'],'url':record['url']}))
            if effect!='read_only':
                raise ValueError('Mark this URL as an approved read-only path in the website project first.')
            token=_token()
            with httpx.Client(timeout=10,trust_env=False,follow_redirects=False) as client:
                res=client.post(BASE+'/audit/start',content=str(target),
                    headers={'X-BugHunter-Token':token,'Content-Type':'text/plain'})
                res.raise_for_status()
                result=res.json()
            if result.get('started') is not True or result.get('history_id')!=target:
                raise ValueError('Burp did not confirm starting an audit.')
            self.app.store.event('burp.audit.started',{'project_id':project['id'],'history_id':target})
            return {'started':True,'history_id':target,'project_id':project['id'],
                    'source':'Official Burp Montoya active audit',
                    'warning':'Burp may send many modified requests and affect target data. Monitor it in Burp; cancel if needed. Findings require verification.'}
        if method=='burp.repeater.open':
            if params.get('approved') is not True:
                raise ValueError('Confirm opening this request in Burp Repeater first.')
            target=params.get('history_id')
            if type(target) is not int or not 1<=target<=2147483647:
                raise ValueError('Provide an existing Burp history number.')
            # Fresh in-scope metadata is checked before any opening action.
            listing=self.dispatch('burp.history',{'project_id':project['id']})
            if target not in {x['burp_history_id'] for x in listing['items']}:
                raise ValueError('History item is unavailable or outside the approved project scope.')
            token=_token()
            with httpx.Client(timeout=5,trust_env=False,follow_redirects=False) as client:
                res=client.post(BASE+'/repeater',content=str(target),headers={'X-BugHunter-Token':token,'Content-Type':'text/plain'})
                res.raise_for_status()
                output=res.json()
            if output.get('opened') is not True or output.get('sent_request') is not False or output.get('history_id')!=target:
                raise ValueError('Burp did not confirm opening the Repeater tab.')
            return {'opened':True,'history_id':target,'sent_request':False,
                    'verified':'Burp opened the selected in-scope request for manual inspection, without sending it.'}
        token=_token()
        with httpx.Client(timeout=5,trust_env=False,follow_redirects=False) as client:
            resp=client.get(BASE+('/health' if method=='burp.health' else '/issues' if method=='burp.issues' else '/history'),headers={'X-BugHunter-Token':token})
            resp.raise_for_status()
            data=resp.json()
        if method=='burp.health':return {'connected':bool(data.get('connected')), 'service':data.get('service'), 'verified':'Authenticated Burp extension response'}
        raw=data.get('items')
        if method=='burp.issues':
            if not isinstance(raw,list):raise ValueError('Burp issue response is malformed.')
            issues=[safe_issue(x,project) for x in raw[:200]]
            issues=[x for x in issues if x is not None]
            return {'project_id':project['id'],'issues':issues,'count':len(issues),
                    'source':'Authenticated Burp Scanner issue feed',
                    'verification':'Scanner-reported findings require independent reproduction; no new scan was started.'}
        if not isinstance(raw,list):raise ValueError('Burp history response is malformed.')
        items=[safe_item(item,project) for item in raw[:100]]
        items=[x for x in items if x is not None]
        return {'project_id':project['id'],'items':items,'count':len(items),
                'passive_review_leads':passive_review(items), 'verified':'Actual Burp proxy history, scope filtered; no vulnerability is proven.','redactions':'Request bodies, response bodies, headers, cookies and query parameters excluded.'}


def passive_review(items:list[dict])->list[dict]:
    """Evidence-linked header presence only. Not a vulnerability assertion."""
    seen=set()
    out=[]
    for entry in items:
        flags=entry.get('response_checks') or {}
        if not flags or entry['status']<200 or entry['status']>=400:continue
        for key,description in (
            ('hsts','HTTPS response did not advertise HSTS'),
            ('csp','Response did not advertise a Content-Security-Policy'),
            ('nosniff','Response did not advertise X-Content-Type-Options'),
        ):
            if key not in flags or flags[key] is not False:continue
            if key=='hsts' and not entry['url'].startswith('https://'):continue
            # Avoid spamming same host with identical observations.
            host=urlsplit(entry['url']).netloc
            if (host,key) in seen:continue
            seen.add((host,key))
            out.append({'history_id':entry['burp_history_id'],'url':entry['url'],
                        'observation':description,'level':'review only',
                        'verification_needed':'Confirm requirements and hosting configuration before reporting a vulnerability.'})
            if len(out)>=24:return out
    return out


def safe_audit_status(data:dict)->dict:
    """Bounded status only. No request bodies, paths, tokens, or error text."""
    if not isinstance(data,dict) or type(data.get('tracked')) is not bool:
        raise ValueError('Malformed Burp audit status.')
    if data['tracked'] is False:
        return {'tracked':False,'source':'Authenticated local Burp extension'}
    hid=data.get('history_id')
    if type(hid) is not int or hid<=0:raise ValueError('Invalid Burp audit history id.')
    out={'tracked':True,'history_id':hid,'source':'Authenticated local Burp extension'}
    for name in ('requests','errors','reported_issues'):
        val=data.get(name)
        if type(val) is int and 0<=val<100000000:out[name]=val
    status=data.get('status')
    if isinstance(status,str):out['status']=status[:180]
    return out
