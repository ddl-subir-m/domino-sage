// One stable OpenCode model handle. Its database owns all provider metadata.
import { createAnthropic } from '@ai-sdk/anthropic';
import { createOpenAI } from '@ai-sdk/openai';
import { createOpenAICompatible } from '@ai-sdk/openai-compatible';
import { APICallError } from '@ai-sdk/provider';

function safeError(error) {
  if (error?.name === 'AbortError') return new DOMException('Model request cancelled.', 'AbortError');
  let message = 'The model reply could not be read. Try again.';
  if (APICallError.isInstance(error)) {
    // This is the enforced local endpoint's error, already stripped of upstream
    // bodies. Keep its policy checkpoint message and the SDK's retry semantics.
    try {
      const body = JSON.parse(error.responseBody);
      if (typeof body.error?.message === 'string') message = body.error.message;
      else if (typeof body.message === 'string'
          && /^(The model gateway|The model stream|The gateway|Gateway stream)\b/.test(body.message)) {
        message = body.message;
      }
    } catch { /* No safe server message. */ }
    return new APICallError({message, url: error.url, statusCode: error.statusCode,
      isRetryable: error.isRetryable, requestBodyValues: undefined,
      responseBody: JSON.stringify({error: {message}})});
  }
  // Stream error messages are emitted by our endpoint after validation. SDK
  // validation errors instead embed the rejected event, including opaque state.
  const detail = typeof error === 'string' ? error : error?.message;
  if (typeof detail === 'string'
      && /^(The model gateway|The model stream|The gateway|Gateway stream)\b/.test(detail)) {
    message = detail;
  }
  return new Error(message);
}

function safeStream(stream) {
  const reader = stream.getReader();
  return new ReadableStream({
    async pull(controller) {
      try {
        const {done, value} = await reader.read();
        if (done) {controller.close(); return;}
        let event = value;
        if (event.type === 'stream-start' && event.warnings?.length) {
          event = {...event, warnings: event.warnings.map(() => ({type: 'other',
            message: 'Provider-specific history is retained in the session and is not sent to an incompatible codec.'}))};
        } else if (event.type === 'error') {
          event = {type: 'error', error: safeError(event.error)};
        }
        controller.enqueue(event);
      } catch (error) {controller.error(safeError(error));}
    },
    cancel(reason) {return reader.cancel(reason);},
  });
}

export function createSageGateway(options) {
  const root = options.baseURL.replace(/\/$/, '').replace(/\/v1$/, '');
  const fetchLocal = options.fetch || fetch;
  const providers = {
    messages: createAnthropic({baseURL: root + '/v1/sage/anthropic', apiKey: 'local-shim-no-auth', fetch: fetchLocal}),
    responses: createOpenAI({baseURL: root + '/v1/sage', apiKey: 'local-shim-no-auth', fetch: fetchLocal}),
    chat: createOpenAICompatible({baseURL: root + '/v1/sage', apiKey: 'local-shim-no-auth', name: 'google', fetch: fetchLocal}),
  };
  function languageModel(modelId) {
    return {
      specificationVersion: 'v3', provider: 'sage-gateway.routed', modelId, supportedUrls: {},
      async doStream(params) {
        const response = await fetchLocal(root + '/v1/sage/resolve', {
          method: 'POST', signal: params.abortSignal,
          headers: {...params.headers, 'content-type': 'application/json'},
          body: JSON.stringify({prompt: params.prompt, tools: params.tools}),
        });
        if (!response.ok) {
          const error = await response.json();
          throw new Error(error.error?.message || 'The model route could not be resolved.');
        }
        const route = await response.json();
        const provider = providers[route.protocol];
        if (!provider) throw new Error('The model route is not supported.');
        const model = route.protocol === 'responses'
          ? provider.responses(route.model) : provider.languageModel(route.model);
        const providerOptions = route.protocol === 'responses'
          ? {openai: {store: false, include: ['reasoning.encrypted_content'],
              ...(route.effort !== null ? {reasoningEffort: route.effort} : {})}}
          : route.protocol === 'messages'
            ? {anthropic: route.effort === null ? {} : route.effort === 'none'
                ? {thinking: {type: 'disabled'}} : {thinking: {type: 'adaptive'}, effort: route.effort}}
            : {google: route.effort === null ? {} : {reasoningEffort: route.effort}};
        // Keep state in the normal provider metadata; never copy it into raw
        // diagnostics, SDK errors (which retain request bodies), or warnings.
        try {
          const result = await model.doStream({...params, includeRawChunks: false, providerOptions});
          return {...result, stream: safeStream(result.stream)};
        } catch (error) {throw safeError(error);}
      },
      async doGenerate() {throw new Error('This provider requires the OpenCode streaming path.');},
    };
  }
  return {languageModel};
}
