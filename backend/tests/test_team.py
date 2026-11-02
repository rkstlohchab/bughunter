from concurrent.futures import ThreadPoolExecutor
import tomllib

import pytest

from bughunter.app import Application
from bughunter.team import TeamTools


def test_simultaneous_agents_claim_exclusive_paths_and_publish_handoffs(tmp_path):
    app = Application(tmp_path/'data',primary=False)
    try:
        repo = tmp_path/'repo'; repo.mkdir()
        team = app.team.create('Application development',str(repo)); tid=team['id']
        one = app.team.task_create(tid,'Frontend',['src'])
        two = app.team.task_create(tid,'Shared component',['src/App.tsx'])
        tools = [TeamTools(app,tid,name) for name in ('claude','codex')]
        def claim(i):
            try: return tools[i].call('team_claim',{'id':[one,two][i]['id']})
            except ValueError as e: return str(e)
        with ThreadPoolExecutor(2) as pool: results=list(pool.map(claim,[0,1]))
        assert sum(isinstance(r,dict) for r in results)==1
        index = next(i for i,r in enumerate(results) if isinstance(r,dict)); owned=results[index]
        params = {'id':owned['id'],'claim_key':owned['claim_key'],'revision':owned['revision'],'progress':'UI implemented'}
        with pytest.raises(ValueError,match='current exclusive'):
            tools[1-index].call('team_update',params)
        updated = tools[index].call('team_update',params)
        with pytest.raises(ValueError,match='Stale revision'): tools[index].call('team_update',params)
        with pytest.raises(ValueError,match='handoff'): tools[index].call('team_update',{**params,'revision':updated['revision'],'status':'done'})
        tools[index].call('team_update',{**params,'revision':updated['revision'],'status':'done','verification':'UI checks passed','handoff':'Ready for review'})
        other=tools[1-index].call('team_claim',{'id':[one,two][1-index]['id']})
        assert other['owner'] in {'claude','codex'}
        with ThreadPoolExecutor(2) as pool:
            list(pool.map(lambda i:tools[i].call('team_note',{'text':f'Agent {i} handoff'}),[0,1]))
        status=tools[0].call('team_status',{})
        assert len(status['notes'])==2
        assert all('claim_hash' not in t and 'claim_key' not in t for t in status['tasks'])
        app.team.release(tid,other['id'],'Stopped the editor and inspected partial changes')
        with pytest.raises(ValueError): tools[1-index].call('team_update',{'id':other['id'],'claim_key':other['claim_key'],'revision':other['revision']})
        config=app.team.connection(tid,'codex')
        assert tomllib.loads(config['codex_config'])['mcp_servers']['bughunter_team']['args']==config['mcpServers']['bughunter_team']['args']
        for name in ['actions.approve','projects.save','team.release','team.task_create','shell','sources.import']:
            with pytest.raises(ValueError,match='not available'): tools[0].call(name,{})
    finally: app.shutdown()


def test_team_scope_and_path_aliases(tmp_path):
    app=Application(tmp_path/'data',primary=False)
    try:
        repo=tmp_path/'repo';repo.mkdir();(repo/'src').mkdir();(repo/'alias').symlink_to(repo/'src')
        team=app.team.create('One',str(repo)); other=app.team.create('Two',str(tmp_path))
        for path in ['../out','/tmp/out','src/*','alias/file.py']:
            with pytest.raises(ValueError): app.team.task_create(team['id'],'Bad',[path])
        task=app.team.task_create(other['id'],'Other',['.'])
        with pytest.raises(ValueError): TeamTools(app,team['id'],'codex').call('team_claim',{'id':task['id']})
        with pytest.raises(ValueError): TeamTools(app,team['id'],'../invalid')
    finally: app.shutdown()
