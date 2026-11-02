/** Small, deterministic UI router. This proposes workflows; it never executes a tool. */
export type TaskIntent = 'chat' | 'website' | 'cart' | 'memory' | 'ai_test' | 'agents' | 'research';
export function classifyTask(message: string): TaskIntent {
  const text = message.trim().toLowerCase();
  // Ordinary questions should get ordinary answers, not surprise tool launches.
  if (/^(?:how|why|what|where|when|can|could|would|should|explain|describe|tell me|is it)\b/.test(text)) return 'chat';
  if (/\b(?:deploy|delegate|launch|run|use)\b.{0,90}\b(?:subagents?|claude code|codex|coding agents?)\b/i.test(text)) return 'agents';
  // Action requests to monitor or modify values in an offline game belong to the memory workspace.
  // General explanations remain Chat; this router never executes or approves a memory write.
  if (/\b(?:find|scan|locate|inspect|resolve|track|monitor|analy[sz]e|build|create|make|generate|modify|change)\b.{0,130}\b(?:memory|pointers?|cheat engine|process|health address|health value|money value|esp overlay|entity coordinates|game health|game money)\b|\bcheat engine\b/i.test(text)) return 'memory';
  if (/\b(?:test|audit|evaluat(?:e|ion)|probe|assess|check|perform|run|start|do)\b.{0,130}\b(?:prompt injection|jailbreak|llm|ai model|model defenses?)\b|\bprompt injection (?:test|audit)\b/i.test(text)) return 'ai_test';
  if (/\b(?:audit|test|inspect|review|check|perform|run|do)\b.{0,130}\b(?:cart|checkout|coupon|price validation)\b|\b(?:cart|checkout)\b.{0,50}\b(?:audit|test)\b/i.test(text)) return 'cart';
  if (/\b(?:audit|test|crawl|scan|inspect|review|check|perform|run|do)\b.{0,130}\b(?:websites?|web\s?pages?|web apps?|web applications?|sites?|web penetration test)\b|\b(?:websites?|web applications?)\b.{0,80}\b(?:audit|test)\b/i.test(text)) return 'website';
  if (/\b(?:research|investigate|study)\b.{0,130}\b(?:my sources|my knowledge|documents|research|topic)\b/i.test(text)) return 'research';
  return 'chat';
}

/** Route explicit Mac GUI requests to the permissioned desktop panel.
 * This classifier never performs the requested action or grants access.
 */
export function wantsMacControl(message: string): boolean {
  const request = message.trim();
  if (!request) return false;
  // Questions about tools and explanations are ordinary chat.
  if (/^(?:how|why|what|where|when|can|could|would|should|explain|describe|tell me|is it|does it)\b/i.test(request)) return false;
  // Requests about Burp logs, security testing and memory scanning already
  // have separate scoped workflows. Explicit GUI manipulation is different.
  const namedApp = /\b(?:textedit|notes|finder|safari|chrome|firefox|preview|calculator|pages|numbers|keynote|vscode|visual studio code|burp suite|cheat engine|terminal|desktop app|mac app|application)\b/i;
  const device = /\b(?:my mac(?:book)?|my computer|my desktop|macos|mac os|computer screen|my device)\b/i;
  const controlVerb = /\b(?:open|launch|start|close|switch to|control|operate|automate|click|type|enter|paste|scroll|navigate|press|select|fill out|write in|create a document in)\b/i;
  const guiVerb = /\b(?:click|type|enter|paste|scroll|press|select|fill out|create a document|write in|switch to)\b/i;
  if (/\b(?:control|operate|automate|use)\b.{0,90}\b(?:my mac(?:book)?|my computer|my desktop|my device|mac app|desktop app)\b/i.test(request)) return true;
  if (controlVerb.test(request) && device.test(request)) return true;
  if (controlVerb.test(request) && namedApp.test(request) && (guiVerb.test(request) || /\b(?:open|launch|start)\b/i.test(request))) return true;
  if (/\b(?:click|type|select|scroll|press)\b.{0,90}\b(?:button|menu|tab|window|text field|on screen|in the app)\b/i.test(request)) return true;
  return false;
}
