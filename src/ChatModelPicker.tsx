import { useEffect, useRef, useState } from 'react';
import type { KeyboardEvent } from 'react';
import { ArrowLeft, Check, ChevronDown, ChevronRight, Sparkles } from 'lucide-react';
import { AI_CATEGORIES, categoryFor } from './aiProviders';
import type { Category } from './aiProviders';
export type ProviderInfo = {provider: string; label: string; kind: string; ready: boolean; needs: string; models: string[]; active: boolean};

type Props = {providers: ProviderInfo[]; active: {provider: string; model: string}; onPick: (provider: string, model: string) => void; onConfigure: () => void};

export default function ChatModelPicker({providers, active, onPick, onConfigure}: Props) {
  const [open, setOpen] = useState(false);
  const [category, setCategory] = useState<Category | null>(null);
  const trigger = useRef<HTMLButtonElement>(null);
  const root = useRef<HTMLDivElement>(null);
  const submenu = useRef<HTMLDivElement>(null);
  const categoryButtons = useRef<Partial<Record<Category, HTMLButtonElement | null>>>({});
  const focusSubmenu = useRef(false);
  const label = providers.find(p => p.provider === active.provider)?.label || 'Choose a model';
  const close = () => { setOpen(false); setCategory(null); trigger.current?.focus(); };
  useEffect(() => {
    if (open) categoryButtons.current[categoryFor(active.provider)]?.focus();
  }, [open]);
  useEffect(() => {
    if (category && focusSubmenu.current) {
      (submenu.current?.querySelector<HTMLElement>('[aria-checked="true"]') || submenu.current?.querySelector<HTMLElement>('[role^="menuitem"]'))?.focus();
      focusSubmenu.current = false;
    }
  }, [category]);
  function expand(id: Category, focus: boolean) {
    focusSubmenu.current = focus;
    if (id === category && focus) submenu.current?.querySelector<HTMLElement>('[role^="menuitem"]')?.focus();
    else setCategory(id);
  }
  function keys(event: KeyboardEvent<HTMLDivElement>) {
    const element = event.target as HTMLElement;
    if (event.key === 'Escape') { event.preventDefault(); event.stopPropagation(); close(); return; }
    const inSubmenu = !!submenu.current?.contains(element);
    if (event.key === 'ArrowLeft' && inSubmenu) {
      event.preventDefault(); categoryButtons.current[category!]?.focus(); setCategory(null); return;
    }
    if (event.key === 'ArrowRight' && !inSubmenu) {
      const id = element.dataset.category as Category;
      if (id) { event.preventDefault(); expand(id, true); }
      return;
    }
    if (!['ArrowDown', 'ArrowUp', 'Home', 'End'].includes(event.key)) return;
    const menu = inSubmenu ? submenu.current : root.current?.querySelector('[aria-label="AI connection types"]');
    const items = Array.from(menu?.querySelectorAll<HTMLElement>('[role^="menuitem"]') || []);
    if (!items.length) return;
    event.preventDefault();
    const index = items.indexOf(element);
    const next = event.key === 'Home' ? 0 : event.key === 'End' ? items.length - 1 : (index + (event.key === 'ArrowDown' ? 1 : -1) + items.length) % items.length;
    items[next].focus();
  }
  const selectedCategory = AI_CATEGORIES.find(c => c.id === category);
  const shown = providers.filter(p => categoryFor(p.provider) === category);
  return <div className="model-picker" ref={root} onBlur={event => {
    if (!event.currentTarget.contains(event.relatedTarget as Node | null)) {setOpen(false); setCategory(null);}
  }}>
    <button ref={trigger} type="button" className="model-picker-btn" aria-label="Choose a model" title={`${label} · ${active.model}`} aria-haspopup="menu" aria-expanded={open} aria-controls={open ? 'chat-model-menu' : undefined} onClick={() => {setOpen(!open);setCategory(null);}}>
      <Sparkles size={16}/><span><strong>{label}</strong>{active.model && <em>{active.model}</em>}</span><ChevronDown size={16}/>
    </button>
    {open && <><div className="model-picker-backdrop" onMouseDown={event => event.preventDefault()} onClick={close}/>
      <div id="chat-model-menu" className={`model-picker-menu grouped-model-menu ${category ? 'expanded' : ''}`} onKeyDown={keys}>
        <div role="menu" aria-label="AI connection types" className="model-category-list">
          {AI_CATEGORIES.map(c => <button key={c.id} ref={node => {categoryButtons.current[c.id] = node;}} type="button" role="menuitem" aria-haspopup="menu" aria-expanded={category === c.id} aria-controls={category === c.id ? 'chat-model-submenu' : undefined} data-category={c.id} className={`model-category ${category === c.id ? 'active' : ''}`} onMouseEnter={() => expand(c.id, false)} onClick={() => expand(c.id, true)}>
            <span><strong>{c.label}</strong><small>{c.detail}</small></span><ChevronRight size={16}/>
          </button>)}
        </div>
        {selectedCategory && <div id="chat-model-submenu" ref={submenu} role="menu" aria-label={`${selectedCategory.label} models`} className="model-submenu">
          <div className="model-submenu-title"><button type="button" role="menuitem" aria-label="Back to connection types" onClick={() => {categoryButtons.current[category!]?.focus();setCategory(null);}}><ArrowLeft size={15}/></button><strong>{selectedCategory.label}</strong></div>
          {shown.map(p => <div className="model-group" key={p.provider}>
            <div className="model-group-head">{p.label}</div>
            {p.ready && p.models.length > 0 ? p.models.map(m => <button key={m} type="button" role="menuitemradio" aria-checked={active.provider === p.provider && active.model === m} className={`model-opt ${active.provider === p.provider && active.model === m ? 'on' : ''}`} onClick={() => {onPick(p.provider, m);close();}}><span>{m}</span>{active.provider === p.provider && active.model === m && <Check size={15}/>}</button>) : <>
              <p className="model-need">{p.needs || 'Choose a model in Settings.'}</p><button type="button" role="menuitem" className="model-opt" onClick={() => {close();onConfigure();}}>Configure {p.label}</button>
            </>}
          </div>)}
          {!shown.length && <button type="button" role="menuitem" className="model-opt" onClick={() => {close();onConfigure();}}>Connect a model in Settings</button>}
        </div>}
      </div></>}
  </div>;
}
