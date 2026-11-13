import {test,expect} from '@playwright/test';
import {chatFixture} from './chat-fixture';

async function fixture(page:any){
 await chatFixture(page);const calls:any[]=[];const sources:any[]=[
 {id:'day1_video',title:'Day 1 video',filename:'day1.mp4',media_type:'video/mp4',status:'processing',media_control:'resume',media_progress:{phase:'frames',analyzed:250,saved_analyzed:710,total:1363,visual_context:250}},
 {id:'day2_video',title:'Day 2 video',filename:'day2.mp4',media_type:'video/mp4',status:'queued',media_control:'pause',media_progress:{phase:'frames',analyzed:917,total:963}},
 {id:'day1_pdf',title:'Bug Hunters Methodology Live Day One Recon',filename:'day1.pdf',media_type:'application/pdf',status:'partial',warnings:['Page 6 contains no extracted text.']},
 {id:'waiting_video',title:'Account-limited video',filename:'waiting.mp4',media_type:'video/mp4',status:'queued',media_control:'resume',provider_wait:{provider:'codex_subscription'},media_progress:{phase:'provider_wait',analyzed:1041,total:1363}},
 ];
 await page.route('http://127.0.0.1:8765/rpc',async(route:any)=>{
  if(route.request().method()==='OPTIONS')return route.fallback();const {id,method,params={}}=route.request().postDataJSON();let result:any;
  if(method==='sources.list')result=sources;
  else if(method==='sources.get')result={...sources.find(s=>s.id===params.id),chunks:[]};
  else if(method==='sources.control'){calls.push({method,params});const row=sources.find(s=>s.id===params.id);row.media_control=params.operation;if(row.status==='partial'&&params.operation==='resume')row.status='queued';result=row;}
  else if(method==='sources.retry'){calls.push({method,params});const row=sources.find(s=>s.id===params.id);row.provider_wait=null;row.media_progress.phase='frames';row.status='processing';result=row;}
  else return route.fallback();await route.fulfill({headers:{'Access-Control-Allow-Origin':'*'},json:{id,result}});
 });
 await page.goto('/');await page.getByRole('button',{name:'Library',exact:true}).click();return {sources,calls};
}

test('Library exposes Pause and Continue beside each file and preserves saved progress',async({page})=>{
 const {calls}=await fixture(page);const row=page.getByRole('row').filter({has:page.getByRole('button',{name:'Day 1 video',exact:true})});
 await expect(row).toContainText('710 / 1363 frames');await row.getByRole('button',{name:'Pause Day 1 video',exact:true}).click();await expect(row).toContainText('Processing paused');
 await row.getByRole('button',{name:'Continue Day 1 video',exact:true}).click();await expect(row.getByRole('button',{name:'Pause Day 1 video',exact:true})).toBeVisible();
 expect(calls.map(c=>c.params.operation)).toEqual(['pause','resume']);await expect(page.getByRole('button',{name:'Continue Day 2 video',exact:true})).toBeVisible();
});

test('Partial shows the precise missing-page warning and has a direct Continue action',async({page})=>{
 const {calls}=await fixture(page);const row=page.getByRole('row').filter({has:page.getByRole('button',{name:'Bug Hunters Methodology Live Day One Recon',exact:true})});
 await expect(row).toContainText('Page 6 contains no extracted text.');await row.getByRole('button',{name:'Continue Bug Hunters Methodology Live Day One Recon',exact:true}).click();
 expect(calls[0].params).toEqual({id:'day1_pdf',operation:'resume'});await expect(row).toContainText('queued');
});

test('Source details puts processing controls above the model and content',async({page})=>{
 await fixture(page);await page.getByRole('button',{name:'Day 2 video',exact:true}).click();const dialog=page.getByRole('dialog',{name:'Day 2 video',exact:true});
 await expect(dialog.getByRole('button',{name:'Continue Day 2 video',exact:true}).first()).toBeVisible();
});

test('A switched-account retry is available directly beside the saved frame count',async({page})=>{
 const {calls}=await fixture(page);const row=page.getByRole('row').filter({has:page.getByRole('button',{name:'Account-limited video',exact:true})});
 await expect(row).toContainText('1041 / 1363 frames saved');await row.getByRole('button',{name:'Retry now Account-limited video',exact:true}).click();
 expect(calls[0]).toEqual({method:'sources.retry',params:{id:'waiting_video'}});await expect(row).toContainText('1041 / 1363 frames');await expect(row.getByRole('button',{name:'Retry now Account-limited video',exact:true})).toBeHidden();
});
