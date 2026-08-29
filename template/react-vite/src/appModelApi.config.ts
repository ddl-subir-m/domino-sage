// Written by Sage — do not edit. Sage rewrites this file whenever the app's Resources change.
//
// `models` is every Model API this app may call — pass one by name to `callModelApi`. Each carries
// its own `token`, and every one of them is compiled into the app's bundle, so ANYONE WHO OPENS THE
// PUBLISHED APP CAN READ THEM and call those models until each is regenerated from its Model API's
// Settings page in Domino. That is how Domino's own sample calls a Model API from a page: the model
// has no other credential, and a Domino session will not open one.
//
// `name`/`url`/`token` repeat the first entry. null means no Model API has been chosen yet.
// See ./appModelApi.ts.
export const appModelApiConfig = {
  name: null,
  url: null,
  token: null,
  models: [],
};
