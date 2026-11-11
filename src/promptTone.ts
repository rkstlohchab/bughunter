/** Small, visible tone edits. Never changes the requested action or adds a cover story. */
export const PROMPT_LIMIT = 8000;

const RULES: [RegExp, string][] = [
  [/\bwhat the (?:hell|fuck)\b/gi, 'what'],
  [/\bfix this shit\b/gi, 'help fix this issue'],
  [/\bthis (?:fucking|damn|stupid)\b/gi, 'this'],
  [/\bthe (?:fucking|damn|stupid)\b/gi, 'the'],
  [/\byou idiot\b/gi, 'please'],
];

export type ToneEdit = { before: string; after: string };

export function polishTone(original: string): { text: string; edits: ToneEdit[] } {
  if (original.length > PROMPT_LIMIT) throw new Error(`Keep the prompt under ${PROMPT_LIMIT.toLocaleString()} characters.`);
  const edits: ToneEdit[] = [];
  // Leave quoted evidence, code, URLs and identifiers intact. Every applied
  // replacement is shown to the person editing the prompt before they copy it.
  const protectedText = /(```[\s\S]*?(?:```|$)|`[^`\n]*(?:`|$)|"[^"\n]*(?:"|$)|'[^'\n]*'|https?:\/\/[^\s]+)/g;
  const text = original.split(protectedText).map((part, index) => {
    if (index % 2) return part;
    for (const [pattern, replacement] of RULES) {
      part = part.replace(pattern, before => {
        const after = /^[A-Z]/.test(before) ? replacement[0].toUpperCase() + replacement.slice(1) : replacement;
        edits.push({ before, after });
        return after;
      });
    }
    return part;
  }).join('');
  return { text, edits };
}
