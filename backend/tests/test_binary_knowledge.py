import json,struct,hashlib
import pytest
from bughunter.binary_knowledge import extract,metadata
from bughunter.import_formats import detect_format
from bughunter.knowledge import KnowledgeService,extraction_messages
from bughunter.store import Store

def pe_fixture():
    data=bytearray(2048);data[:2]=b'MZ';struct.pack_into('<I',data,0x3c,0x80);data[0x80:0x84]=b'PE\0\0'
    struct.pack_into('<HHIIIHH',data,0x84,0x14c,1,0,0,0,224,0x102);opt=0x98
    struct.pack_into('<H',data,opt,0x10b);struct.pack_into('<I',data,opt+16,0x1000);struct.pack_into('<I',data,opt+28,0x400000);struct.pack_into('<I',data,opt+60,0x200);struct.pack_into('<I',data,opt+92,16)
    section=opt+224;data[section:section+8]=b'.rdata\0\0';struct.pack_into('<IIII',data,section+8,0x600,0x1000,0x600,0x200);struct.pack_into('<I',data,section+36,0x40000040)
    struct.pack_into('<II',data,opt+104,0x1100,40);struct.pack_into('<IIIII',data,0x300,0x1180,0,0,0x1150,0x1180)
    data[0x350:0x35d]=b'KERNEL32.dll\0';struct.pack_into('<II',data,0x380,0x11c0,0);data[0x3c0:0x3ce]=b'\0\0GetTickCount\0'
    value=b'Case opening mini game';data[0x500:0x500+len(value)]=value
    return bytes(data)

def test_pe_metadata_imports_and_string_file_offsets(tmp_path,monkeypatch):
    data=pe_fixture();path=tmp_path/'game1.exe';path.write_bytes(data);monkeypatch.setattr('bughunter.binary_knowledge.shutil.which',lambda _:None)
    segments,messages=extract(path,tmp_path/'out',lambda:None)
    report=json.loads((tmp_path/'out/binary-analysis.json').read_text());assert report['format']=='PE' and report['architecture']=='x86'
    assert report['entry_rva']==0x1000 and report['entry_preferred_va']==0x401000 and not report['target_executed']
    assert report['sha256']==hashlib.sha256(data).hexdigest();assert report['imports']==[{'library':'KERNEL32.dll','name':'GetTickCount'}]
    string=next(r for r in report['readable_strings'] if 'Case opening' in r['text']);assert string['file_offset']==0x500
    assert any('0x500' in s['text'] and s['modality']=='binary_static' for s in segments)
    assert not extraction_messages(messages)[0]

def test_executable_bytes_are_never_used_as_the_program_to_launch(tmp_path,monkeypatch):
    path=tmp_path/'game1.exe';path.write_bytes(pe_fixture());calls=[]
    monkeypatch.setattr('bughunter.binary_knowledge.shutil.which',lambda _: '/trusted/objdump')
    def run(args,**kwargs):calls.append(args);return {'exit_code':0,'stdout':'Observed static listing','stderr':'','truncated':False}
    monkeypatch.setattr('bughunter.binary_knowledge.process_runtime.run',run)
    extract(path,tmp_path/'out',lambda:None)
    assert len(calls)==3 and all(args[0]=='/trusted/objdump' and args[-1]==str(path) for args in calls)
    assert any('--start-address=4198400' in args for args in calls)

def test_cancelled_import_does_not_publish_completed_analysis(tmp_path,monkeypatch):
    path=tmp_path/'game1.exe';path.write_bytes(pe_fixture());monkeypatch.setattr('bughunter.binary_knowledge.shutil.which',lambda _:None);calls=[]
    def check():
        calls.append(1)
        if len(calls)>2:raise InterruptedError('Stopped')
    with pytest.raises(InterruptedError):extract(path,tmp_path/'out',check)
    assert not (tmp_path/'out/binary-analysis.json').exists()

def test_invalid_pe_offsets_fail_before_optional_decoder(tmp_path,monkeypatch):
    data=bytearray(pe_fixture());struct.pack_into('<I',data,0x3c,0xfffffff0)
    with pytest.raises(ValueError,match='offset'):metadata(data)

def test_elf64_header_and_macho_header_are_detected(tmp_path):
    data=bytearray(64);data[:7]=b'\x7fELF\x02\x01\x01';struct.pack_into('<HHIQQQIHHHHHH',data,16,2,62,1,0x401000,0,0,0,64,0,0,0,0,0)
    assert metadata(data)['architecture']=='x86-64';assert metadata(data)['entry_preferred_va']==0x401000
    for magic,endian in [(b'\xce\xfa\xed\xfe','<'),(b'\xfe\xed\xfa\xce','>')]:
        raw=magic+struct.pack(endian+'IIIIII',7,3,2,0,0,0);path=tmp_path/'image';path.write_bytes(raw)
        assert detect_format(path)=='executable';assert metadata(raw)['format']=='Mach-O'

def test_import_retry_indexes_binary_and_links_matching_project_source(tmp_path,monkeypatch):
    path=tmp_path/'game1.exe';path.write_bytes(pe_fixture());store=Store(tmp_path/'data');service=KnowledgeService(store,start_worker=False)
    monkeypatch.setattr('bughunter.binary_knowledge.shutil.which',lambda _:None)
    cpp=store.put('source',{'title':'game1 source','filename':'game1.cpp','status':'ready','project_id':None})
    try:
        source=service.import_source(path=str(path),copy_original=False);result=service.process_source(source['id']);assert result['status']=='ready'
        hits=service.search('Case opening mini game');assert any(h['source_id']==source['id'] for h in hits)
        report=json.loads((store.root/'artifacts'/source['id']/'binary-analysis.json').read_text());assert report['source_companions']==[{'source_id':cpp['id'],'filename':'game1.cpp'}]
    finally:service.shutdown()
