import type {Page} from '@playwright/test';
import {expect} from '@playwright/test';
export const analysisProviders=[
 {provider:'codex',label:'ChatGPT subscription',ready:true,models:['gpt-6.1-sol','gpt-5.6-luna','subscription-vision'],model_options:[{id:'gpt-6.1-sol',label:'GPT-6.1 Sol',effort:{levels:['low','medium','high','xhigh','max','ultra']}},{id:'gpt-5.6-luna',label:'GPT-5.6 Luna',effort:{levels:['low','medium','high']}},{id:'subscription-vision',label:'Available subscription model'}]},
 {provider:'claude_code',label:'Claude subscription',ready:true,models:['claude-sonnet-4-6','claude-opus-4-7'],model_options:[{id:'claude-sonnet-4-6',label:'Claude Sonnet 4.6',effort:{levels:['low','medium','high','max']}},{id:'claude-opus-4-7',label:'Claude Opus 4.7',effort:{levels:['low','medium','high','xhigh','max']}}]},
 {provider:'gemini_cli',label:'Google AI subscription',ready:true,models:['gemini-3.8-flash'],model_options:[{id:'gemini-3.8-flash',label:'Gemini 3.8 Flash',effort:{levels:['low','medium','high']}}]},
 {provider:'gemini',label:'Gemini API',ready:true,models:['gemini-3.8-flash'],model_options:[{id:'gemini-3.8-flash',label:'Gemini 3.8 Flash',effort:{levels:['low','medium','high']}}]},
];
export async function pickAnalysisModel(page:Page,name:string,category='Subscriptions'){
 const panel=page.locator('.media-ai');await panel.getByRole('button',{name:'Choose analysis model',exact:true}).click();
 await page.getByRole('menuitem',{name:category,exact:false}).click();
 const provider=category==='My own models'?'Local AI':analysisProviders.find(provider=>(category==='Paid APIs'?provider.provider==='gemini':provider.provider!=='gemini')&&provider.model_options?.some(option=>option.label===name))?.label;
 if(provider)await page.getByRole('menuitem',{name:provider,exact:true}).hover();
 await page.getByRole('menuitemradio',{name,exact:true}).click();
 await expect(panel.getByRole('button',{name:'Choose analysis model',exact:true})).toContainText(name);
}
export function localProvider(models:any[]){const vision=models.filter(model=>model.roles?.includes('vision'));return {provider:'ollama',label:'Local AI',ready:true,models:vision.map(model=>model.id),model_options:vision.map(model=>({id:model.id,label:model.label||model.name||model.id,detail:(model.installed?'Installed':'Download required')+' · '+(model.download_bytes?`${(model.download_bytes/1024**3).toFixed(1)} GiB`:'')+' '+(model.memory_note||''),effort:{levels:[]}}))};}
