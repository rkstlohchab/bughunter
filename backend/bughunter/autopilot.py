"""Chat-first, evidence-led workflows for imported files, research and controlled tests.

No model instruction, document, or website can widen project authorization. This
service deliberately has no arbitrary shell, process writing, or unrestricted
network access. Website work delegates to the existing scoped policy gateway.
"""
from __future__ import annotations

import hashlib
import io
import json
import re
import struct
import threading
import zipfile
import xml.etree.ElementTree as ET
from pathlib import Path
from urllib.parse import urlparse

from .store import now

MAX_INPUT = 12000
MAX_FILES = 6
MAX_ARCHIVE_ENTRIES = 2000
MAX_ARCHIVE_MEMBER_BYTES = 256 * 1024 * 1024
MAX_FILE_READ = 32 * 1024 * 1024


def choose_workflow(message: str, source_ids=None) -> str:
    """Deterministic routing; this does not give an LLM permission to run tools."""
    words = (message or '').lower()
    local_check = re.match(r'\s*(?:please\s+)?(?:run|check|audit|verify|test)\b', words)
    if local_check and re.search(r'\b(?:knowledge base|knowledge library|library integrity|knowledge integrity)\b', words):
        return 'knowledge_check'
    if local_check and re.search(r'\b(?:app checks|app tests|app test suite|application tests|local test suite|all tests|regression suite|self[ -]?tests?)\b', words):
        return 'app_checks'
    # Dedicated quote-only staging harness. Never infer live checkout testing.
    if (re.search(r'\b(?:test|run|check|audit|verify)\b', words)
            and re.search(r'\b(?:staging|pre-production|preprod)\b', words)
            and re.search(r'\b(?:price|quote|pricing|cart integrity)\b', words)):
        return 'staging_price'
    # The disposable local shop is the sole destination for automatic price
    # mutation requests; a live website must never be inferred from the chat.
    if (re.search(r'\b(?:test|run|audit|check)\b',words)
            and re.search(r'\b(?:price|checkout|coupon|cart|basket)\b',words)
            and re.search(r'\b(?:local|disposable|offline|test lab|test shop|demo shop|built.in lab)\b',words)):
        return 'commerce_lab'
    # Dedicated passive business-logic review: the only live source is Burp's
    # authenticated, project-scoped metadata feed. No request modification.
    if (re.search(r'\b(?:price manipulation|price tamper(?:ing)?|checkout tamper(?:ing)?|business.logic|payment security|(?:web|website)\s+(?:pen(?:etration)?\s*test(?:ing)?|pentest(?:ing)?))\b', words)
            and re.search(r'\b(?:web|site|burp|store|shop|checkout|price|payment|ecommerce|e-commerce)\b',words)):
        return 'web_pentest'
    if re.search(r'\bburp(?: suite)?\b',words) and re.search(r'\b(?:stop|cancel)\b.{0,50}\b(?:scan|audit)\b|\b(?:scan|audit)\b.{0,50}\b(?:stop|cancel)\b',words):
        return 'burp_scan_stop'
    if re.search(r'\bburp(?: suite)?\b',words) and re.search(r'\b(?:scan|audit)\b.{0,40}\b(?:status|progress|how far)\b|\b(?:status|progress)\b.{0,40}\b(?:scan|audit)\b',words):
        return 'burp_scan_status'
    if re.search(r'\bburp(?: suite)?\b',words) and re.search(r'\b(?:start|run|launch|begin)\b.{0,60}\b(?:scan|audit)\b|\b(?:scan|audit)\b.{0,60}\b(?:start|run|launch|begin)\b',words):
        return 'burp_scan_start'
    if (re.search(r'\bburp(?: suite)?\b', words)
            and re.search(r'\b(?:investigate|review|analy[sz]e|check|inspect|assess|report|find)\b',words)
            and re.search(r'\b(?:website|site|security|traffic|vulnerabilit(?:y|ies)|findings?|requests?)\b',words)):
        return 'burp_review'
    if re.search(r'\bburp(?: suite)?\b', words) and re.search(r'\b(?:issues?|findings?|scanner results?|vulnerabilit(?:y|ies))\b',words) and re.search(r'\b(?:show|list|read|review|report|summari[sz]e|what|found)\b',words):
        return 'burp_issues'
    if re.search(r'\bburp(?: suite)?\b', words) and re.search(r'\b(?:repeater)\b',words) and re.search(r'\b(?:open|show|send|review)\b',words):
        return 'burp_repeater'
    if re.search(r'\bburp(?: suite)?\b', words) and re.search(r'\b(?:history|traffic|requests?|responses?|intercepted|proxy logs?)\b', words):
        return 'burp_history'
    if re.search(r'\b(?:websites?|web apps?|burp|http|https|url|domain|web vuln|site security)\b', words) and re.search(r'\b(?:scan|audit|check|test|find|inspect|analy[sz]e|review)\b', words):
        return 'website'
    if re.search(r'\b(?:study|learn|research|explain)\b', words) and re.search(r'\b(?:game|esp|cheat engine|reverse.engineering)\b', words) and not source_ids:
        return 'knowledge' if re.search(r'\b(?:study|learn|research)\b', words) else 'chat'
    if re.search(r'\b(?:game|apk|\.exe|executable|binary|dll|ipa|reverse engineer|program|cheat engine|esp|unlimited money|memory)\b', words) or (source_ids and re.search(r'\b(?:analy[sz]e|inspect|check)\b', words)):
        return 'file'
    if re.search(r'\b(?:study|research|summari[sz]e|search|explain|learn|knowledge)\b', words):
        return 'knowledge'
    return 'chat'


def _inspect_binary(raw: bytes, name: str):
    """No execution or disassembly; labels are metadata, not vulnerability claims."""
    if raw.startswith(b'MZ'):
        info = {'format': 'Windows PE executable (MZ)', 'platform': 'Windows / compatibility layer'}
        if len(raw) >= 64:
            pe_offset = struct.unpack_from('<I', raw, 0x3c)[0]
            if pe_offset + 24 <= len(raw) and raw[pe_offset:pe_offset + 4] == b'PE\0\0':
                machine = struct.unpack_from('<H', raw, pe_offset + 4)[0]
                info['architecture'] = {0x14c:'x86',0x8664:'x86-64',0xaa64:'ARM64'}.get(machine,hex(machine))
        return info
    if raw.startswith(b'\x7fELF'):
        return {'format': 'ELF', 'platform': 'Linux / Unix', 'architecture': '64-bit' if raw[4:5] == b'\x02' else '32-bit'}
    magic = raw[:4]
    if magic in (b'\xfe\xed\xfa\xce', b'\xce\xfa\xed\xfe', b'\xfe\xed\xfa\xcf', b'\xcf\xfa\xed\xfe',b'\xca\xfe\xba\xbe'):
        return {'format':'Mach-O or universal binary','platform':'macOS / iOS'}
    if raw.startswith(b'PK\x03\x04'):
        return {'format':'ZIP-compatible archive', 'platform':'archive / APK / JAR'}
    return {'format':'Unknown or plain text','platform':'Not identified'}



def _pe_details(raw: bytes) -> tuple[dict, list, list]:
    """Bounded, dependency-free PE header/section/import metadata parsing.

    Input is untrusted. Every offset is checked; the analysis never executes code.
    These are observations about bytes, not a vulnerability diagnosis.
    """
    info, findings, warnings = {}, [], []
    if len(raw) < 64 or not raw.startswith(b'MZ'):
        return info, findings, warnings
    pe = struct.unpack_from('<I', raw, 0x3c)[0]
    if pe > len(raw) - 24 or raw[pe:pe+4] != b'PE\0\0':
        return info, findings, ['PE header is absent or outside the safe inspection window.']
    machine, section_count, _, _, _, optional_size, characteristics = struct.unpack_from('<HHIIIHH', raw, pe+4)
    info['sections_declared'] = section_count
    info['is_dll'] = bool(characteristics & 0x2000)
    opt = pe + 24
    if optional_size < 2 or opt + optional_size > len(raw):
        return info, findings, ['Optional PE header is incomplete.']
    magic = struct.unpack_from('<H', raw, opt)[0]
    if magic not in (0x10b, 0x20b):
        return info, findings, ['Unknown PE optional-header format.']
    info['pe_type'] = 'PE32+' if magic == 0x20b else 'PE32'
    if optional_size >= 72:
        # DllCharacteristics is at optional-header offset 70 for PE32 and PE32+.
        dll_flags = struct.unpack_from('<H', raw, opt+70)[0]
        info['security_flags'] = {
            'aslr_compatible': bool(dll_flags & 0x0040),
            'nx_compatible': bool(dll_flags & 0x0100),
            'control_flow_guard': bool(dll_flags & 0x4000),
        }
        findings.append({'observation': 'PE security capability flags read from header',
                         'evidence': info['security_flags']})
    sec_start = opt+optional_size
    sections = []
    for i in range(min(section_count, 96)):
        offset = sec_start+i*40
        if offset+40 > len(raw):
            warnings.append('Some section headers are outside the inspection window.')
            break
        raw_name, virtual_size, rva, raw_size, raw_pointer = struct.unpack_from('<8sIIII',raw,offset)
        name = raw_name.split(b'\0')[0].decode('ascii',errors='replace')[:8]
        flags = struct.unpack_from('<I',raw,offset+36)[0]
        sections.append({'name':name,'virtual_address':rva,'virtual_size':virtual_size,
                         'raw_size':raw_size,'raw_offset':raw_pointer,
                         'executable':bool(flags & 0x20000000), 'writable':bool(flags & 0x80000000)})
    if sections:
        findings.append({'observation':'PE section headers read without executing file',
                         'evidence':{'sections':sections[:24], 'sections_examined':len(sections)}})
    if section_count > 96:
        warnings.append('Only the first 96 PE section headers were examined.')
    # Bound the import-table walk to safe RVAs in the file sample. This is not a disassembler.
    dd_offset = 112 if magic == 0x20b else 96
    if optional_size < dd_offset+16:
        return info, findings, warnings
    import_rva = struct.unpack_from('<I',raw,opt+dd_offset+8)[0]
    def file_offset(rva: int):
        for sec in sections:
            begin = sec['virtual_address']
            span = max(sec['raw_size'],sec['virtual_size'])
            if begin <= rva < begin+span:
                delta = rva-begin
                if delta < sec['raw_size']:
                    offset = sec['raw_offset']+delta
                    if 0 <= offset < len(raw): return offset
        return None
    def cstring(offset: int, max_bytes=160):
        if offset is None or offset >= len(raw): return None
        value=raw[offset:min(offset+max_bytes,len(raw))].split(b'\0',1)[0]
        try: return value.decode('ascii') if value and all(31 < x < 127 for x in value) else None
        except UnicodeDecodeError: return None
    imports=[]
    start=file_offset(import_rva) if import_rva else None
    if start is not None:
        for i in range(80):
            location=start+i*20
            if location+20 > len(raw): break
            oft, stamp, chain, name_rva, thunk = struct.unpack_from('<IIIII',raw,location)
            if not any((oft,stamp,chain,name_rva,thunk)): break
            name=cstring(file_offset(name_rva))
            if name: imports.append(name)
    if imports:
        findings.append({'observation':'Referenced library names from PE import directory',
                         'evidence':{'libraries':imports[:60], 'count_examined':len(imports)}})
    return info, findings, warnings


def _apk_details(archive: zipfile.ZipFile, entries: list, name: str):
    """Inspect Android metadata only, never run APK code or inflate large members."""
    names=[e.filename for e in entries if not e.is_dir()]
    if not (name.endswith('.apk') or 'AndroidManifest.xml' in names):
        return {}, [], []
    details=[]
    warnings=[]
    dex=[n for n in names if re.fullmatch(r'classes\d*\.dex',n)]
    native=[n for n in names if n.startswith('lib/') and n.endswith('.so')]
    abi=sorted({n.split('/')[1] for n in native if len(n.split('/')) > 2})
    details.append({'observation':'Android application packaging verified from archive entries',
                    'evidence':{'dex_files':len(dex),'dex_paths':dex[:40],'native_libraries':len(native),
                                'native_abis':abi[:12], 'has_manifest':'AndroidManifest.xml' in names,
                                'has_signature_files':any(n.startswith('META-INF/') and n.endswith(('.RSA','.EC','.DSA')) for n in names)}})
    manifest=next((e for e in entries if e.filename=='AndroidManifest.xml'),None)
    if manifest and manifest.file_size <= 512*1024 and manifest.compress_size <= 512*1024:
        try:
            content=archive.read(manifest)
            # Android's on-disk manifest is usually binary AXML and is not plain XML.
            if content.lstrip().startswith(b'<?xml') or content.lstrip().startswith(b'<manifest'):
                if b'<!DOCTYPE' not in content and b'<!ENTITY' not in content:
                    root=ET.fromstring(content)
                    android='{http://schemas.android.com/apk/res/android}'
                    permissions=[e.get(android+'name') for e in root.findall('uses-permission')]
                    application=root.find('application')
                    flags={} if application is None else {
                        'debuggable':application.get(android+'debuggable'),
                        'allow_backup':application.get(android+'allowBackup'),
                        'uses_cleartext_traffic':application.get(android+'usesCleartextTraffic')}
                    exported=[]
                    if application is not None:
                        for kind in ('activity','service','receiver','provider'):
                            for component in application.findall(kind)[:80]:
                                if component.get(android+'exported')=='true':
                                    exported.append({'type':kind,'name':str(component.get(android+'name') or '')[:180]})
                    details.append({'observation':'Plain-text Android manifest settings observed',
                                    'evidence':{'package':root.get('package'),
                                                'requested_permissions':[x for x in permissions if x][:60],
                                                'application_attributes':flags,'explicitly_exported_components':exported[:80]}})
                else: warnings.append('Unsafe XML entity declarations were not parsed.')
            else:
                warnings.append('AndroidManifest.xml uses binary AXML; install an APK decoder to inspect manifest permissions.')
        except (OSError,ValueError,zipfile.BadZipFile,ET.ParseError):
            warnings.append('Android manifest could not be safely parsed.')
    elif manifest:
        warnings.append('Android manifest is too large for safe inline inspection.')
    return {'format':'Android APK / ZIP','platform':'Android'},details,warnings


def _file_review_leads(info: dict, observations: list[dict]) -> list[dict]:
    """Conservative security leads tied directly to observed file fields."""
    leads=[]
    flags=info.get('security_flags') or {}
    for key, label in [('aslr_compatible','ASLR compatibility bit is not set'),
                       ('nx_compatible','NX compatibility bit is not set')]:
        if key in flags and flags[key] is False:
            leads.append({'title':label,'confidence':'Observed PE header bit',
                          'verification_needed':'Check the supported loader and actual runtime mitigations.'})
    for observation in observations:
        evidence=observation.get('evidence') or {}
        for section in evidence.get('sections',[]):
            if section.get('writable') and section.get('executable'):
                leads.append({'title':'Writable and executable PE section declared',
                              'confidence':'Observed PE section flags',
                              'verification_needed':'Inspect loader mappings to determine actual permissions.'})
                break
        attrs=evidence.get('application_attributes') or {}
        if attrs.get('debuggable')=='true':
            leads.append({'title':'APK manifest enables debugging', 'confidence':'Observed plain-text manifest',
                          'verification_needed':'Determine whether this was intended for an internal test build.'})
        if attrs.get('uses_cleartext_traffic')=='true':
            leads.append({'title':'APK manifest permits cleartext traffic','confidence':'Observed plain-text manifest',
                          'verification_needed':'Review network security configuration and whether sensitive requests use TLS.'})
        if evidence.get('explicitly_exported_components'):
            leads.append({'title':'Android application declares exported components',
                          'confidence':'Observed plain-text manifest',
                          'verification_needed':'Review each exposed component and its permissions in an authorized lab.'})
    return leads[:20]

def inspect_source(source: dict):
    """Bounded static inspection of an already-imported local file."""
    path = Path(source.get('path') or '')
    if not source.get('original_retained') or not path.is_file():
        raise ValueError('The original uploaded file is unavailable; import it again.')
    if path.stat().st_size > 2 * 1024**3:
        raise ValueError('File exceeds the 2 GB static-inspection limit.')
    with path.open('rb') as handle:
        sample = handle.read(MAX_FILE_READ)
    info = _inspect_binary(sample, source.get('filename',''))
    details = []
    warnings = []
    if path.stat().st_size > MAX_FILE_READ:
        warnings.append('Only the first 32 MiB were inspected for structured metadata; the whole file was still hashed.')
    name = (source.get('filename') or path.name).lower()
    if info.get('format') == 'Windows PE executable (MZ)':
        pe_info, pe_evidence, pe_warnings = _pe_details(sample)
        info.update(pe_info)
        details.extend(pe_evidence)
        warnings.extend(pe_warnings)
    if path.stat().st_size <= MAX_FILE_READ and zipfile.is_zipfile(path):
        with zipfile.ZipFile(path) as archive:
            # The central directory lists metadata without inflating compressed members.
            entries = archive.infolist()
            if len(entries) > MAX_ARCHIVE_ENTRIES:
                warnings.append('Archive has more than 2,000 entries; only a bounded listing was reviewed.')
            entries = entries[:MAX_ARCHIVE_ENTRIES]
            names = [e.filename for e in entries if not e.is_dir()]
            details.append({'observation':'Archive entries listed without extracting or executing them',
                            'evidence':{'entry_count_reviewed':len(entries), 'sample_entries':names[:35]}})
            if any(e.file_size > MAX_ARCHIVE_MEMBER_BYTES for e in entries):
                warnings.append('One or more members are too large for safe automatic extraction.')
            apk_info,apk_evidence,apk_warnings = _apk_details(archive,entries,name)
            info.update(apk_info)
            details.extend(apk_evidence)
            warnings.extend(apk_warnings)
            if not apk_info and any(n.endswith('.class') for n in names):
                info['format']='Java archive (JAR / ZIP)'
    digest = hashlib.sha256()
    with path.open('rb') as handle:
        for block in iter(lambda:handle.read(1024*1024), b''):
            digest.update(block)
    return {'source_id':source['id'], 'filename':source.get('filename') or path.name,
            'size_bytes':path.stat().st_size, 'sha256':digest.hexdigest(), **info,
            'observations':details, 'review_leads':_file_review_leads(info,details), 'warnings':warnings,
            'verified':'File identification and metadata were read directly from the imported file.',
            'not_verified':'No exploit, ESP, unlimited-money modification, or memory pointer has been discovered or tested.'}


class Autopilot:
    def __init__(self, app):
        self.app = app
        self.store = app.store
        self._lock = threading.RLock()

    def _validate(self, params):
        text = params.get('message')
        if not isinstance(text,str) or not text.strip() or len(text)>MAX_INPUT:
            raise ValueError('Enter a request of up to 12,000 characters.')
        sources = params.get('source_ids') or []
        if not isinstance(sources,list) or len(sources)>MAX_FILES or any(not isinstance(s,str) for s in sources):
            raise ValueError('Choose up to six imported files.')
        project_id = params.get('project_id') or None
        if project_id and not self.store.get('project',project_id):
            raise ValueError('Select an existing project.')
        for source_id in sources:
            row=self.store.get('source',source_id)
            if not row or row.get('project_id') not in (None,project_id):
                raise ValueError('An attachment is unavailable in this project.')
        return text.strip(), list(dict.fromkeys(sources)), project_id

    def plan(self, **params):
        text, sources, project = self._validate(params)
        mode=choose_workflow(text,sources)
        readiness=[]
        if mode == 'app_checks' and not self.app.readiness_p7._suite_available('all'):
            readiness.append('App checks require the extracted source project and Python runtime. Use the development app to run them.')
        if mode in {'burp_history','burp_repeater','burp_issues','burp_review','burp_scan_start','web_pentest','staging_price'}:
            row=self.store.get('project',project) if project else None
            if not row or not row.get('scope') or not row.get('authorization'):
                readiness.append('Select an authorized website project before reading Burp history.')
        if mode=='staging_price' and project:
            from .staging_price import validate_configuration
            try:
                validate_configuration(self.app.policy.project(project))
            except ValueError as exc:
                readiness.append(str(exc))
        if mode in {'burp_repeater','burp_scan_start'} and not re.search(r'(?<![0-9])#?([1-9][0-9]{0,9})(?![0-9])',text):
            readiness.append('Include the Burp history number, for example: open Burp #42 in Repeater.')
        if mode=='file' and not sources:
            readiness.append('Attach your owned game, APK, executable or source file in Chat.')
        if mode=='website':
            row=self.store.get('project',project) if project else None
            if not row or not row.get('scope') or not row.get('authorization') or not row.get('read_only_paths'):
                readiness.append('Create an authorized website project with read-only paths.')
            config=self.store.setting('gateway',{})
            if not row or not row.get('lab_project'):
                import time
                if time.time()-float(config.get('verified_at') or 0)>300:
                    readiness.append('Connect the built-in secure web gateway in Settings.')
        from .policy import scope_hash
        scoped_project = self.store.get('project', project) if project else None
        return {'scope_snapshot': scope_hash(scoped_project) if scoped_project else None, 'workflow':mode, 'ready':not readiness and mode!='chat', 'missing':readiness,
                'requires_approval':mode in {'website','burp_repeater','burp_scan_start','burp_scan_stop','commerce_lab','staging_price'}, 'project_id':project,
                'title':{'file':'Inspect uploaded program', 'website':'Review authorized website',
                         'app_checks':'Run local app checks', 'knowledge_check':'Check knowledge integrity',
                         'knowledge':'Research my knowledge',
                         'burp_history':'Inspect scoped Burp proxy history', 'burp_review':'Review Burp traffic and Scanner findings', 'burp_repeater':'Open Burp history in Repeater', 'burp_issues':'Read Burp Scanner findings', 'burp_scan_start':'Start authorized Burp scan', 'burp_scan_stop':'Stop Burp scan', 'burp_scan_status':'Check Burp scan progress', 'web_pentest':'Review website and checkout security', 'commerce_lab':'Test checkout in disposable shop', 'staging_price':'Test staging-only price quotes', 'chat':'Answer in chat'}[mode],
                'explanation':{
                  'app_checks':'Run the fixed local regression suite, save live progress and a final report. Unavailable dependencies are reported, not counted as passes.',
                  'knowledge_check':'Inspect stored sources, citations and search indexes without changing your library or contacting a model.',
                  'file':'Read-only inspection of files you uploaded; no game or process modification.',
                  'website':'Only approved read-only GET pages, through the enforced project gateway.',
                  'knowledge':'Retrieve real passages from imported sources, with citations.',
                  'commerce_lab':'With approval, execute nine fixed checkout-integrity regression cases on the bundled loopback-only disposable shop. Never against a live store.',
                  'staging_price':'With approval, send three non-transactional quote requests to an owner-configured staging endpoint through the project policy gateway. No checkout, orders, or payments.',
                  'web_pentest':'Analyze existing scoped Burp traffic, pricing-related request field names and Scanner findings without submitting or changing any order.',
                  'burp_history':'Read real in-scope Burp history and passive header observations without sending requests.',
                  'burp_review':'Read authenticated, scoped Burp traffic and reported Scanner issues, then save one evidence-based summary. No requests are sent.',
                  'burp_repeater':'Open a reviewed, in-scope history request as a Burp Repeater tab; no network request is sent.',
                  'burp_issues':'Read findings already reported by Burp Scanner; no scan is started and no exploit is claimed.',
                  'burp_scan_start':'With explicit approval, start an active audit of one reviewed GET/HEAD Burp history item. The scan can change site data.',
                  'burp_scan_stop':'Stop the currently running audit in the local Burp extension.',
                  'burp_scan_status':'Read live request counts and scan status from the authenticated Burp extension.',
                  'chat':'Normal AI conversation.'}[mode]}

    def _record(self, project_id, text, mode, status='running'):
        return self.store.put('work_task',{
            'kind':'autopilot','title':text[:110], 'objective':text[:MAX_INPUT], 'status':status,
            'project_id':project_id, 'workflow':mode, 'source':'desktop_chat',
            'events':[{'time':now(),'kind':'started','text':'Chat-triggered '+mode+' workflow.'}],
            'result':None})

    def _finish(self, task, result, status='completed', error=None):
        previous=self.store.get('work_task',task['id']) or task
        events=previous.get('events',[])+[{'time':now(),'kind':status,'text':error or 'Results recorded; review evidence and limitations.'}]
        return self.store.put('work_task',{**previous,'status':status,'events':events[-100:],
                                            'result':result,'error':error},id=task['id'])

    def start(self, **params):
        self.store.require_desktop_owner()
        if self.store.setting('emergency_stop',False):
            raise ValueError('Tasks are stopped. Use Resume tasks in the top bar.')
        text, sources, project=self._validate(params)
        plan=self.plan(**params)
        mode=plan['workflow']
        if mode=='chat':
            raise ValueError('This is a normal question. Send it using Chat.')
        if plan['missing']:
            raise ValueError('Missing setup: '+' '.join(plan['missing']))
        if mode == 'app_checks':
            # An explicit desktop chat request selects a fixed suite; model text
            # cannot supply commands or approve target-facing actions.
            run = self.app.readiness_p7.start('all', approved=True)
            return self.store.put('work_task', {
                'kind': 'app_checks', 'workflow': mode, 'title': 'Local app checks',
                'objective': text, 'status': run['status'], 'project_id': project,
                'linked_kind': 'p7_run', 'linked_id': run['id'], 'source': 'desktop_chat',
                'events': [{'time': now(), 'kind': 'started', 'text': 'Local app checks started.'}],
            })
        if mode=='staging_price':
            if params.get('approved') is not True:
                raise ValueError('Approve staging-only quote checks before running.')
            return self.app.staging_price.start(project_id=project, approved=True)
        if mode=='commerce_lab':
            if params.get('approved') is not True:
                raise ValueError('Confirm checkout tests on the disposable local shop first.')
            # CommerceAudit starts a bounded loopback-only job and retains its
            # own evidence receipts; never takes a target URL from chat.
            return self.app.commerce.start(approved=True)
        if mode=='website':
            if params.get('approved') is not True:
                raise ValueError('Confirm authorization for the current website and paths first.')
            # Existing audit service revalidates the project/scope/transport on EVERY request.
            pages=params.get('max_pages',12)
            if type(pages) is not int or not 1<=pages<=30:
                raise ValueError('Website reviews support 1–30 pages.')
            return self.app.website_audit.start(project_id=project, approved=True, max_pages=pages, render_public=params.get('render_public',False))
        task=self._record(project,text,mode)
        try:
            if mode == 'knowledge_check':
                output = {'workflow': mode, 'integrity': self.app.knowledge.dispatch(
                    'knowledge.integrity', {'project_id': project if project else '*'})}
            elif mode=='burp_scan_start':
                if params.get('approved') is not True:raise ValueError('Confirm active Burp scanning first.')
                match=re.search(r'(?<![0-9])#?([1-9][0-9]{0,9})(?![0-9])',text)
                target=int(match.group(1)) if match else None
                output={'workflow':mode,**self.app.burp_history.dispatch('burp.audit.start',
                    {'project_id':project,'history_id':target,'approved':True})}
            elif mode=='burp_scan_status':
                output={'workflow':mode,**self.app.burp_history.dispatch('burp.audit.status',{})}
            elif mode=='burp_scan_stop':
                output={'workflow':mode,**self.app.burp_history.dispatch('burp.audit.stop',
                    {'approved':params.get('approved') is True})}
            elif mode=='burp_issues':
                output={'workflow':mode,**self.app.burp_history.dispatch('burp.issues',{'project_id':project})}
            elif mode=='burp_repeater':
                if params.get('approved') is not True:
                    raise ValueError('Approve opening the selected request in Burp first.')
                match=re.search(r'(?<![0-9])#?([1-9][0-9]{0,9})(?![0-9])',text)
                target=int(match.group(1)) if match else None
                output={'workflow':mode,**self.app.burp_history.dispatch('burp.repeater.open',
                    {'project_id':project,'history_id':target,'approved':True})}
            elif mode=='burp_review':
                # Composite, read-only workflow. The bridge methods independently
                # enforce project authorization and apply URL/path redaction.
                # Each step reports what was actually returned, not a model guess.
                receipts = []
                health = self.app.burp_history.dispatch('burp.health', {})
                if health.get('connected') is not True:
                    raise ValueError('Burp extension did not verify an authenticated connection.')
                receipts.append({'step':'bridge_health','observed':'Authenticated Burp extension responded'})
                history = self.app.burp_history.dispatch('burp.history', {'project_id':project})
                receipts.append({'step':'proxy_history','observed_count':len(history.get('items') or [])})
                issues = self.app.burp_history.dispatch('burp.issues', {'project_id':project})
                receipts.append({'step':'scanner_notifications','observed_count':len(issues.get('issues') or [])})
                output={'workflow':mode, 'project_id':project,
                        'history':(history.get('items') or [])[:100],
                        'passive_review_leads':(history.get('passive_review_leads') or [])[:24],
                        'issues':(issues.get('issues') or [])[:200],
                        'receipts':receipts,
                        'source':'Authenticated local Burp Montoya extension with project scope filtering',
                        'assessment':'Read-only inspection of existing records. Scanner findings require independent verification; no new network scan was started.'}
            elif mode=='web_pentest':
                # Multi-source, read-only assessment. Every fetched item is
                # checked against the currently authorized project by BurpHistory.
                data=self.app.burp_history.dispatch('burp.web.review', {'project_id':project})
                output={'workflow':mode, **data,
                    'assessment':'Passive coverage and review leads only. Price manipulation and scanner findings are not proven vulnerabilities; no modified requests were sent.'}
            elif mode=='burp_history':
                output={'workflow':mode, **self.app.burp_history.dispatch('burp.history', {'project_id':project})}
            elif mode=='file':
                results=[inspect_source(self.store.get('source',sid)) for sid in sources]
                output={'workflow':mode,'files':results,'evidence_source_ids':sources,
                        'assessment':'Observed file metadata only. No memory access or exploit performed.',
                        'provenance':'Locally computed SHA-256, binary signature and archive entries.'}
            elif mode=='knowledge':
                hits=self.app.knowledge.search_for_project(text, project, limit=6)
                tokens={token for token in re.findall(r'[a-z0-9_.-]{3,}',text.lower())
                        if token not in {'find','research','search','learn','about','knowledge','what','this','from','have','show','file','files'}}
                known=[]
                for item in self._verified_file_evidence(project):
                    searchable=(str(item.get('filename') or '')+' '+str(item.get('sha256') or '')+' '+str(item.get('format') or '')).lower()
                    if tokens and any(token in searchable for token in tokens):
                        known.append({'source_id':item['source_id'],'task_id':item['task_id'],
                                      'filename':item['filename'],'sha256':item['sha256'],
                                      'format':item['format'],
                                      'summary':'Observed metadata from original file; no vulnerability verification.'})
                    if len(known)>=8:break
                output={'workflow':mode,'references':[
                  {'source_id':h.get('source_id'),'chunk_id':h.get('id') or h.get('chunk_id'),
                   'title':h.get('title') or h.get('source_title') or 'Source',
                   'excerpt':str(h.get('text') or '')[:650]} for h in hits],
                   'verified_file_evidence':known,
                   'assessment':'Indexed passages are source references; file metadata has separately recorded SHA-256 provenance.'}
            else:
                raise ValueError('Unsupported workflow.')
            return self._finish(task,output)
        except Exception as exc:
            self._finish(task,{},status='failed',error=f'{type(exc).__name__}: {str(exc)[:450]}')
            raise

    def _verified_file_evidence(self, project_id=None):
        """Derive evidence only from completed, local, read-only file inspections."""
        records = []
        for task in self.store.list('work_task'):
            if (task.get('kind') != 'autopilot' or task.get('workflow') != 'file'
                    or task.get('status') != 'completed'
                    or task.get('project_id') != project_id):
                continue
            for item in (task.get('result') or {}).get('files', [])[:MAX_FILES]:
                if not (item.get('sha256') and item.get('source_id')):
                    continue
                records.append({'evidence_type':'observed_file_metadata',
                                'task_id':task['id'], 'source_id':item['source_id'],
                                'filename':item.get('filename'), 'sha256':item['sha256'],
                                'format':item.get('format'), 'platform':item.get('platform'),
                                'observations':item.get('observations',[]),
                                'review_leads':item.get('review_leads',[]),
                                'warnings':item.get('warnings',[]),
                                'review_state':'machine_observed_metadata',
                                'provenance':'Original local file read, hashed, and statically inspected',
                                'limitations':item.get('not_verified')})
        return records

    def _burp_evidence(self, project_id=None):
        """Export only sanitized, project-scoped observations recorded by Burp RPC.

        Never elevate a missing response header to a verified vulnerability.
        Raw requests, bodies, cookies, headers and query parameters aren't retained.
        """
        records=[]
        for task in self.store.list('work_task'):
            if (task.get('kind')!='autopilot' or task.get('workflow') not in {'burp_history','burp_review','web_pentest'}
                    or task.get('status')!='completed' or task.get('project_id')!=project_id):
                continue
            result=task.get('result') or {}
            if result.get('workflow') not in {'burp_history','burp_review','web_pentest'}:continue
            for item in (result.get('items') or result.get('history') or [])[:100]:
                if not isinstance(item,dict) or not str(item.get('source') or '').startswith('Burp HTTP history'):
                    continue
                # Only known, bounded fields are exported. The original bridge
                # strips headers/cookies/auth data and all query strings.
                fields={k:item.get(k) for k in ('method','url','status','burp_history_id','pricing_fields','response_checks')}
                if not isinstance(fields['url'],str) or '?' in fields['url']:continue
                canonical=json.dumps(fields,sort_keys=True,separators=(',',':'))
                records.append({'evidence_type':'observed_burp_http_metadata',
                                'task_id':task['id'],'project_id':project_id,
                                'observation_hash':hashlib.sha256(canonical.encode('utf-8')).hexdigest(),
                                **fields, 'review_state':'unreviewed_observation',
                                'provenance':'Authenticated Burp extension; filtered to Burp and approved project scopes',
                                'limitations':'Metadata and header-presence checks only; no exploit or vulnerability verified'})
        for task in self.store.list('work_task'):
            if (task.get('kind')!='autopilot' or task.get('workflow') not in {'burp_issues','burp_review','web_pentest'}
                    or task.get('status')!='completed' or task.get('project_id')!=project_id):
                continue
            for issue in ((task.get('result') or {}).get('issues') or [])[:200]:
                if not isinstance(issue,dict) or issue.get('source')!='Burp Scanner notification':continue
                fields={k:issue.get(k) for k in ('url','name','severity','confidence')}
                if not isinstance(fields['url'],str) or '?' in fields['url']:continue
                canonical=json.dumps(fields,sort_keys=True,separators=(',',':'))
                records.append({'evidence_type':'burp_scanner_notification','task_id':task['id'],
                                'project_id':project_id,'observation_hash':hashlib.sha256(canonical.encode()).hexdigest(),
                                **fields,'review_state':'requires_independent_verification',
                                'provenance':'Authenticated Burp Scanner notification; filtered to authorized project scope',
                                'limitations':'Burp scanner classification, not independently reproduced in BugHunter'})
        for task in self.store.list('work_task'):
            if task.get('kind')!='autopilot' or task.get('workflow')!='web_pentest' or task.get('status')!='completed' or task.get('project_id')!=project_id:
                continue
            for lead in (((task.get('result') or {}).get('review') or {}).get('price_and_checkout_review') or [])[:50]:
                evidence=lead.get('evidence') or {}
                if not isinstance(evidence,dict) or not isinstance(evidence.get('url'),str) or '?' in evidence['url']:
                    continue
                fields={k:evidence.get(k) for k in ('burp_history_id','method','url','pricing_fields','commerce_path')}
                canonical=json.dumps(fields,sort_keys=True,separators=(',',':'))
                records.append({'evidence_type':'checkout_price_review_lead','task_id':task['id'],
                    'project_id':project_id, 'observation_hash':hashlib.sha256(canonical.encode()).hexdigest(),
                    **fields, 'review_state':'not_verified','limitations':'A pricing field name or endpoint does not establish a vulnerability. No modified request was sent.'})
        return records

    def _staging_quote_evidence(self, project_id=None):
        """Only evidence-backed, non-transactional quote test outcomes.

        The manifest challenge and full HTTP bodies are intentionally excluded.
        """
        rows=[]
        for task in self.store.list('work_task'):
            if (task.get('kind') != 'staging_price' or task.get('status')!='completed'
                    or task.get('project_id')!=project_id):
                continue
            report=task.get('result') or {}
            for entry in report.get('cases',[])[:3]:
                fields={key:entry.get(key) for key in ('case','expected_total_minor','observed_total_minor','quote_matches_catalog','evidence_ids')}
                canonical=json.dumps(fields,sort_keys=True,separators=(',',':'))
                rows.append({'evidence_type':'staging_quote_integrity_observation',
                    'project_id':project_id,'task_id':task['id'],
                    'observation_hash':hashlib.sha256(canonical.encode()).hexdigest(),
                    **fields,'review_state':'observed_quote_only_unreviewed',
                    'provenance':'Staging contract and exact policy-gateway receipts',
                    'limitations':'Non-transactional quote comparison only. No checkout, orders, payments, or production exploit were verified.'})
        return rows

    def export_evidence(self, project_id=None):
        """Portable, clearly labelled observations; never model-generated exploit claims."""
        if project_id and not self.store.get('project', project_id):
            raise ValueError('Unknown project.')
        rows = self._verified_file_evidence(project_id) + self._burp_evidence(project_id) + self._staging_quote_evidence(project_id)
        content = '\n'.join(json.dumps(row, ensure_ascii=False) for row in rows)
        return {'filename':'bughunter-observed-file-evidence.jsonl',
                'content':content+('\n' if content else ''), 'count':len(rows),
                'description':'File metadata, Burp and quote-only staging observations; not trained weights or confirmed production exploits.'}

    def dispatch(self, method, params):
        if method=='autopilot.plan':return self.plan(**params)
        if method=='autopilot.start':return self.start(**params)
        if method=='autopilot.get':
            task=self.store.get('work_task',str(params.get('id','')))
            if not task or task.get('kind') not in {'autopilot','website_audit','cart_lab','staging_price','app_checks'}:
                raise ValueError('Task not found.')
            return task
        if method=='autopilot.export_evidence':return self.export_evidence(params.get('project_id'))
        raise ValueError('Unknown autonomous workflow method.')
