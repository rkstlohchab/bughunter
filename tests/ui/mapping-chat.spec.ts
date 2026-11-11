import { expect, test, type Page } from '@playwright/test';

async function fixture(page:Page, options:{attached?:boolean;approval?:boolean;fail?:boolean;selectFailure?:boolean;deferSelection?:boolean}={}) {
  await page.addInitScript((options)=>{
    const w=window as any;
    w.__mappingCalls=[];
    let state:any=JSON.parse(localStorage.getItem('chat-mapping-fixture')||'null');
    const program={pid:42,process_instance_id:'42:fixture:one',program:'fixture.bin',build_sha256:'a'.repeat(64)};
    const ref={id:'ref',title:'Fixture score reference',url:'https://example.org/fixture',quote:'fixture.bin stores the score at 0x100.'};
    let receipts=new Set<string>();
    const save=()=>localStorage.setItem('chat-mapping-fixture',JSON.stringify(state));
    const message=(content:string,citations?:any[])=>state.session.messages.push({role:'assistant',content,citations});
    const reply=()=>JSON.parse(JSON.stringify({...state,next:state.workflow.status==='checking'?{kind:'check',check_id:state.workflow.check_id,request:{pid:42,process_instance_id:program.process_instance_id,expected_value:state.value}}:null}));
    w.__TAURI_INTERNALS__={transformCallback:()=>0,invoke:async(command:string,args:any)=>{
      w.__mappingCalls.push({command,args});
      if(command==='ce_prepare_mapping_chat') return {processes:[program],context:options.attached?program:null};
      if(command==='ce_select_mapping_program') {
        if(options.deferSelection) await new Promise<void>(resolve=>{w.__finishSelection=resolve;});
        if(options.selectFailure) throw 'error: task_for_pid(42) failed: KERN_FAILURE(5). The target may be hardened or the helper may lack debugger authorization.';
        return program;
      }
      if(command==='ce_check_mapping') {
        if(options.fail) throw new Error('The selected program has exited. Choose it again.');
        if(options.approval && args.automatic) return {check_id:args.checkId,approval_required:true,request:{pid:42,expected_value:state.value}};
        receipts.add(args.checkId);return {check_id:args.checkId,saved:true};
      }
      if(command!=='rpc') return {};
      const p=args.params||{};
      switch(args.method) {
        case 'system.status': return {emergency_stop:false};
        case 'models.available': return {providers:[],active:{model:'fixture'}};
        case 'chat.list': return state?[state.session]:[];
        case 'chat.get': return state.session;
        case 'chat.mapping.start':
          state={session:{id:'chat_mapping',project_id:null,title:p.message,mapping_id:'mapping_chat',messages:[{role:'user',content:p.message}]},
            workflow:{id:'mapping_chat',chat_id:'chat_mapping',status:'needs_program',query:p.message,context:null,round:0,events:[],progress:'Choose the program.'},research:null};
          message('I’ll research references, check candidates and report here.');save();return reply();
        case 'chat.mapping.select':
          state.workflow={...state.workflow,status:'researching',context:p.context,progress:'Researching cited references.'};
          state.research={id:'research',references:[ref],candidates:[{id:'candidate',label:'Score',status:'proposed',observations:[]}],verified_count:0,events:[],warnings:[]};
          save();return reply();
        case 'chat.mapping.advance':
          if(state.workflow.status==='researching') {state.workflow.status='needs_value';state.workflow.progress='Waiting for the visible value.';message('What numeric value is currently visible?');}
          if(state.workflow.status==='checking' && receipts.has(state.workflow.check_id)) {
            state.research.candidates[0].observations.push({check_id:state.workflow.check_id,value:Number(state.value),expected:Number(state.value),matched:true});
            state.workflow.check_id=null;
            if(state.workflow.round>=2) {
              state.workflow.status='completed';state.workflow.progress='Cited evidence saved.';state.research.verified_count=1;state.research.candidates[0].status='value_verified';
              message('1 verified value mapping saved. Score matched 100 and 90 in the selected process. [1] Fixture score reference',[ref]);
            } else {state.workflow.status='needs_value';state.workflow.progress='Waiting for a different value.';message('Readbacks saved. Change the visible value and tell me the new number.');}
          }
          save();return reply();
        case 'chat.mapping.reply':
          state.session.messages.push({role:'user',content:p.message});
          if(p.message==='choose program') {state.workflow.status='needs_program';state.workflow.requires_selection=true;}
          else if(/^stop|cancel$/i.test(p.message)) {state.workflow.status='cancelled';state.workflow.progress='Stopped.';message('Stopped; evidence is saved.');}
          else {state.value=p.message;state.workflow.round++;state.workflow.check_id='mapping_check_'+String(state.workflow.round).repeat(32);state.workflow.status='checking';state.workflow.progress='Checking cited candidates.';}
          save();return reply();
        case 'chat.mapping.pause':state.workflow.status='blocked';state.workflow.progress=p.reason;message(p.reason+' Reply retry.');save();return reply();
        case 'chat.mapping.stop':state.workflow.status='cancelled';state.workflow.progress='Stopped.';message('Stopped; evidence is saved.');save();return reply();
        case 'memory.mappings.export':return {filename:'mapping-evidence.json',content:JSON.stringify(state)};
        case 'chat.export':return {filename:'conversation.json',content:JSON.stringify(state)};
        default:return args.method.endsWith('.list')?[]:{};
      }
    }};
  },options);
}
async function send(page:Page,message:string) {
  await page.getByRole('textbox',{name:'Message',exact:true}).fill(message);
  await page.getByRole('button',{name:'Send',exact:true}).click();
}
const activity=(page:Page)=>page.getByRole('region',{name:'Mapping task activity'});

test('chat researches, checks automatically, reports citations and restores the outcome',async({page})=>{
  await fixture(page,{attached:true});await page.goto('/');
  await send(page,'Research memory mappings for score in the selected program');
  await expect(page.getByText('What numeric value is currently visible?',{exact:true})).toBeVisible();
  await send(page,'100');
  await expect(page.getByText('Readbacks saved. Change the visible value and tell me the new number.',{exact:true})).toBeVisible();
  await send(page,'90');
  await expect(page.getByText(/1 verified value mapping saved/)).toBeVisible();
  await expect(page.getByRole('link',{name:'[1] Fixture score reference',exact:true})).toHaveAttribute('href','https://example.org/fixture');
  let calls=await page.evaluate(()=>(window as any).__mappingCalls);
  expect(calls.filter((c:any)=>c.command==='ce_check_mapping')).toHaveLength(2);
  expect(calls.filter((c:any)=>c.command==='ce_check_mapping').every((c:any)=>c.args.automatic===true)).toBe(true);
  expect(calls.some((c:any)=>['ce_select_mapping_program','ce_run_agent_prompt'].includes(c.command))).toBe(false);
  expect(calls.find((c:any)=>c.args?.method==='chat.mapping.start').args.params.search_web).toBe(false);
  const downloaded=page.waitForEvent('download');
  await activity(page).getByRole('button',{name:'Download mapping evidence'}).click();
  expect((await downloaded).suggestedFilename()).toBe('mapping-evidence.json');
  await page.reload();
  await expect(page.getByText(/1 verified value mapping saved/)).toBeVisible();
  calls=await page.evaluate(()=>(window as any).__mappingCalls);
  expect(calls.some((c:any)=>c.command==='ce_check_mapping')).toBe(false);
  await page.screenshot({path:'/tmp/bughunter-chat-mapping-complete.png',fullPage:true});
});

test('program selection happens in chat and never attaches before selection',async({page})=>{
  await fixture(page);await page.goto('/');
  await send(page,'Find mappings for the score in my program');
  await expect(activity(page).getByRole('button',{name:'Use selected program'})).toBeDisabled();
  let calls=await page.evaluate(()=>(window as any).__mappingCalls);
  expect(calls.some((c:any)=>c.command==='ce_select_mapping_program')).toBe(false);
  await activity(page).getByRole('combobox',{name:'Program',exact:true}).selectOption('42');
  await activity(page).getByRole('button',{name:'Use selected program'}).click();
  await expect(page.getByText('What numeric value is currently visible?',{exact:true})).toBeVisible();
  calls=await page.evaluate(()=>(window as any).__mappingCalls);
  expect(calls.find((c:any)=>c.command==='ce_select_mapping_program').args).toEqual({pid:42,processInstanceId:'42:fixture:one'});
});

test('review setting requires exact inline approval and stop clears queued checks',async({page})=>{
  await fixture(page,{attached:true,approval:true});await page.goto('/');
  await send(page,'Research memory mappings for the selected program');
  await expect(page.getByText('What numeric value is currently visible?',{exact:true})).toBeVisible();
  await send(page,'100');
  const approval=page.getByRole('alertdialog',{name:'Approve mapping check'});
  await expect(approval).toContainText('100');
  let calls=await page.evaluate(()=>(window as any).__mappingCalls);
  expect(calls.filter((c:any)=>c.command==='ce_check_mapping' && !c.args.automatic)).toHaveLength(0);
  await approval.getByRole('button',{name:'Approve this check'}).click();
  await expect(page.getByText('Readbacks saved. Change the visible value and tell me the new number.',{exact:true})).toBeVisible();
  await send(page,'90');await expect(approval).toBeVisible();
  await send(page,'stop');await expect(approval).not.toBeVisible();
  await expect(activity(page)).toContainText('cancelled');
  calls=await page.evaluate(()=>(window as any).__mappingCalls);
  expect(calls.filter((c:any)=>c.command==='ce_check_mapping' && !c.args.automatic)).toHaveLength(1);
});

test('failed native check stays unverified and permits program reselection on mobile',async({page})=>{
  await fixture(page,{attached:true,fail:true});await page.setViewportSize({width:390,height:844});await page.goto('/');
  await send(page,'Research memory mappings for the selected program');
  await expect(page.getByText('What numeric value is currently visible?',{exact:true})).toBeVisible();
  await send(page,'100');await expect(activity(page)).toContainText('The selected program has exited');
  await expect(activity(page)).toContainText('0 verified');
  await activity(page).getByRole('button',{name:'Choose program again'}).click();
  await expect(activity(page).getByRole('button',{name:'Use selected program'})).toBeVisible();
  expect(await page.evaluate(()=>document.documentElement.scrollWidth<=window.innerWidth)).toBe(true);
  await page.screenshot({path:'/tmp/bughunter-chat-mapping-mobile.png',fullPage:true});
});


test('attachment denial remains visible after refresh and conversation reload',async({page})=>{
  await fixture(page,{selectFailure:true});await page.goto('/');
  await send(page,'Research memory mappings for my program');
  await activity(page).getByRole('combobox',{name:'Program',exact:true}).selectOption('42');
  await activity(page).getByRole('button',{name:'Use selected program'}).click();
  await expect(activity(page)).toContainText('macOS refused memory access');
  await expect(activity(page)).toContainText('blocked');
  await expect(activity(page).getByRole('button',{name:'Choose program again'})).toBeVisible();
  await page.reload();
  await expect(activity(page)).toContainText('macOS refused memory access');
  const calls=await page.evaluate(()=>(window as any).__mappingCalls);
  expect(calls.some((c:any)=>['ce_select_mapping_program','ce_check_mapping'].includes(c.command))).toBe(false);
  await activity(page).getByRole('button',{name:'Choose program again'}).click();
  await expect(activity(page).getByRole('combobox',{name:'Program',exact:true})).toBeVisible();
});

test('attachment has visible progress and prevents duplicate selections',async({page})=>{
  await fixture(page,{deferSelection:true});await page.goto('/');
  await send(page,'Research memory mappings for my program');
  await activity(page).getByRole('combobox',{name:'Program',exact:true}).selectOption('42');
  await activity(page).getByRole('button',{name:'Use selected program'}).click();
  await expect(activity(page).getByRole('status').filter({hasText:'Connecting to fixture.bin'})).toBeVisible();
  await expect(activity(page).getByRole('combobox',{name:'Program',exact:true})).toBeDisabled();
  await expect(activity(page).getByRole('button',{name:'Connecting…'})).toBeDisabled();
  await page.evaluate(()=>(window as any).__finishSelection());
  await expect(page.getByText('What numeric value is currently visible?',{exact:true})).toBeVisible();
  const calls=await page.evaluate(()=>(window as any).__mappingCalls);
  expect(calls.filter((c:any)=>c.command==='ce_select_mapping_program')).toHaveLength(1);
});
