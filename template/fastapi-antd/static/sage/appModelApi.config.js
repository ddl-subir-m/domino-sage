// Written by Sage — do not edit. Sage rewrites this file whenever the app's Resources change.
//
// `models` is every Model API this app may call — pass one by name to `sage.callModelApi`. Each
// entry's `token` is that model's access token, and ANYONE WHO OPENS THE PUBLISHED APP CAN READ
// THEM: this is a page's only way to call a Model API, and whoever added the model was told so.
// `name`/`url`/`token` repeat the first entry. null means no Model API has been chosen yet.
// See ./appModelApi.js.
window.appModelApiConfig = {
  name: null,
  url: null,
  token: null,
  models: [],
};
