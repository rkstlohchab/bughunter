"""Bounded static executable evidence. The imported file is read, never launched."""
from pathlib import Path
import hashlib,json,mmap,re,shutil,struct
from . import process_runtime
from .security import redact_text

MAX_BYTES=128*1024**2

class Reader:
    def __init__(self,data):self.data=data;self.size=len(data)
    def at(self,offset,size):
        if offset<0 or size<0 or offset+size>self.size:raise ValueError('Truncated executable or invalid file offset.')
        return self.data[offset:offset+size]
    def unpack(self,fmt,offset):return struct.unpack(fmt,self.at(offset,struct.calcsize(fmt)))
    def string(self,offset,limit=512):return self.at(offset,min(limit,self.size-offset)).split(b'\0',1)[0].decode('utf-8','replace')

def pe(reader):
    offset=reader.unpack('<I',0x3c)[0]
    if reader.at(offset,4)!=b'PE\0\0':raise ValueError('Invalid PE signature in executable.')
    machine,count,stamp,symbol_at,symbol_count,optional_size,flags=reader.unpack('<HHIIIHH',offset+4)
    if not 1<=count<=96:raise ValueError('Invalid PE section count.')
    opt=offset+24;reader.at(opt,optional_size);magic=reader.unpack('<H',opt)[0]
    if magic not in (0x10b,0x20b):raise ValueError('Unsupported PE optional header.')
    bits=64 if magic==0x20b else 32;minimum=112 if bits==64 else 96
    if optional_size<minimum:raise ValueError('Truncated PE optional header.')
    entry=reader.unpack('<I',opt+16)[0];base=reader.unpack('<Q' if bits==64 else '<I',opt+(24 if bits==64 else 28))[0]
    sections=[]
    for i in range(count):
        at=opt+optional_size+i*40;name=reader.at(at,8).rstrip(b'\0').decode('ascii','replace');virtual_size,rva,size,file_at=reader.unpack('<IIII',at+8);characteristics=reader.unpack('<I',at+36)[0]
        if size:reader.at(file_at,size)
        sections.append({'name':name,'file_offset':file_at,'file_size':size,'rva':rva,'virtual_size':virtual_size,'flags':hex(characteristics)})
    def file_offset(rva):
        headers=reader.unpack('<I',opt+60)[0]
        if 0<=rva<headers and rva<reader.size:return rva
        for section in sections:
            delta=rva-section['rva']
            if 0<=delta<section['file_size']:return section['file_offset']+delta
        raise ValueError('PE directory points outside stored section bytes.')
    imports=[];exports=[];notes=[];directories=reader.unpack('<I',opt+(108 if bits==64 else 92))[0]
    try:
        if directories>1 and optional_size>=minimum+16:
            table_rva,table_size=reader.unpack('<II',opt+minimum+8)
            if table_rva:
                table=file_offset(table_rva)
                for i in range(min(table_size//20,256)):
                    original,_,_,name_rva,first=reader.unpack('<IIIII',table+i*20)
                    if not (original or name_rva or first):break
                    dll=reader.string(file_offset(name_rva));thunk=file_offset(original or first);step=bits//8
                    for j in range(4096):
                        value=reader.unpack('<Q' if bits==64 else '<I',thunk+j*step)[0]
                        if not value:break
                        name='ordinal '+str(value&0xffff) if value&(1<<(bits-1)) else reader.string(file_offset(value)+2)
                        imports.append({'library':dll,'name':name})
                        if len(imports)>=4096:break
                    if len(imports)>=4096:notes.append('Import entries capped at 4096.');break
        if directories and optional_size>=minimum+8:
            export_rva,export_size=reader.unpack('<II',opt+minimum)
            if export_rva:
                table=file_offset(export_rva);name_count=reader.unpack('<I',table+24)[0];names=file_offset(reader.unpack('<I',table+32)[0]) if name_count else 0
                for i in range(min(name_count,4096)):exports.append(reader.string(file_offset(reader.unpack('<I',names+i*4)[0])))
    except ValueError as error:notes.append('Some PE import/export entries could not be read: '+str(error))
    return {'format':'PE','bits':bits,'architecture':{0x14c:'x86',0x8664:'x86-64',0xaa64:'ARM64',0x1c4:'ARM'}.get(machine,hex(machine)),'image_base':base,'entry_rva':entry,'entry_preferred_va':base+entry,'address_kind':'preferred linked virtual address, not an observed runtime address','sections':sections,'imports':imports,'exports':exports,'coff_symbol_count':symbol_count,'notes':notes}

def elf(reader):
    bits={1:32,2:64}.get(reader.at(4,1)[0]);endian={1:'<',2:'>'}.get(reader.at(5,1)[0])
    if not bits or not endian:raise ValueError('Invalid ELF class or byte order.')
    header=reader.unpack(endian+('HHIQQQIHHHHHH' if bits==64 else 'HHIIIIIHHHHHH'),16)
    kind,machine,_,entry,_,section_at,_,_,_,_,section_size,count,names_index=header
    if count>4096 or count and section_size<(64 if bits==64 else 40):raise ValueError('Invalid ELF section table.')
    sections=[]
    for i in range(count):
        row=reader.unpack(endian+('IIQQQQIIQQ' if bits==64 else 'IIIIIIIIII'),section_at+i*section_size)
        name,type,flags,address,at,size,*_=row
        if type!=8 and size:reader.at(at,size)
        sections.append({'name_offset':name,'type':type,'flags':hex(flags),'virtual_address':address,'file_offset':at,'file_size':size})
    if sections and names_index<len(sections):
        names=sections[names_index]
        for section in sections:section['name']=reader.string(names['file_offset']+section['name_offset'],min(256,max(0,names['file_size']-section['name_offset'])))
    return {'format':'ELF','bits':bits,'architecture':{3:'x86',62:'x86-64',183:'ARM64',40:'ARM',243:'RISC-V'}.get(machine,str(machine)),'entry_preferred_va':entry,'address_kind':'linked virtual address, not an observed runtime address','file_type':kind,'sections':sections,'notes':[]}

MACH_MAGICS={b'\xce\xfa\xed\xfe':('<',32),b'\xcf\xfa\xed\xfe':('<',64),b'\xfe\xed\xfa\xce':('>',32),b'\xfe\xed\xfa\xcf':('>',64)}
FAT_MAGICS={b'\xca\xfe\xba\xbe':('>',False),b'\xbe\xba\xfe\xca':('<',False),b'\xca\xfe\xba\xbf':('>',True),b'\xbf\xba\xfe\xca':('<',True)}

def macho(reader):
    magic=reader.at(0,4)
    if magic in FAT_MAGICS:
        endian,wide=FAT_MAGICS[magic];count=reader.unpack(endian+'I',4)[0]
        if not 1<=count<=32:raise ValueError('Invalid universal Mach-O slice count.')
        slices=[];fmt=endian+('IIQQII' if wide else 'IIIII')
        for i in range(count):
            cpu,subtype,at,size,*_=reader.unpack(fmt,8+i*struct.calcsize(fmt));reader.at(at,size);slices.append({'cpu_type':hex(cpu),'file_offset':at,'file_size':size})
        return {'format':'Mach-O universal','architecture':'multiple slices','slices':slices,'sections':[],'notes':['Each universal slice has its own linked addresses.']}
    endian,bits=MACH_MAGICS[magic];cpu,subtype,kind,count,size,flags=reader.unpack(endian+'IIIIII',4);at=32 if bits==64 else 28;end=at+size;reader.at(at,size)
    if count>4096:raise ValueError('Invalid Mach-O load command count.')
    libraries=[];commands=[]
    for _ in range(count):
        command,length=reader.unpack(endian+'II',at)
        if length<8 or at+length>end:raise ValueError('Invalid Mach-O load command size.')
        commands.append(hex(command))
        if command in {0xc,0x18,0x80000018,0x8000001f,0x20,0x80000023}:
            name_at=reader.unpack(endian+'I',at+8)[0]
            if 12<=name_at<length:libraries.append(reader.string(at+name_at,length-name_at))
        at+=length
    return {'format':'Mach-O','bits':bits,'architecture':{7:'x86',0x1000007:'x86-64',12:'ARM',0x100000c:'ARM64'}.get(cpu,hex(cpu)),'file_type':kind,'load_commands':commands,'linked_libraries':libraries,'sections':[],'notes':[]}

def metadata(data):
    reader=Reader(data);magic=reader.at(0,4)
    if magic[:2]==b'MZ':return pe(reader)
    if magic==b'\x7fELF':return elf(reader)
    if magic in MACH_MAGICS or magic in FAT_MAGICS:return macho(reader)
    raise ValueError('Unsupported executable file signature.')

def extract(path,artifacts,check,companions=()):
    path=Path(path).resolve();size=path.stat().st_size
    if not 4<=size<=MAX_BYTES:raise ValueError('Static binary imports require a file between 4 bytes and 128 MiB.')
    check();artifacts.mkdir(parents=True,exist_ok=True)
    with path.open('rb') as stream,mmap.mmap(stream.fileno(),0,access=mmap.ACCESS_READ) as data:
        identity=hashlib.sha256(data).hexdigest();report=metadata(data);strings=[]
        for encoding,pattern in [('ascii',rb'[\x20-\x7e]{6,256}'),('utf-16le',rb'(?:[\x20-\x7e]\x00){6,256}')]:
            count=0
            for match in re.finditer(pattern,data):
                check();text=match.group().decode(encoding)
                if len(re.findall(r'[A-Za-z]',text))<4:continue
                strings.append({'file_offset':match.start(),'encoding':encoding,'text':redact_text(text)});count+=1
                if count>=500:report['notes'].append(encoding+' string extraction capped at 500 entries.');break
    report.update(sha256=identity,size_bytes=size,analysis_mode='static file inspection',target_executed=False,readable_strings=strings,source_companions=list(companions))
    notes=list(report['notes']);segments=[]
    def add(title,text,**details):segments.append({'text':redact_text(text),'locator':title,'modality':'binary_static','binary_sha256':identity,'assertion_status':'observed','origin':'static analysis',**details})
    header={k:v for k,v in report.items() if k not in {'readable_strings','imports','exports','sections','notes'}}
    add('Static executable metadata',json.dumps(header,indent=2)+'\nAn imported binary is evidence; strings and imports alone do not establish behavior. Companion source names are candidates, not proof of the same build.')
    if report.get('sections'):add('Executable section table',json.dumps(report['sections'],indent=2))
    for kind in ('imports','exports','linked_libraries'):
        entries=report.get(kind,[])
        for start in range(0,len(entries),40):add(f'Executable {kind} · entries {start+1}–{min(start+40,len(entries))}',json.dumps(entries[start:start+40],indent=2))
    for start in range(0,len(strings),24):
        group=strings[start:start+24];first=group[0]['file_offset'];add(f'Binary strings · file offset 0x{first:x}','\n'.join(f"File offset 0x{row['file_offset']:x} [{row['encoding']}]: {row['text']}" for row in group),file_offset=first)
    decoder=shutil.which('llvm-objdump') or shutil.which('objdump')
    if decoder and Path(decoder).resolve()!=path:
        jobs=[('headers',['--private-headers']),('symbols',['--syms','--demangle'])]
        entry=report.get('entry_preferred_va')
        if entry:jobs.append(('entry-disassembly',['--disassemble','--demangle',f'--start-address={entry}',f'--stop-address={entry+8192}']))
        report['decoder']=str(Path(decoder).resolve());report['decoder_outputs']={}
        for name,args in jobs:
            check()
            try:
                result=process_runtime.run([decoder,*args,str(path)],timeout=30,check=check,output_limit=200000)
                if result['exit_code']:
                    notes.append('Optional '+name+' inspection unavailable: '+result['stderr'][-160:]);continue
                output=redact_text(result['stdout']);target=artifacts/('binary-'+name+'.txt');target.write_text(output);report['decoder_outputs'][name]={'path':str(target),'truncated':result['truncated']}
                for index in range(0,len(output),7000):add(f'Static {name} · excerpt {index//7000+1}',output[index:index+7000]+'\nAddresses are tool-reported linked addresses or explicitly section-relative symbol offsets; no runtime mapping was observed.',artifact_id=target.name)
                if result['truncated']:notes.append(name+' output is a bounded tail; the complete listing is not indexed.')
            except InterruptedError:raise
            except (OSError,TimeoutError) as error:notes.append('Optional '+name+' inspection unavailable: '+str(error)[:160])
    else:notes.append('Install LLVM binutils for optional symbol listings and entry-point disassembly. Metadata and strings were indexed.')
    check();report['notes']=notes;temporary=artifacts/'binary-analysis.tmp';temporary.write_text(json.dumps(report,indent=2));temporary.replace(artifacts/'binary-analysis.json')
    return segments,['Static binary analysis note: '+note for note in notes]
