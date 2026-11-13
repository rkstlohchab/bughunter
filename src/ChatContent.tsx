import {Children,isValidElement,memo,useEffect,useRef,useState} from 'react';
import type {ReactNode} from 'react';
import Markdown from 'react-markdown';
import remarkGfm from 'remark-gfm';
import remarkMath from 'remark-math';
import rehypeHighlight from 'rehype-highlight';
import rehypeKatex from 'rehype-katex';
import {Check,Copy} from 'lucide-react';
import 'katex/dist/katex.min.css';
import TeachingDiagram from './TeachingDiagram';
function plainText(node:ReactNode):string{return Children.toArray(node).map(child=>typeof child==='string'||typeof child==='number'?String(child):isValidElement<{children?:ReactNode}>(child)?plainText(child.props.children):'').join('');}

function CodeBlock({children}:{children:ReactNode}){
 const code=useRef<HTMLPreElement>(null);const [copied,setCopied]=useState(false);const [error,setError]=useState(false);
 const timer=useRef<ReturnType<typeof setTimeout>|undefined>(undefined);
 useEffect(()=>()=>clearTimeout(timer.current),[]);
 const child=Children.toArray(children).find(isValidElement<{className?:string}>);
 const language=child&&isValidElement<{className?:string}>(child)?/language-([^\s]+)/.exec(child.props.className||'')?.[1]:undefined;
 async function copy(){try{await navigator.clipboard.writeText(code.current?.textContent||'');setCopied(true);setError(false);clearTimeout(timer.current);timer.current=setTimeout(()=>setCopied(false),1800);}catch{setError(true);}}
 if(language==='mermaid')return <TeachingDiagram source={plainText(child)}/>;
 return <div className="chat-code-block"><div className="chat-code-toolbar"><span>{language||'Code'}</span><button type="button" onClick={()=>void copy()} aria-label="Copy code">{copied?<Check size={14}/>:<Copy size={14}/>} {copied?'Copied':error?'Select code to copy':'Copy'}</button></div><pre ref={code} tabIndex={0}>{children}</pre></div>;
}
function highlights(){
 return (tree:any)=>{
  function visit(node:any){
   if(!Array.isArray(node.children)||['code','inlineCode','html'].includes(node.type))return;
   node.children=node.children.flatMap((child:any)=>{
    if(child.type!=='text'){visit(child);return [child];}
    const value=String(child.value);const parts:any[]=[];let cursor=0;
    for(const match of value.matchAll(/==([^=\n]+)==/g)){
     if(match.index!>cursor)parts.push({type:'text',value:value.slice(cursor,match.index)});
     parts.push({type:'highlight',data:{hName:'mark'},children:[{type:'text',value:match[1]}]});cursor=match.index!+match[0].length;
    }
    if(!parts.length)return [child];
    if(cursor<value.length)parts.push({type:'text',value:value.slice(cursor)});return parts;
   });
  }
  visit(tree);
 };
}
const components={
 pre:({children}:{children?:ReactNode})=><CodeBlock>{children}</CodeBlock>,
 table:({children}:{children?:ReactNode})=><div className="chat-table-scroll" tabIndex={0}><table>{children}</table></div>,
 a:({href,children}:{href?:string;children?:ReactNode})=><a href={href} target="_blank" rel="noreferrer noopener">{children}</a>,
 img:({src,alt}:{src?:string;alt?:string})=><img src={src} alt={alt||'Image in response'} loading="lazy"/>,
};
export default memo(function ChatContent({content}:{content:string}){
 return <div className="chat-markdown"><Markdown remarkPlugins={[remarkGfm,remarkMath,highlights]} rehypePlugins={[[rehypeHighlight,{detect:false,ignoreMissing:true}],[rehypeKatex,{throwOnError:false,strict:'ignore',trust:false}]]} skipHtml components={components} urlTransform={url=>/^(?:https?:|mailto:|#)/i.test(url)?url:''}>{content}</Markdown></div>;
});
