import {test,expect} from '@playwright/test';
import {chatFixture} from './chat-fixture';

test('task results stay available and a browser workspace can be viewed separately from Chat',async({page})=>{
 await chatFixture(page);await page.route('http://127.0.0.1:8765/rpc',async route=>{
  if(route.request().method()==='OPTIONS')return route.fallback();const {id,method}=route.request().postDataJSON();let result:any;
  if(method==='harness.list')result=[{id:'harness_browser',kind:'harness',chat_id:'chat_browser',title:'Build the chatbot notebook',status:'awaiting_user',model:'gpt-6.1-sol',background:true,browser:{state:'open',title:'Workspace'}}];
  else if(method==='harness.browser.view')result={state:'open',title:'Workspace',url:'http://localhost/fixture',width:1280,height:800,image_url:'data:image/png;base64,iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+jhWQAAAAASUVORK5CYII=',cursor:{x:200,y:100,label:'GPT-6.1 Sol #1'}};
  else return route.fallback();await route.fulfill({headers:{'Access-Control-Allow-Origin':'*'},json:{id,result}});
 });
 await page.goto('/');await page.getByRole('button',{name:'Background tasks',exact:true}).click();const panel=page.getByRole('region',{name:'Background tasks',exact:true});await expect(panel).toContainText('Reply available');await panel.getByRole('button',{name:'View browser',exact:true}).click();const browser=page.getByRole('region',{name:'Agent browser workspace'});await expect(browser.getByRole('img',{name:'Live background browser page'})).toBeVisible();await expect(browser).toContainText('GPT-6.1 Sol #1');await expect(browser).toContainText('your keyboard and mouse stay free');await browser.getByRole('button',{name:'Close browser preview'}).click();await expect(browser).toHaveCount(0);
});
