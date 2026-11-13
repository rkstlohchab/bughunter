/** Reconciles replaceable partials, completed utterances and local dictation commands. */
export function cleanDictation(text:string){
 return text.replace(/^\s*(?:(?:hey|okay|ok)\s+)?(?:bug\s*hunter|bob\s*hunter)[, .:]*\s*/i,'').replace(/\b(?:uh+|um+|uhm+|erm+|ahm+)\b[, ]*/gi,'').replace(/\s+/g,' ').trim();
}
export function isSendCommand(text:string){return /^(?:please\s+)?(?:send|send it|send (?:this|the|a|my) message|send (?:the )?(?:prompt|request)|submit|submit it|submit (?:the )?(?:message|prompt|request))(?:\s+please)?[.!?]*$/i.test(text.trim());}
function splitCommand(value:string){
 if(isSendCommand(value))return {value:'',command:true};
 // STT can join a message and its command into one line, even across a pause.
 const tail=value.match(/(?:[.!?,;:…]\s*|\s+)((?:please\s+)?(?:send it|send (?:this|the|a|my) message|submit it)(?:\s+please)?[.!?]*)$/i);
 if(!tail)return {value,command:false};
 const prefix=value.slice(0,tail.index).trim();
 if(!prefix||/(?:\b(?:say|said|phrase|words|command|to)|\b(?:should|can|would|do) I)\s*$/i.test(prefix))return {value,command:false};
 return {value:prefix,command:true};
}
export class DictationDraft{
 private base:string;private lines=new Map<string,string>();private completed=new Set<string>();
 constructor(text=''){this.base=text;}
 edit(text:string){for(const id of this.lines.keys())this.completed.add(id);this.base=text;this.lines.clear();}
 text(){return [this.base,...this.lines.values()].map(value=>value.trim()).filter(Boolean).join(' ');}
 accept(id:number|string,text:string,final=false){
  const lineId=String(id);
  if(this.completed.has(lineId))return {text:this.text(),send:false,changed:false};
  const {value,command}=splitCommand(cleanDictation(text));
  if(command&&!value)this.lines.delete(lineId);else this.lines.set(lineId,value);
  if(final)this.completed.add(lineId);
  return {text:this.text(),send:final&&command,changed:true};
 }
}
