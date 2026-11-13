import {expect,test} from '@playwright/test';
import {chatFixture} from './chat-fixture';

test('source coverage distinguishes samples, unclear intervals and untested methods',async({page})=>{
  await chatFixture(page);
  const source={id:'coverage-course',title:'Coverage course',filename:'course.mp4',status:'partial'};
  await page.route('http://127.0.0.1:8765/rpc',async route=>{
    if(route.request().method()==='OPTIONS')return route.fallback();
    const {id,method}=route.request().postDataJSON();let result:any;
    if(method==='sources.list')result=[source];
    else if(method==='sources.get')result={...source,chunks:[{id:'step-citation',text:'Open Burp and select Extensions.'}]};
    else if(method==='sources.coverage')result={kind:'video',source_status:'partial',
      audio:{completed_parts:55,total_parts:55,transcription_process_complete:true,transcript_passages:1082,unclear_total:0,unclear_passages:[]},
      visual:{plan_available:true,planned_frames:100,completed_planned_frames:80,saved_visual_observations:80,
        unreadable_total:1,unreadable_intervals:[{chunk_id:'unreadable-citation',locator:'Frame at 00:02:00',quote:'The small menu is unreadable.'}],
        maximum_sampling_spacing_seconds:30,pending_ranges:[{start:600,last_timestamp:900,frames:20}]},
      methods:{candidate_steps_total:1,candidate_steps:[{chunk_id:'step-citation',locator:'00:01:00',quote:'Open Burp and select Extensions.',basis:'narration'}],mentioned_tools:[{name:'Burp Suite'}]},
      windows:[{label:'00:00:00–00:10:00',audio:'processed',planned_frames:100,completed_frames:80,readability_issues:1,status:'partial'}],warnings:[]};
    else return route.fallback();
    await route.fulfill({headers:{'Access-Control-Allow-Origin':'*'},json:{id,result}});
  });
  await page.goto('/');await page.getByRole('button',{name:'Library',exact:true}).click();
  await page.getByRole('button',{name:'Coverage course',exact:true}).click();
  const panel=page.getByRole('region',{name:'Learning coverage'});
  await expect(panel.getByText('Audio processed: 55 / 55 parts')).toBeVisible();
  await expect(panel.getByText('Planned frames saved: 80 / 100')).toBeVisible();
  await expect(panel.getByText('Independently tested methods: none recorded')).toBeVisible();
  await panel.getByText('Intervals needing review (1)',{exact:true}).click();
  await expect(panel.getByText('The small menu is unreadable.')).toBeVisible();
  await panel.getByText('Quoted method steps (1 candidates)',{exact:true}).click();
  await expect(panel.getByText('Citation: step-citation')).toBeVisible();
  await expect(panel.getByText('narration',{exact:false})).toBeVisible();
  await expect(panel.getByText('Short actions between screenshots may be missed.',{exact:false})).toBeVisible();
});

test('an older backend response shows a clear coverage error without crashing the reader',async({page})=>{
  await chatFixture(page);
  const source={id:'old-source',title:'Old source',filename:'old.txt',status:'ready'};
  await page.route('http://127.0.0.1:8765/rpc',async route=>{
    if(route.request().method()==='OPTIONS')return route.fallback();
    const {id,method}=route.request().postDataJSON();
    if(!['sources.list','sources.get','sources.coverage'].includes(method))return route.fallback();
    const result=method==='sources.list'?[source]:method==='sources.get'?{...source,chunks:[{id:'text',text:'Saved source text.'}]}:{};
    await route.fulfill({headers:{'Access-Control-Allow-Origin':'*'},json:{id,result}});
  });
  await page.goto('/');await page.getByRole('button',{name:'Library',exact:true}).click();
  await page.getByRole('button',{name:'Old source',exact:true}).click();
  await expect(page.getByText('Coverage unavailable. Refresh after the application backend is updated.',{exact:false})).toBeVisible();
  await expect(page.getByRole('dialog').locator('.knowledge-reader-text')).toContainText('Saved source text.');
});
