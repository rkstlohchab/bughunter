import {test,expect} from '@playwright/test';
import {chatFixture} from './chat-fixture';

test('resource row and open details update without refresh while unrelated requests are stalled',async({page})=>{
 await chatFixture(page);let release=()=>{};const stalled=new Promise<void>(resolve=>{release=resolve;});
 let source:any={id:'day1',title:'Day 1 recording',filename:'day1.pdf',status:'queued',chunk_count:1,media_control:'resume',media_progress:{phase:'frames',analyzed:1041,saved_analyzed:1041,total:1363}};
 const initial=structuredClone(source);let detailReads=0;
 await page.route('http://127.0.0.1:8765/rpc',async route=>{
  if(route.request().method()==='OPTIONS')return route.fallback();
  const {id,method}=route.request().postDataJSON();let result:any;
  if(method==='traffic.list'){await stalled;result=[];}
  if(method==='sources.list')result=[source];
  if(method==='sources.get'){detailReads++;result={...initial,chunks:[{id:'chunk_one',text:source.status==='ready'?'Newly indexed final passage.':'Saved initial passage.'}]};}
  if(result===undefined)return route.fallback();await route.fulfill({headers:{'Access-Control-Allow-Origin':'*'},json:{id,result}});
 });
 try{
  await page.goto('/');await page.getByRole('button',{name:'Library',exact:true}).click();
  await page.getByRole('button',{name:'Day 1 recording',exact:true}).click();
  const dialog=page.getByRole('dialog',{name:'Day 1 recording',exact:true});await expect(dialog).toBeVisible();
  const row=page.getByRole('row').filter({has:page.getByRole('button',{name:'Day 1 recording',exact:true})});
  source={...source,status:'processing',media_progress:{...source.media_progress,analyzed:1140,saved_analyzed:1140}};
  await expect(row.getByText('Reading video frames: 1140 / 1363 frames',{exact:true})).toBeVisible({timeout:3500});
  await expect(dialog.getByText('Reading video frames: 1140 / 1363 frames',{exact:true})).toBeVisible();
  source={...source,media_control:'pause'};
  await expect(row.locator('.status')).toHaveText('paused',{timeout:3500});await expect(dialog.locator('.status').first()).toHaveText('paused');
  source={...source,status:'ready',chunk_count:2,media_control:'resume'};
  await expect(row.locator('.status')).toHaveText('ready',{timeout:3500});await expect(dialog.locator('.status').first()).toHaveText('ready');
  await expect(dialog.locator('.knowledge-reader-text')).toHaveText('Newly indexed final passage.');expect(detailReads).toBeGreaterThan(1);
 }finally{release();}
});

test('resource polling reports a failed refresh, keeps saved state and recovers automatically',async({page})=>{
 await chatFixture(page);let fail=false;let status='queued';let concurrent=0;let peak=0;
 await page.route('http://127.0.0.1:8765/rpc',async route=>{
  if(route.request().method()==='OPTIONS')return route.fallback();const {id,method}=route.request().postDataJSON();
  if(method!=='sources.list')return route.fallback();concurrent++;peak=Math.max(peak,concurrent);
  try{
   await new Promise(resolve=>setTimeout(resolve,30));
   if(fail)return await route.fulfill({status:503,headers:{'Access-Control-Allow-Origin':'*'}});
   await route.fulfill({headers:{'Access-Control-Allow-Origin':'*'},json:{id,result:[{id:'day1',title:'Day 1 recording',filename:'day1.pdf',status}]}});
  }finally{concurrent--;}
 });
 await page.goto('/');await page.getByRole('button',{name:'Library',exact:true}).click();
 const row=page.getByRole('row').filter({has:page.getByRole('button',{name:'Day 1 recording',exact:true})});await expect(row).toBeVisible();
 fail=true;await page.evaluate(()=>window.dispatchEvent(new Event('bughunter-sources-changed')));
 await expect(page.getByText('Live resource updates interrupted.',{exact:false})).toBeVisible();await expect(row.locator('.status')).toHaveText('queued');
 fail=false;status='processing';
 await expect(row.locator('.status')).toHaveText('processing',{timeout:3500});await expect(page.getByText('Live resource updates interrupted.',{exact:false})).toHaveCount(0);expect(peak).toBe(1);
});
