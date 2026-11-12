"""Offline tests for demonstrably observed file evidence (no target execution)."""
import io
import json
import struct
import zipfile

import pytest

from bughunter.autopilot import Autopilot, choose_workflow, inspect_source
from bughunter.store import Store


def source_for(path, project_id=None):
    return {'id':'source_test','filename':path.name,'path':str(path),
            'project_id':project_id,'original_retained':True}


def test_pe_header_sections_and_import_library(tmp_path):
    raw = bytearray(4096)
    raw[:2] = b'MZ'
    struct.pack_into('<I', raw, 0x3c, 0x80)
    raw[0x80:0x84] = b'PE\0\0'
    struct.pack_into('<HHIIIHH', raw, 0x84, 0x8664, 1, 0, 0, 0, 0xf0, 0x0002)
    opt = 0x98
    struct.pack_into('<H', raw, opt, 0x20b)
    struct.pack_into('<H', raw, opt+70, 0x0140)
    struct.pack_into('<II', raw, opt+112+8, 0x1100, 40)
    section = opt+0xf0
    struct.pack_into('<8sIIII', raw, section,b'.rdata\0\0', 0x500,0x1000,0x500,0x400)
    struct.pack_into('<I', raw,section+36,0x40000040)
    # RVA 0x1100 => file offset 0x500; name RVA 0x1140 => 0x540
    struct.pack_into('<IIIII', raw, 0x500,0,0,0,0x1140,0)
    raw[0x540:0x54c] = b'KERNEL32.dll'
    file = tmp_path / 'offline_game.exe'
    file.write_bytes(raw)
    report=inspect_source(source_for(file))
    assert report['format']=='Windows PE executable (MZ)'
    assert report['architecture']=='x86-64'
    assert report['security_flags']['nx_compatible'] is True
    assert report['security_flags']['aslr_compatible'] is True
    assert report['sha256'] and len(report['sha256'])==64
    assert any('section headers' in entry['observation'] for entry in report['observations'])
    assert any('KERNEL32.dll' in entry['evidence'].get('libraries',[]) for entry in report['observations'])
    assert 'No exploit' in report['not_verified']


def test_apk_package_manifest_metadata_and_no_execution(tmp_path):
    file=tmp_path / 'offline_test.apk'
    manifest='''<?xml version="1.0"?><manifest xmlns:android="http://schemas.android.com/apk/res/android" package="test.local">
    <uses-permission android:name="android.permission.INTERNET"/>
    <application android:debuggable="true" android:allowBackup="false"/>
    </manifest>'''
    with zipfile.ZipFile(file,'w') as z:
        z.writestr('AndroidManifest.xml',manifest)
        z.writestr('classes.dex',b'dex\n')
        z.writestr('lib/arm64-v8a/libapp.so',b'\0\0')
    report=inspect_source(source_for(file))
    assert report['format']=='Android APK / ZIP'
    assert any(x['evidence'].get('native_abis') == ['arm64-v8a'] for x in report['observations'])
    manifest_observed=[x for x in report['observations'] if 'manifest settings' in x['observation']][0]
    assert manifest_observed['evidence']['package']=='test.local'
    assert manifest_observed['evidence']['application_attributes']['debuggable']=='true'
    assert manifest_observed['evidence']['requested_permissions']==['android.permission.INTERNET']


def test_binary_manifest_not_misrepresented(tmp_path):
    file=tmp_path/'offline_test.apk'
    with zipfile.ZipFile(file,'w') as z:
        z.writestr('AndroidManifest.xml',b'\x03\x00\x08\x00\x10\x00\x00\x00')
    report=inspect_source(source_for(file))
    assert not any('Plain-text Android manifest' in x['observation'] for x in report['observations'])
    assert any('binary AXML' in x for x in report['warnings'])


def test_export_only_completed_matching_project_file_evidence(tmp_path):
    store=Store(tmp_path)
    project=store.put('project',{'name':'Offline Lab'})
    other=store.put('project',{'name':'Another Lab'})
    data={'files':[{'source_id':'source1','filename':'demo.exe','sha256':'a'*64,
                    'format':'Windows PE executable (MZ)','platform':'Windows','observations':[],
                    'not_verified':'No dynamic verification'}]}
    store.put('work_task',{'kind':'autopilot','workflow':'file','project_id':project['id'],
                           'status':'completed','result':data})
    store.put('work_task',{'kind':'autopilot','workflow':'file','project_id':other['id'],
                           'status':'completed','result':data})
    store.put('work_task',{'kind':'autopilot','workflow':'file','project_id':project['id'],
                           'status':'failed','result':data})
    agent=Autopilot(type('App',(),{'store':store})())
    export=agent.export_evidence(project['id'])
    assert export['count']==1
    record=json.loads(export['content'])
    assert record['review_state']=='machine_observed_metadata'
    assert record['source_id']=='source1'
    assert len(agent._verified_file_evidence(other['id']))==1
    assert agent.export_evidence(None)['count']==0


def test_routing_and_missing_original_file(tmp_path):
    assert choose_workflow('Please inspect the attached APK',['source_test'])=='file'
    assert choose_workflow('Find vulnerabilities in my website')=='website'
    assert choose_workflow('Evaluate prompt injection on my AI')=='chat'
    assert choose_workflow('Learn prompt injection techniques')=='knowledge'
    assert choose_workflow('Explain prompt injection')=='knowledge'
    assert choose_workflow('Learn ESP for an offline game')=='knowledge'
    with pytest.raises(ValueError,match='unavailable'):
        inspect_source({'id':'source_test','filename':'gone.exe',
                        'path':str(tmp_path/'gone.exe'), 'original_retained':True})


def test_portable_knowledge_archive_includes_observations_not_training(tmp_path):
    from bughunter.knowledge_archive import KnowledgeArchive
    store=Store(tmp_path)
    with store.connection() as db:
        for name in ('knowledge_chunks','knowledge_nodes','knowledge_edges','knowledge_corrections'):
            db.execute(f'CREATE TABLE {name} (id TEXT PRIMARY KEY, source_id TEXT, locator TEXT, text TEXT)')
    source=store.put('source',{'filename':'demo.exe','path':str(tmp_path/'demo.exe'),'status':'ready'})
    store.put('work_task',{'kind':'autopilot','workflow':'file','project_id':None,
                           'status':'completed','result':{'files':[
                               {'source_id':source['id'],'filename':'demo.exe','sha256':'b'*64,
                                'format':'Windows PE executable (MZ)',
                                'observations':[{'observation':'Known header','evidence':{'magic':'MZ'}}],
                                'not_verified':'No runtime analysis'}]}})
    store.put('work_task',{'kind':'autopilot','workflow':'file','project_id':None,
                           'status':'failed','result':{'files':[{'source_id':'bogus','sha256':'0'*64}]}})
    output=KnowledgeArchive(type('Knowledge',(),{'store':store})()).export_bundle(include_frames=False)
    with zipfile.ZipFile(output['path']) as bundle:
        rows=[json.loads(line) for line in bundle.read('observed_file_evidence.jsonl').splitlines()]
        assert len(rows)==1
        assert rows[0]['source_id']==source['id']
        assert rows[0]['not_verified']=='No runtime analysis'
        assert bundle.read('train.jsonl') == b''
        assert bundle.read('evaluation.jsonl') == b''
        assert output['counts']['observed_file_evidence.jsonl']==1


def test_review_leads_are_explicitly_unverified(tmp_path):
    file=tmp_path/'debug_app.apk'
    manifest='''<manifest xmlns:android="http://schemas.android.com/apk/res/android" package="test.local">
    <application android:debuggable="true" android:usesCleartextTraffic="true">
       <activity android:name=".TestActivity" android:exported="true"/>
    </application></manifest>'''
    with zipfile.ZipFile(file,'w') as archive:
        archive.writestr('AndroidManifest.xml',manifest)
    report=inspect_source(source_for(file))
    titles=[lead['title'] for lead in report['review_leads']]
    assert 'APK manifest enables debugging' in titles
    assert 'APK manifest permits cleartext traffic' in titles
    assert 'Android application declares exported components' in titles
    assert all(lead['verification_needed'] for lead in report['review_leads'])
    assert report['not_verified'].startswith('No exploit')
