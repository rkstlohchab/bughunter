import {test,expect} from '@playwright/test';
import {chatFixture} from './chat-fixture';

test('REA setup appears only in Computer & apps and verifies its real engine requirements',async({page})=>{
 const state=await chatFixture(page);let checked=false;let cancelled=false;
 await page.route('http://127.0.0.1:8765/rpc',async route=>{
  if(route.request().method()==='OPTIONS')return route.fallback();
  const {id,method,params={}}=route.request().postDataJSON();let result:any;
  const status=()=>({installed:true,version:'6.7.0',tool_count:checked?207:0,config:{},engine_setup:{state:cancelled?'cancelled':'running',phase:'building',progress:100,detail:cancelled?'Setup stopped; saved files are retained.':'Building the native decompiler.'},native_detail:'Native analysis uses a compatible engine.',last_check:checked?{identity:{catalog:{counts:{mcp_tools:207}}},available_operations:120,providers:[{id:'ghidra',name:'Ghidra',status:'ready',reason:'Native decompiler verified.'}]}:{}});
  if(method==='rea.status')result=status();
  if(method==='rea.check'){checked=true;result=status();}
  if(method==='rea.cancel_setup'){cancelled=true;result=status();}
  if(method==='rea.configure')result={...status(),config:params};
  if(result===undefined)return route.fallback();
  state.calls.push({method,params});return route.fulfill({json:{id,result},headers:{'Access-Control-Allow-Origin':'*'}});
 });
 await page.goto('/');await page.getByRole('button',{name:'Settings',exact:true}).click();
 await expect(page.getByRole('heading',{name:'Reverse engineering',exact:true})).toBeHidden();
 await page.getByRole('tab',{name:'Computer & apps',exact:true}).click();
 const panel=page.getByRole('region',{name:'Reverse engineering settings'});
 await expect(panel).toBeVisible();await expect(page.getByRole('heading',{name:'Connect your AI',exact:true})).toBeHidden();
 await panel.getByRole('button',{name:'Check REA tools',exact:true}).click();
 await expect(panel.getByText('REA 6.7.0 installed · 207 tools verified',{exact:true})).toBeVisible();
 await panel.getByText('Analysis engines and requirements',{exact:true}).click();await expect(panel.getByText('Native decompiler verified.',{exact:true})).toBeVisible();
 await panel.getByRole('button',{name:'Cancel engine setup',exact:true}).click();await expect(panel.getByText('Setup stopped; saved files are retained.',{exact:true})).toBeVisible();
 for(const width of [760,390]){await page.setViewportSize({width,height:960});expect(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth)).toBe(true);}
 await page.screenshot({path:'artifacts/rea-integration-2026-11-13/rea-settings-mobile.png',fullPage:true});
 await page.getByRole('tab',{name:'General',exact:true}).click();await expect(panel).toBeHidden();
});
