import { useEffect, useState } from 'react';
import { Cpu, RefreshCw } from 'lucide-react';
import { Button, Field } from './components';
import { ceMcpApi, isDesktop } from './ceMcpApi';
import type { CeAppConfig, CePreflightItem } from './ceMcpApi';

export default function MemoryToolsSetup(){
 const [config,setConfig]=useState<CeAppConfig|null>(null);
 const [checks,setChecks]=useState<CePreflightItem[]>([]);
 const [message,setMessage]=useState('');
 const [working,setWorking]=useState(false);
 const desktop=isDesktop();
 async function refresh(){if(!desktop)return;setWorking(true);
   try{const c=await ceMcpApi.getConfig();setConfig(c);setChecks(await ceMcpApi.preflight());setMessage('Checked the active Mac memory helper.');}
   catch(e){setMessage(String(e));}finally{setWorking(false);}}
 useEffect(()=>{void refresh();},[]);
 async function testNativeMemory(){
   if(!desktop||!config||working)return;
   setWorking(true);
   setMessage('Running a real read/write test on a temporary value in the Mac memory helper. No external app will be touched.');
   let startedHere=false;
   try{
     const status=await ceMcpApi.connectionHealth();
     const currentBackend=status.connected?status.backend:config.backend;
     if(currentBackend!=='mac_native')throw new Error('Choose the built-in Mac memory reader and save the selection first. This test does not validate external Cheat Engine.');
     if(!status.connected){await ceMcpApi.connectMcp();startedHere=true;}
     const raw=await ceMcpApi.callTool('bridge_self_test',{});
     const report=JSON.parse(raw) as {passed?:boolean;backend?:string;steps?:string[];external_process_touched?:boolean};
     if(report.passed!==true||report.external_process_touched!==false||report.steps?.length!==4){throw new Error('The bridge did not return complete read/write/restore evidence.');}
     setMessage(`Passed: ${report.backend} read, changed, checked, and restored its own temporary value. No game was touched. Access to other processes and Cheat Engine is NOT verified.`);
   }catch(e){setMessage(`Failed: ${String(e)}. A successful connection alone does not prove process-memory access.`);}
   finally{
     if(startedHere)await ceMcpApi.disconnectMcp().catch(()=>{});
     setWorking(false);
   }
 }
 async function save(){if(!config)return;setWorking(true);
   try{await ceMcpApi.saveConfig(config);setMessage('Memory helper settings saved. Disconnect it in Tasks before changing its backend.');await refresh();}
   catch(e){setMessage(String(e));}finally{setWorking(false);}}
 return <section className="surface settings-card"><div className="section-title"><div><h2>Memory helper</h2><p>For learning about your own offline programs. This is separate from whether Cheat Engine is installed.</p></div><Cpu size={20}/></div>
  {!desktop?<p className="muted">Available in the Mac desktop app.</p>:<div className="settings-card-body">
    <p className="muted">The Mac memory reader is included with BugHunterClaudex. It may connect even when Cheat Engine is not installed. You still need macOS permission to attach to an allowed process; protected processes may reject access.</p>
    {config&&<><Field label="Memory reader"><select value={config.backend} onChange={e=>setConfig({...config,backend:e.target.value as CeAppConfig['backend']})}><option value="mac_native">Built-in Mac memory reader (recommended)</option><option value="cheat_engine">Cheat Engine MCP server (Windows/Wine only)</option></select></Field>
      <details className="settings-more"><summary>Advanced connection details</summary><div className="settings-more-body"><Field label="Python command"><input value={config.python_bin} onChange={e=>setConfig({...config,python_bin:e.target.value})}/></Field>
      <Field label="Bridge script path (optional)"><input value={config.backend==='mac_native'?config.mac_script_path:config.mcp_script_path} onChange={e=>setConfig({...config,[config.backend==='mac_native'?'mac_script_path':'mcp_script_path']:e.target.value})} placeholder="Leave empty for built-in Mac reader"/></Field>
      {config.backend==='cheat_engine'&&<><Field label="Cheat Engine server address"><input value={config.ce_host} onChange={e=>setConfig({...config,ce_host:e.target.value})}/></Field><Field label="Cheat Engine server port"><input value={config.ce_port} onChange={e=>setConfig({...config,ce_port:e.target.value})}/></Field><Field label="Server access token"><input type="password" autoComplete="off" value={config.ce_auth_token} onChange={e=>setConfig({...config,ce_auth_token:e.target.value})}/></Field></>}
      </div></details><div className="button-group wrap"><Button disabled={working} onClick={save}>Save memory helper</Button><Button disabled={working} icon={RefreshCw} onClick={()=>void refresh()}>Check memory helper</Button>{config.backend==='mac_native'&&<Button disabled={working} onClick={()=>void testNativeMemory()}>Test memory reader</Button>}</div></>}
    {message&&<p role="status" className="muted">{message}</p>}
    {checks.length>0&&<details className="settings-more"><summary>Diagnostic details ({checks.filter(c=>c.status==='ok').length}/{checks.length} checks passed)</summary><div className="settings-more-body">{checks.map(c=><p key={c.id}><strong>{c.label}</strong> — {c.status==='ok'?'Ready':c.detail}{c.fix&&<>. {c.fix}</>}</p>)}</div></details>}
  </div>}
 </section>;
}
