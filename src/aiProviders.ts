export type Category = 'subscription' | 'api' | 'selfhosted';
export const AI_CATEGORIES: {id: Category; label: string; detail: string}[] = [
  {id: 'subscription', label: 'Subscriptions', detail: 'ChatGPT and Claude plans'},
  {id: 'api', label: 'Paid APIs', detail: 'Connect with an API key'},
  {id: 'selfhosted', label: 'My own models', detail: 'Local and custom servers'},
];
export const AI_PROVIDERS: Record<Category, {id: string; name: string; detail: string}[]> = {
  subscription: [
    {id: 'codex', name: 'ChatGPT subscription', detail: 'Your ChatGPT plan through Codex'},
    {id: 'claude_code', name: 'Claude subscription', detail: 'Your Claude plan through Claude Code'},
  ],
  api: [
    {id: 'openai', name: 'OpenAI API', detail: 'Separate API billing'},
    {id: 'anthropic', name: 'Anthropic API', detail: 'Separate API billing'},
    {id: 'deepseek', name: 'DeepSeek', detail: 'API billing'},
  ],
  selfhosted: [
    {id: 'ollama', name: 'Ollama on this Mac', detail: 'Local models'},
    {id: 'openai_compatible', name: 'Kaggle / custom AI', detail: 'OpenAI-compatible server'},
    {id: 'remote_ollama', name: 'Ollama on another computer', detail: 'Remote Ollama API'},
    {id: 'crucible', name: 'CrucibleLLM', detail: 'Model on your device'},
  ],
};
export const categoryFor = (id: string): Category => AI_CATEGORIES.find(c => AI_PROVIDERS[c.id].some(p => p.id === id))?.id || 'selfhosted';
export const isSubscription = (id: string) => categoryFor(id) === 'subscription';
