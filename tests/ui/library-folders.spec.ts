import {test,expect} from '@playwright/test';
import {chatFixture} from './chat-fixture';

const folder=(origin:string,label:string,extra:Record<string,unknown>={})=>({origin,origin_label:{offline:'Offline: your files and courses',online:'Online: links you added',built_in:'Built-in: BugHunter reference notes'}[origin],label,...extra});

test('Library groups resources by origin and course, and Move to folder saves the choice',async({page})=>{
 await chatFixture(page);const calls:any[]=[];
 const sources=[
  {id:'d1',title:'Day 1 Recon',filename:'Day 1 Recon.mp4',status:'ready',library_folder:folder('offline','Jason Haddix › Bug Hunters Methodology',{author:'Jason Haddix',course:'Bug Hunters Methodology'})},
  {id:'v1',title:'Video 1',filename:'Video 1.mp4',status:'ready',library_folder:folder('offline','game hacking course',{course:'game hacking course',section:'videos'})},
  {id:'y1',title:'YouTube lesson abc',filename:'abc.youtube',status:'ready',library_folder:folder('online','YouTube › NahamSec',{platform:'YouTube',course:'NahamSec'})},
  {id:'r1',title:'commerce-pricing',filename:'commerce-pricing.md',status:'ready',library_folder:folder('built_in','BugHunter reference pack',{course:'BugHunter reference pack'})},
 ];
 await page.route('http://127.0.0.1:8765/rpc',async route=>{
  if(route.request().method()==='OPTIONS')return route.fallback();
  const {id,method,params}=route.request().postDataJSON();let result:any;
  if(method==='sources.list')result=sources;
  if(method==='sources.organize'){calls.push(params);result={updated:params.ids.length,sources:[]};}
  if(result===undefined)return route.fallback();
  await route.fulfill({headers:{'Access-Control-Allow-Origin':'*'},json:{id,result}});
 });
 await page.goto('/');await page.getByRole('button',{name:'Library',exact:true}).click();
 const origins=page.locator('.library-origin > summary');
 await expect(origins).toHaveText([/Offline: your files and courses · 2/,/Online: links you added · 1/,/Built-in: BugHunter reference notes · 1/]);
 await expect(page.locator('.library-folder > summary').filter({hasText:'Jason Haddix › Bug Hunters Methodology'})).toBeVisible();
 await expect(page.locator('.library-folder > summary').filter({hasText:'YouTube › NahamSec'})).toBeVisible();
 await expect(page.getByRole('row').filter({hasText:'Video 1'}).getByText('videos',{exact:true})).toBeVisible();
 await page.getByRole('checkbox',{name:'Select Video 1'}).check();
 await page.getByRole('button',{name:'Move to folder'}).click();
 const dialog=page.getByRole('dialog',{name:'Move to folder'});
 await expect(dialog.getByLabel('Course or channel')).toHaveValue('game hacking course');
 await dialog.getByLabel('Course or channel').fill('Ultimate Game Hacking Course');
 await dialog.getByLabel('Author (optional)').fill('GH Academy');
 await dialog.getByRole('button',{name:'Move',exact:true}).click();
 await expect.poll(()=>calls.length).toBe(1);
 expect(calls[0]).toEqual({ids:['v1'],author:'GH Academy',course:'Ultimate Game Hacking Course'});
});
