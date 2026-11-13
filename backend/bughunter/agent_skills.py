"""Bundled, versioned workflow instructions available to all model providers."""
from pathlib import Path
import json,hashlib

class AgentSkills:
    def __init__(self):self.root=Path(__file__).parent/'assets/agent_skills'
    def list(self,query=''):
        rows=json.loads((self.root/'manifest.json').read_text())
        return {'items':[{k:row[k] for k in ('id','title','version','package','package_version','source')} for row in rows if not query or query.casefold() in (row['id']+' '+row['title']).casefold()]}
    def read(self,id,path='SKILL.md',offset=0,limit=100):
        manifest=next((row for row in json.loads((self.root/'manifest.json').read_text()) if row['id']==id),None)
        if not manifest or path not in manifest['files']:raise ValueError('Choose an installed skill and one of its declared resources.')
        raw=(self.root/id/path).read_bytes()
        if hashlib.sha256(raw).hexdigest()!=manifest['files'][path]:raise ValueError('The bundled skill differs from its version manifest. Reinstall the matching app build.')
        lines=raw.decode().splitlines();offset=max(0,int(offset));limit=max(1,min(int(limit),120));selected=lines[offset:offset+limit]
        return {'id':id,'path':path,'version':manifest['version'],'package_version':manifest['package_version'],'text':'\n'.join(selected),'offset':offset,'next_offset':offset+len(selected) if offset+len(selected)<len(lines) else None,'total_lines':len(lines),'resources':list(manifest['files']),'source':manifest['source'],'note':'Workflow guidance. Task grants and actual tool capabilities still apply.'}
