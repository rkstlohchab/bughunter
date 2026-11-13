import {test} from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';
import ts from 'typescript';
const source=fs.readFileSync(new URL('../../src/VoiceTranscript.ts',import.meta.url),'utf8');
const compiled=ts.transpileModule(source,{compilerOptions:{module:ts.ModuleKind.ES2022,target:ts.ScriptTarget.ES2022}}).outputText;
const {DictationDraft,cleanDictation,isSendCommand}=await import('data:text/javascript;base64,'+Buffer.from(compiled).toString('base64'));

test('partials replace the same utterance in real time without duplicated finals',()=>{
 const d=new DictationDraft('Existing draft');assert.equal(d.accept(1,'Um, open').text,'Existing draft open');assert.equal(d.accept(1,'Um, open Library').text,'Existing draft open Library');assert.equal(d.accept(1,'Open Library.',true).text,'Existing draft Open Library.');assert.equal(d.accept(1,'Open Library.',true).changed,false);assert.equal(d.accept(2,'and explain the course',true).text,'Existing draft Open Library. and explain the course');
});

test('send commands submit an existing draft without a wake phrase',()=>{
 for(const phrase of ['send','send it','send a message','send the message','please send it','Hey Bob Hunter, send a message']){
  const d=new DictationDraft('Show a diagram');const result=d.accept(1,phrase,true);assert.equal(result.text,'Show a diagram');assert.equal(result.send,true,phrase);
 }
});

test('normal requests mentioning sending remain dictation text',()=>{
 const d=new DictationDraft();assert.equal(d.accept(1,'Send a message to Alice about the meeting',true).send,false);assert.equal(isSendCommand('Explain how to send a message'),false);assert.equal(cleanDictation('hmm explain the human function'),'hmm explain the human function');
});

test('manual edits and a sent draft are retained rather than overwritten by delayed audio',()=>{
 const d=new DictationDraft();d.accept(1,'First request',true);d.edit('Manual correction');assert.equal(d.accept(2,'next sentence',true).text,'Manual correction next sentence');d.edit('');assert.equal(d.accept(3,'New request',true).text,'New request');
});

test('late final results cannot reinsert text after sending a partial draft',()=>{const d=new DictationDraft();d.accept(1,'First draft');d.edit('');assert.equal(d.accept(1,'First draft.',true).changed,false);assert.equal(d.text(),'');});

test('native 64-bit line identifiers are kept distinct',()=>{
 const d=new DictationDraft();d.accept('4875383337390624049','Explain the diagram',true);
 assert.equal(d.accept('4875383337390624050','Send it',true).send,true);
});

test('a send command joined to the dictated sentence submits only its message once',()=>{
 for(const phrase of ['Show the diagram. Send it.','Show the diagram, send the message.','Show the diagram send it']){
  const d=new DictationDraft();assert.equal(d.accept(1,phrase).send,false);
  const result=d.accept(1,phrase,true);assert.equal(result.text,'Show the diagram',phrase);assert.equal(result.send,true);assert.equal(d.accept(1,phrase,true).send,false);
 }
});

test('quoted or explanatory send phrases stay in the message',()=>{
 for(const phrase of ['Explain what happens when I say send it','Tell me whether to send it','Write the words "send it"','Send the message to Alice']){
  const d=new DictationDraft();assert.equal(d.accept(1,phrase,true).send,false,phrase);
 }
});
