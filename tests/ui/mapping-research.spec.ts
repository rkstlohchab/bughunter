import { expect, test, type Page } from '@playwright/test';
import { openTool } from './navigation';

async function desktop(page: Page, failCheck = false) {
  await page.addInitScript(({ failCheck }) => {
    const w = window as any;
    w.__mappingCalls = [];
    let pending: any = null;
    let observations: any[] = [];
    const citation = { id:'reference',title:'Fixture developer notes',kind:'library',source_id:'source_fixture',locator:'Score layout',url:'https://example.org/fixture' };
    let job: any = JSON.parse(localStorage.getItem('mapping-test-job') || 'null');
    const save = () => localStorage.setItem('mapping-test-job',JSON.stringify(job));
    w.__TAURI_INTERNALS__ = {
      transformCallback: () => 0,
      invoke: async (command: string,args: any) => {
        w.__mappingCalls.push({command,args});
        if(command==='ce_get_config') return {backend:'mac_native'};
        if(command==='ce_connect_mcp') return [{name:'verify_mapping',inputSchema:{}}];
        if(command==='ce_call_mcp_tool') return JSON.stringify({pid:42,process_instance_id:'42:fixture',program:'fixture.bin',build_sha256:'a'.repeat(64)});
        if(command==='ce_check_mapping') { if(failCheck) throw new Error('Selected process has changed. Select it again.'); return {check_id:args.checkId,saved:true}; }
        if(command!=='rpc') return {};
        const p = args.params || {};
        switch(args.method) {
          case 'settings.memory_bridge': return {supported:true,base_url:'http://127.0.0.1:11434/v1',model:'fixture',api_key:'',system_prompt:''};
          case 'memory.mappings.list': return job ? [job] : [];
          case 'memory.mappings.get': return job;
          case 'memory.mappings.start':
            job={id:'mapping_fixture',program:p.program,query:p.query,status:'ready',progress:'Candidates saved. Select the attached process and supply its currently observed value.',warnings:[],
              references:[citation],events:[{at:new Date().toISOString(),text:'Searched saved references.'}],verified_count:0,
              candidates:[{id:'candidate',label:'Score',status:'proposed',reference_id:'reference',quote:'fixture.bin stores its score at 0x100.',reason:'Test the cited score location.',recipe:{kind:'module_pointer',module_name:'fixture.bin',rva:'0x100',value_type:'i32'},observations:[]}]};
            observations=[];save();return job;
          case 'memory.mappings.check': pending=p; return {id:'mapping_check_'+'a'.repeat(32)};
          case 'memory.mappings.observe':
            observations.push({check_id:'check'+observations.length,at:new Date().toISOString(),value:Number(pending.expected_value),expected:Number(pending.expected_value),matched:true,address:'0x1234',build_sha256:'a'.repeat(64)});
            job.candidates[0].observations=observations;
            job.candidates[0].status=new Set(observations.map((o:any)=>o.value)).size>=2?'value_verified':'matched_once';
            job.verified_count=job.candidates[0].status==='value_verified'?1:0;save();return job;
          case 'memory.mappings.export': return {filename:'mapping-evidence.json',content:JSON.stringify(job)};
          case 'system.status': return {version:'0.1.0',emergency_stop:false};
          case 'models.available': return {providers:[],active:{}};
          case 'training.workspaces': return [];
          default: return args.method.endsWith('.list') ? [] : {};
        }
      }
    };
  },{failCheck});
}

async function openResearch(page: Page) {
  await page.goto('/'); await openTool(page,'Cheat Engine');
  return page.getByRole('region',{name:'Cited mapping research'});
}

async function prepare(page: Page) {
  const panel=await openResearch(page);
  await page.getByRole('button',{name:'Start memory helper'}).click();
  await panel.getByRole('button',{name:'Use attached program'}).click();
  await panel.getByLabel('What should we find?').fill('Research score mapping');
  await panel.getByRole('button',{name:'Research references',exact:true}).click();
  await expect(panel.getByRole('article',{name:'Score'})).toBeVisible();
  return panel;
}

test('cited mapping checks persist and export only after two distinct observed values',async({page})=>{
  await desktop(page);
  let panel=await prepare(page);
  await expect(panel.getByRole('link',{name:'Fixture developer notes'})).toHaveAttribute('href','https://example.org/fixture');
  await expect(panel.getByRole('button',{name:'Check candidate',exact:true})).toBeDisabled();
  await panel.getByLabel('Value currently visible in the selected program').fill('100');
  await panel.getByRole('button',{name:'Check candidate',exact:true}).click();
  await expect(panel.getByText('Matched once',{exact:true})).toBeVisible();
  await panel.getByLabel('Value currently visible in the selected program').fill('90');
  await panel.getByRole('button',{name:'Check candidate',exact:true}).click();
  await expect(panel.getByText('Verified for this process',{exact:true})).toBeVisible();
  const calls=await page.evaluate(()=>(window as any).__mappingCalls);
  const native=calls.filter((c:any)=>c.command==='ce_check_mapping');
  expect(native).toHaveLength(2); expect(Object.keys(native[0].args)).toEqual(['checkId']);
  const uploads=calls.filter((c:any)=>c.args?.method==='memory.mappings.observe');
  expect(Object.keys(uploads[0].args.params)).toEqual(['check_id']);
  panel=await openResearch(page);
  await expect(panel.getByText('Verified for this process',{exact:true})).toBeVisible();
  await expect(panel.getByRole('button',{name:'Check candidate',exact:true})).toBeDisabled();
  const downloaded=page.waitForEvent('download');
  await panel.getByRole('button',{name:'Export cited results'}).click();
  expect((await downloaded).suggestedFilename()).toBe('mapping-evidence.json');
});

test('native check failure keeps hypothesis unverified and reports error',async({page})=>{
  await desktop(page,true); const panel=await prepare(page);
  await panel.getByLabel('Value currently visible in the selected program').fill('100');
  await panel.getByRole('button',{name:'Check candidate',exact:true}).click();
  await expect(panel.getByRole('alert')).toContainText('Selected process has changed');
  await expect(panel.getByText('Candidate',{exact:true})).toBeVisible();
  const calls=await page.evaluate(()=>(window as any).__mappingCalls);
  expect(calls.some((c:any)=>c.args?.method==='memory.mappings.observe')).toBe(false);
});

test('mapping research remains usable on a narrow viewport',async({page})=>{
  await desktop(page); await page.setViewportSize({width:390,height:844});
  const panel=await prepare(page);
  await expect(panel.getByRole('button',{name:'Export cited results'})).toBeVisible();
  expect(await page.evaluate(()=>document.documentElement.scrollWidth<=window.innerWidth)).toBe(true);
  await page.screenshot({path:'/tmp/bughunter-mapping-mobile.png',fullPage:true});
});

