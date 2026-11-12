import { useEffect, useRef } from 'react';
import type { ReactNode } from 'react';
import { Code2, FolderOpen, X } from 'lucide-react';
import type { LucideIcon } from 'lucide-react';
import { pretty } from './api';
const sentence = (text: string) => (text || '').replaceAll('_', ' ');
export function Status({ value = 'unknown' }: { value?: string }) { return <span className={`status status-${value.replaceAll('_', '-')}`}><span />{sentence(value)}</span>; }
export function Button({ children, onClick, variant = '', className = '', disabled = false, icon: Icon, type = 'button', title }: { children?: ReactNode; onClick?: () => void; variant?: string; className?: string; disabled?: boolean; icon?: LucideIcon; type?: 'button' | 'submit'; title?: string }) { return <button type={type} title={title} disabled={disabled} className={`button ${variant} ${className}`} onClick={onClick}>{Icon && <Icon size={16} aria-hidden />}{children}</button>; }
export function Field({ label, children, hint }: { label: string; children: ReactNode; hint?: string }) { return <label className="field"><span>{label}</span>{children}{hint && <small>{hint}</small>}</label>; }
export function Empty({ icon: Icon = FolderOpen, title, description, children }: { icon?: LucideIcon; title: string; description: string; children?: ReactNode }) { return <div className="empty"><div className="empty-symbol"><Icon size={29} strokeWidth={1.4} /></div><h3>{title}</h3><p>{description}</p>{children}</div>; }
export function Modal({ title, children, onClose, wide = false }: { title: string; children: ReactNode; onClose: () => void; wide?: boolean }) {
  const panel = useRef<HTMLDivElement>(null);
  const close = useRef(onClose); close.current = onClose;
  useEffect(() => { const before = document.activeElement as HTMLElement; const el = panel.current; el?.focus(); const handler = (event: KeyboardEvent) => { if (Array.from(document.querySelectorAll('[role="dialog"]')).at(-1) !== el) return; if (event.key === 'Escape') close.current(); if (event.key === 'Tab' && el) { const items = Array.from(el.querySelectorAll<HTMLElement>('button:not(:disabled), input:not(:disabled), select:not(:disabled), textarea:not(:disabled), summary, [tabindex="0"]')).filter(item => item.getClientRects().length > 0); const first = items[0], last = items.at(-1); if (event.shiftKey && (document.activeElement === first || document.activeElement === el)) { event.preventDefault(); last?.focus(); } else if (!event.shiftKey && document.activeElement === last) { event.preventDefault(); first?.focus(); } } }; window.addEventListener('keydown', handler); return () => { window.removeEventListener('keydown', handler); before?.focus(); }; }, []);
  return <div className="modal-shade" onMouseDown={e => { if (e.target === e.currentTarget) onClose(); }}><div className={`modal ${wide ? 'wide' : ''}`} role="dialog" aria-modal="true" aria-label={title} tabIndex={-1} ref={panel}><div className="modal-title"><h2>{title}</h2><button className="icon-button" aria-label="Close dialog" onClick={onClose}><X size={20} /></button></div>{children}</div></div>;
}
export function Code({ value, label }: { value: any; label?: string }) { return <div className="code-wrap">{label && <div className="code-label"><Code2 size={14} />{label}</div>}<pre>{pretty(value)}</pre></div>; }
export function DetailItem({ label, value }: { label: string; value: any }) { return value !== undefined && value !== null && value !== '' ? <div className="detail-item"><h4>{label}</h4><div>{Array.isArray(value) ? <ul>{value.map((item, i) => <li key={i}>{typeof item === 'object' ? pretty(item) : item}</li>)}</ul> : typeof value === 'object' ? <Code value={value} /> : <p>{String(value)}</p>}</div></div> : null; }

