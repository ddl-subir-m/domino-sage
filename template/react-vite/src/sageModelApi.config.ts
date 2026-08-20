// Written by Sage — do not edit. Sage rewrites this file whenever the app's Resources change.
//
// `token` is this Model API's access token. It is compiled into the app's bundle, so ANYONE
// WHO OPENS THE PUBLISHED APP CAN READ IT and call the model with it until it is regenerated
// from the Model API's Settings page in Domino. That is how Domino's own sample calls a Model
// API from a page: the model has no other credential, and a Domino session will not open one.
//
// null means no Model API has been chosen yet. See ./sageModelApi.ts.
export const sageModelApiConfig = {
  name: null,
  url: null,
  token: null,
};
