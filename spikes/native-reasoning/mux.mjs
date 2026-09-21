import { createAnthropic } from '@ai-sdk/anthropic';
import { createOpenAI } from '@ai-sdk/openai';
import { createOpenAICompatible } from '@ai-sdk/openai-compatible';

export function createSageProbe(options) {
  const origin = new URL(options.baseURL).origin;
  const providers = {
    messages: createAnthropic({baseURL: origin + '/anthropic/v1', apiKey:'local-only'}),
    responses: createOpenAI({baseURL: origin + '/v1', apiKey:'local-only'}),
    chat: createOpenAICompatible({baseURL: origin + '/v1', apiKey:'local-only', name:'google'}),
  };
  function languageModel(modelId) {
    return {
      specificationVersion: 'v3', provider: 'sage-probe.chat', modelId, supportedUrls: {},
      async doStream(params) {
        const response = await fetch(origin + '/route', {method:'POST',
          headers:{'content-type':'application/json'}, body:JSON.stringify({prompt:params.prompt,tools:params.tools})});
        if(!response.ok) throw new Error('No route');
        const choice = await response.json();
        const provider = providers[choice.protocol];
        const model = choice.protocol === 'responses' ? provider.responses(choice.model) : provider.languageModel(choice.model);
        return model.doStream({...params, providerOptions:choice.protocol==='responses'
          ? {openai:{store:false,include:['reasoning.encrypted_content'],reasoningEffort:'low'}}
          : choice.protocol==='messages' ? {anthropic:{thinking:{type:'adaptive'},effort:'high'}}
          : {google:{reasoningEffort:'low'}}});
      },
      async doGenerate() {throw new Error('Probe requires streaming');},
    };
  }
  return {languageModel};
}
