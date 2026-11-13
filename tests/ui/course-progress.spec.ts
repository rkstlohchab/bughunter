import { test, expect } from '@playwright/test';
import { chatFixture } from './chat-fixture';

test('Library distinguishes saved audio progress from frame analysis', async ({ page }) => {
  await chatFixture(page);
  let phase = 'speech';
  let status = 'processing';
  let analyzed = 3, total = 55, completed: number | undefined;
  let gaps: unknown[] = [];
  await page.route('http://127.0.0.1:8765/rpc', async route => {
    if (route.request().method() === 'OPTIONS') return route.fallback();
    const request = route.request().postDataJSON();
    if (request.method !== 'sources.list') return route.fallback();
    return route.fulfill({ headers: { 'Access-Control-Allow-Origin': '*' }, json: { id: request.id, result: [
      { id: 'course-video', title: 'Course recording', filename: 'course.mp4', status,
        media_progress: { phase, analyzed, total, completed, gaps, timestamp: 1800 } },
    ] } });
  });
  await page.goto('/');
  await page.getByRole('button', { name: 'Library', exact: true }).click();
  await expect(page.getByText('Transcribing audio: 3 / 55 parts')).toBeVisible();
  await expect(page.getByRole('progressbar', { name: 'Transcribing audio' })).toHaveAttribute('value', '3');
  phase = 'scene_scan'; total = 0; analyzed = 0;
  await page.getByRole('button', { name: 'Refresh workspace', exact: true }).click();
  await expect(page.getByText('Finding screen changes…')).toBeVisible();
  await expect(page.getByRole('progressbar', { name: 'Finding screen changes' })).not.toHaveAttribute('value');
  total = 7; analyzed = 3; completed = 2; gaps = [{start:600,end:1200}];
  await page.getByRole('button', { name: 'Refresh workspace', exact: true }).click();
  await expect(page.getByText('Finding screen changes: 3 / 7 windows')).toBeVisible();
  await expect(page.getByRole('progressbar', { name: 'Finding screen changes' })).toHaveAttribute('value','3');
  await expect(page.getByRole('progressbar', { name: 'Finding screen changes' })).toHaveAttribute('max','7');
  await expect(page.getByText('2 windows completed successfully.')).toBeVisible();
  await expect(page.getByText('1 screen-change scan gaps need attention.')).toBeVisible();
  // A new renderer instance reads persisted scan progress rather than resetting it.
  await page.reload();
  await page.getByRole('button', { name: 'Library', exact: true }).click();
  await expect(page.getByText('Finding screen changes: 3 / 7 windows')).toBeVisible();
  await expect(page.getByRole('progressbar', { name: 'Finding screen changes' })).toHaveAttribute('value','3');
  await expect(page.getByText('1 screen-change scan gaps need attention.')).toBeVisible();
  analyzed = 7; completed = 7; gaps = [];
  await page.getByRole('button', { name: 'Refresh workspace', exact: true }).click();
  await expect(page.getByText('Finding screen changes: 7 / 7 windows')).toBeVisible();
  await expect(page.getByText('1 screen-change scan gaps need attention.')).toHaveCount(0);
  phase = 'frames'; analyzed = 3; total = 55;
  await page.getByRole('button', { name: 'Refresh workspace', exact: true }).click();
  await expect(page.getByText('Reading video frames: 3 / 55 frames')).toBeVisible();
  status = 'ready';
  await page.getByRole('button', { name: 'Refresh workspace', exact: true }).click();
  await expect(page.getByRole('progressbar')).toHaveCount(0);
});

test('Library explains a persisted provider cooldown and hides it after Stop all',async({page})=>{
  await chatFixture(page);
  let status='queued';
  await page.route('http://127.0.0.1:8765/rpc',async route=>{
    if(route.request().method()==='OPTIONS')return route.fallback();
    const {id,method}=route.request().postDataJSON();
    if(method==='system.stop'){status='cancelled';return route.fallback();}
    if(method!=='sources.list')return route.fallback();
    await route.fulfill({headers:{'Access-Control-Allow-Origin':'*'},json:{id,result:[{
      id:'limited-recording',title:'Limited recording',filename:'course.mp4',status,
      media_progress:{phase:'provider_wait',analyzed:200,total:963,timestamp:5490,
        retry_provider:'ChatGPT',retry_at:Date.now()/1000+3600},
    }]}});
  });
  await page.goto('/');
  await page.getByRole('button',{name:'Library',exact:true}).click();
  await expect(page.getByText('Waiting for ChatGPT usage limit',{exact:true})).toBeVisible();
  await expect(page.getByText('200 / 963 frames saved.',{exact:false})).toBeVisible();
  await expect(page.getByText('Automatic retry:',{exact:false})).toBeVisible();
  await page.reload();
  await page.getByRole('button',{name:'Library',exact:true}).click();
  await expect(page.getByText('Waiting for ChatGPT usage limit',{exact:true})).toBeVisible();
  await page.getByRole('button',{name:'Stop all',exact:true}).click();
  await expect(page.getByText('Waiting for ChatGPT usage limit',{exact:true})).toHaveCount(0);
});

test('opening an imported recording shows saved transcript and frame observations in its timeline',async({page})=>{
  await chatFixture(page);
  const calls:{method:string;params:any}[]=[];
  const source={id:'recording',title:'Imported recording',filename:'recording.mp4',media_type:'video/mp4',status:'ready'};
  await page.route('http://127.0.0.1:8765/rpc',async route=>{
    if(route.request().method()==='OPTIONS')return route.fallback();
    const {id,method,params={}}=route.request().postDataJSON();calls.push({method,params});
    let result:any;
    if(method==='sources.list')result=[source];
    else if(method==='sources.get')result={...source,chunks:[{id:'speech-one',text:'Saved transcript passage.'}]};
    else if(method==='media.timeline')result={source_id:source.id,status:'ready',items:[
      {start:15,end:25,modality:'speech',text:'The narrator opens the request editor.'},
      {start:24,modality:'vision',text:'Visible request editor with a selected request.'},
    ]};
    else if(method==='media.status')result={config:{},vision_ready:false,speech_model:false,setup:{state:'idle'}};
    else return route.fallback();
    await route.fulfill({headers:{'Access-Control-Allow-Origin':'*'},json:{id,result}});
  });
  await page.goto('/');
  await page.getByRole('button',{name:'Library',exact:true}).click();
  await page.getByRole('button',{name:'Imported recording',exact:true}).click();
  const dialog=page.getByRole('dialog');
  await expect(dialog.getByText('The narrator opens the request editor.')).toBeVisible();
  await expect(dialog.getByText('Visible request editor with a selected request.')).toBeVisible();
  await expect(dialog.getByText('00:00:15 – 00:00:25')).toBeVisible();
  expect(calls.find(call=>call.method==='media.timeline')!.params).toEqual({source_id:'recording'});
  await page.screenshot({path:'artifacts/complete-learning-2026-11-12/ui/library-timeline.png',fullPage:true});
  await dialog.getByRole('button',{name:'Open Settings',exact:true}).click();
  await expect(page.getByRole('heading',{name:'Video AI setup',exact:true})).toBeVisible();
});

for (const includeActive of [true,false]) {
  test(`withdrawn descriptions stay in audit history with ${includeActive?'an active passage':'no active passages'}`,async({page})=>{
    await chatFixture(page);
    const source={id:'reviewed-course',title:'Reviewed course',filename:'lesson.txt',status:'ready'};
    await page.route('http://127.0.0.1:8765/rpc',async route=>{
      if(route.request().method()==='OPTIONS')return route.fallback();
      const {id,method}=route.request().postDataJSON();
      let result:any;
      if(method==='sources.list')result=[source];
      else if(method==='sources.get')result={...source,chunks:[
        ...(includeActive?[{id:'active-speech',locator:'00:00:10 speech',text:'The narrator opens the request editor.',active:true}]:[]),
        {id:'rejected-vision',locator:'00:00:12 vision',text:'Incorrect withdrawn description.',active:false,review_status:'rejected',review_reason:'Contradicted by the captured frame.'},
        {id:'superseded-vision',locator:'00:00:15 vision',text:'Superseded visual description.',review_status:'superseded',review_reason:'Replaced by contextual reanalysis.'},
      ]};
      else return route.fallback();
      await route.fulfill({headers:{'Access-Control-Allow-Origin':'*'},json:{id,result}});
    });
    await page.goto('/');
    await page.getByRole('button',{name:'Library',exact:true}).click();
    await page.getByRole('button',{name:'Reviewed course',exact:true}).click();
    const detail=page.getByRole('dialog',{name:'Reviewed course',exact:true});
    const audit=detail.locator('.withdrawn-descriptions');
    await expect(audit).not.toHaveAttribute('open');
    await expect(detail.getByText('Incorrect withdrawn description.',{exact:true})).not.toBeVisible();
    if(includeActive){
      await expect(detail.locator('.knowledge-reader-text')).not.toContainText('Incorrect withdrawn description.');
      await expect(detail.locator('.knowledge-reader-edits')).not.toContainText('Superseded visual description.');
      await expect(detail.locator('.knowledge-reader-text')).toHaveText('The narrator opens the request editor.');
      await detail.getByRole('button',{name:'Create training example',exact:true}).click();
      const training=page.getByRole('dialog',{name:'Curate a training example',exact:true});
      await expect(training.getByRole('combobox',{name:'Supporting passage',exact:true}).locator('option')).toHaveCount(1);
      await expect(training.getByRole('combobox',{name:'Supporting passage',exact:true})).toHaveValue('active-speech');
      await expect(training).not.toContainText('Incorrect withdrawn description.');
      await training.getByRole('button',{name:'Close dialog',exact:true}).click();
    }else{
      await expect(detail.locator('.knowledge-reader-text')).toHaveCount(0);
      await expect(detail.locator('.knowledge-reader-edits')).toHaveCount(0);
      await expect(detail.getByRole('button',{name:'Create training example',exact:true})).toHaveCount(0);
      await expect(detail.getByRole('button',{name:'Study this course',exact:true})).toHaveCount(0);
      await expect(detail.getByRole('button',{name:'Correct this part',exact:true})).toHaveCount(0);
    }
    await audit.getByText('Withdrawn descriptions (audit history)',{exact:false}).click();
    await expect(audit.getByText('Incorrect withdrawn description.',{exact:true})).toBeVisible();
    await expect(audit.getByText('Review status: rejected',{exact:true})).toBeVisible();
    await expect(audit.getByText('Reason: Contradicted by the captured frame.',{exact:true})).toBeVisible();
    await page.screenshot({path:`artifacts/complete-learning-2026-11-12/ui/withdrawn-followup/audit-${includeActive?'mixed':'withdrawn-only'}.png`,fullPage:true});
    await expect(audit.getByText('These descriptions are excluded from search, study, correction, and training examples.',{exact:false})).toBeVisible();
  });
}
