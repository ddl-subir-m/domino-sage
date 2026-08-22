// Written by Sage — do not edit. Sage rewrites this file whenever the app's Resources change.
//
// `models` is every LLM Alias this app may call — pass one by name to `askModel`. `alias` is
// the first of them, the model a call that names none gets. null means no model has been
// chosen yet. See ./sageLlm.ts.
export const sageLlmConfig = {
  alias: null,
  displayName: null,
  base: null,
  project: null,
  models: [],
};
