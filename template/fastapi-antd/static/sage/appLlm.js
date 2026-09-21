// The app's language model, called straight from the viewer's browser (#7).
//
// A published app is served from `apps.<domino-host>`, and Domino's LLM Gateway is another App on
// that same host — so a request to it is SAME-ORIGIN, and the viewer's own Domino session cookie
// authenticates it. That is why there is no key in this file and no server hop: a key shipped to
// the browser is a key given away, and a server hop would spend the publisher's access on the
// viewer's behalf. Each viewer spends their own grant, and the gateway's usage log attributes the
// call to them.
//
// The PREVIEW cannot make that call: it is served from Sage's own origin, so the identical request
// is cross-origin and the fetch fails before it leaves the page. Sage's preview proxy makes the call
// server-side and `sage.preview` routes this helper at it — so an app with a model can be tried
// while it is being built, not only after it ships.
//
// Sage owns this file and `./appLlm.config.js`. Do not edit either: the config is rewritten whenever
// the app's Resources change, and an edit here is overwritten.
window.sage = window.sage || {};

(function () {
  const config = window.appLlmConfig || {};

  /** Every Alias this app may call, the first being its default. `alias`/`displayName` are the same
   * first entry, kept beside the list so a config written by a newer Sage still reads here. */
  const models = (config.models && config.models.length)
    ? config.models
    : config.alias
      ? [{ alias: config.alias, displayName: config.displayName }]
      : [];

  // DESCRIBES the act rather than quoting a label: the door draws `Use in {app name}`, which a
  // string in this file cannot know.
  const NO_MODEL =
    "This app has no language model yet. Whoever built it can add one in Sage: in the list of what " +
    "this app ships, choose the Use in action that names this app.";

  function pick(alias) {
    if (!alias) return models[0] || null;
    return models.find((m) => m.alias === alias) || null;
  }

  function unknownModel(alias) {
    const known = models.map((m) => m.alias).join(", ");
    return known ? `This app is not set up to use the model ${alias}. It uses: ${known}.` : NO_MODEL;
  }

  function labelOf(model) {
    return model ? model.displayName || model.alias : "this app's model";
  }

  /** Cost attribution. All keys are `sage-`-namespaced because the gateway silently DROPS its
   * reserved ones (`user`, `model`, `alias`, `project`, `cost`, …) rather than rejecting them. */
  function tagHeaders() {
    const headers = {
      "X-LLM-Tag-sage-source": "domino-sage",
      "X-LLM-Tag-sage-component": "built-app",
    };
    if (config.project) headers["X-LLM-Tag-sage-project"] = config.project;
    return headers;
  }

  // Where the call actually goes: the gateway directly when published, Sage's preview proxy while
  // being built (see the header).
  function endpoint(path) {
    if (sage.preview) return sage.url("api/llm" + path);
    return `${(config.base || "").replace(/\/$/, "")}${path}`;
  }

  // `include` rather than the same-origin default: the base is an absolute URL, and being explicit
  // says the cookie is the whole authentication story here.
  const CREDENTIALS = "include";

  /** One sentence for the viewer, per failure. */
  function httpMessage(status, called = models[0] || null) {
    const model = labelOf(called);
    if (status === 401) return "Your Domino session has expired. Reload the page to sign in again.";
    if (status === 403) return `Your Domino account cannot use ${model}. Ask a Domino administrator for access to it.`;
    if (status === 404) return `${model} is no longer registered in Domino's LLM Gateway. Whoever built this app needs to point it at another model.`;
    if (status === 429) return "The model is busy right now. Wait a moment and try again.";
    return `The model did not answer (error ${status}).`;
  }

  /**
   * Is the model this app was built on available to the person looking at the page? Worth asking
   * on load: the answer is a property of the VIEWER, not of the app — each viewer's own grants
   * decide — so an app that skips this check works for its creator and fails on a button click for
   * the colleague they sent it to. Resolves `{ ok: true, alias, displayName }` or
   * `{ ok: false, message }` with the sentence to show.
   */
  sage.checkModel = async function checkModel(alias) {
    if (!config.base || !models.length) return { ok: false, message: NO_MODEL };
    const model = pick(alias);
    if (!model) return { ok: false, message: unknownModel(alias) };
    let res;
    try {
      res = await fetch(endpoint("/models"), { credentials: CREDENTIALS });
    } catch {
      return { ok: false, message: "Domino's LLM Gateway is not answering. Check your connection and reload the page." };
    }
    if (!res.ok) return { ok: false, message: httpMessage(res.status, model) };
    let ids;
    try {
      const body = await res.json();
      const rows = Array.isArray(body) ? body : (body && (body.data || body.items)) || [];
      ids = rows.map((r) => r && r.id).filter((id) => typeof id === "string");
    } catch {
      // A signed-out session is served an HTML login page with a 200, so a body that will not
      // parse means the session, not the gateway.
      return { ok: false, message: "Your Domino session has expired. Reload the page to sign in again." };
    }
    if (!ids.includes(model.alias)) return { ok: false, message: httpMessage(403, model) };
    return { ok: true, alias: model.alias, displayName: labelOf(model) };
  };

  const INCOMPLETE = "The answer is incomplete. Do not use it as a final result.";

  /** A rejected answer is never a final result. `partialText` may be displayed as incomplete only.
   * `kind` is one of: refused, authentication, access, rate_limit, provider, http, transport,
   * cancelled, incomplete, invalid_response. */
  class ModelError extends Error {
    constructor(kind, message, evidence, partialText = "", reason = null) {
      super(message);
      this.name = kind === "cancelled" ? "AbortError" : "ModelError";
      this.kind = kind;
      this.partialText = partialText;
      this.evidence = evidence;
      this.reason = reason;
    }
  }
  sage.ModelError = ModelError;

  // Only the gateway's structured refusal category and bounded guardrail-name sentence are safe
  // viewer details. Arbitrary provider messages can quote the request; never echo that raw body.
  function responseError(body, evidence, partial = "") {
    const error = (body && body.detail && body.detail.error) || (body && body.error);
    if (!error) return null;
    if (error.type === "gateway_transport_error") {
      return new ModelError("transport", `The preview could not reach the gateway. Receipt is unknown. ${INCOMPLETE}`,
        evidence, partial);
    }
    const named = typeof error.message === "string" &&
      /^Blocked by guardrail: [^\r\n<>]{1,120}$/.test(error.message) ? error.message : null;
    const refused = error.type === "guardrail_blocked" || error.code === "content_filter" || named !== null;
    return new ModelError(refused ? "refused" : "provider",
      refused ? `${named || "The gateway refused this request."} Change the request before sending it again. ${INCOMPLETE}`
        : `The model could not finish the answer. ${INCOMPLETE}`,
      evidence, partial, refused ? (error.code === "content_filter" ? "content_filter" : "guardrail_blocked") : null);
  }

  function recordBody(body, evidence) {
    if (body && typeof body.model === "string") evidence.responseModel = body.model;
    if (body && typeof body.id === "string") evidence.responseId = body.id;
  }

  function checkChoice(choice, evidence, partial) {
    if (choice && choice.finish_reason != null) evidence.finishReason = String(choice.finish_reason);
    const message = choice && (choice.delta || choice.message);
    if ((message && message.refusal) || (choice && choice.finish_reason === "content_filter")) {
      throw new ModelError("refused", `The model refused this request. Change the request before sending it again. ${INCOMPLETE}`,
        evidence, partial, "content_filter");
    }
    if (choice && choice.finish_reason != null && choice.finish_reason !== "stop") {
      // This helper returns text, so tool calls and output caps are not finished text answers.
      throw new ModelError("incomplete", INCOMPLETE, evidence, partial);
    }
  }

  /**
   * Ask one of this app's models a question, and resolve with its whole answer.
   *
   * Rejects with a `sage.ModelError` whose `message` is written for the viewer — show it as-is.
   *
   *     const answer = await sage.askModel([{ role: "user", content: question }]);
   *
   * Pass `onToken` to render the answer as it arrives; `onOutcome` for one final outcome per
   * request; `alias` when this app uses more than one model (the names are in `models` in
   * `./appLlm.config.js`); `signal` to cancel; `maxTokens` and `temperature` as usual.
   *
   * Streaming is off unless `onToken` is given, because not every Alias offers it.
   */
  sage.askModel = async function askModel(messages, opts = {}) {
    if (!config.base || !models.length) throw new Error(NO_MODEL);
    const model = pick(opts.alias);
    if (!model) throw new Error(unknownModel(opts.alias));
    const stream = typeof opts.onToken === "function";
    const evidence = {
      requestedAlias: model.alias, requestId: null, responseId: null, responseModel: null,
      fallbackServedBy: null, fallbackReason: null, cacheStatus: null, servingModel: null,
      providerReceipt: null, decisionStage: null, finishReason: null,
    };
    let answer;
    try {
      const payload = { model: model.alias, messages, stream };
      if (opts.maxTokens !== undefined) payload.max_tokens = opts.maxTokens;
      if (opts.temperature !== undefined) payload.temperature = opts.temperature;
      const res = await fetch(endpoint("/chat/completions"), {
        method: "POST",
        credentials: CREDENTIALS,
        signal: opts.signal,
        headers: { "Content-Type": "application/json", ...tagHeaders() },
        body: JSON.stringify(payload),
      });
      evidence.requestId = res.headers.get("x-request-id");
      evidence.fallbackServedBy = res.headers.get("x-llm-fallback-served-by");
      evidence.fallbackReason = res.headers.get("x-llm-fallback-reason");
      evidence.cacheStatus = res.headers.get("x-cache");
      if (!res.ok) {
        let body = {};
        try { body = await res.json(); } catch { /* Status remains useful without a JSON body. */ }
        const error = responseError(body, evidence);
        if (error && (error.kind === "refused" || error.kind === "transport")) throw error;
        const kind = res.status === 401 ? "authentication" : res.status === 403 ? "access"
          : res.status === 429 ? "rate_limit" : error ? "provider" : "http";
        throw new ModelError(kind, httpMessage(res.status, model), evidence);
      }
      answer = stream ? await readStream(res, opts, evidence) : await readWhole(res, evidence);
      if (opts.signal && opts.signal.aborted) {
        throw new ModelError("cancelled", `The request was cancelled. ${INCOMPLETE}`, evidence);
      }
    } catch (error) {
      const aborted = opts.signal && opts.signal.aborted;
      const failure = error instanceof ModelError ? error : new ModelError(
        aborted ? "cancelled" : "transport",
        aborted ? `The request was cancelled. ${INCOMPLETE}`
          : `The connection to the gateway failed. Receipt is unknown. ${INCOMPLETE}`, evidence);
      if (opts.onOutcome) opts.onOutcome({ status: failure.kind, evidence });
      throw failure;
    }
    if (opts.onOutcome) opts.onOutcome({ status: "complete", evidence });
    return answer;
  };

  async function readWhole(res, evidence) {
    let body;
    try { body = await res.json(); } catch (error) {
      if (!(error instanceof SyntaxError)) throw error;
      throw new ModelError("invalid_response", `The model's answer could not be read. ${INCOMPLETE}`, evidence);
    }
    recordBody(body, evidence);
    const error = responseError(body, evidence);
    if (error) throw error;
    const choice = body && body.choices && body.choices[0];
    const text = choice && choice.message && choice.message.content;
    checkChoice(choice, evidence, typeof text === "string" ? text : "");
    if (!choice || choice.finish_reason !== "stop" || typeof text !== "string") {
      throw new ModelError("incomplete", INCOMPLETE, evidence, typeof text === "string" ? text : "");
    }
    return text;
  }

  // An SSE event ends with a blank line, not a network chunk. A finish_reason alone is not enough:
  // the gateway can send an error after it. Require [DONE], and reject any unfinished event at EOF.
  async function readStream(res, opts, evidence) {
    if (!res.body) throw new ModelError("incomplete", INCOMPLETE, evidence);
    const reader = res.body.getReader();
    const decoder = new TextDecoder();
    let buffer = "";
    let answer = "";
    try {
      for (;;) {
        const { done, value } = await reader.read();
        if (done) break;
        buffer += decoder.decode(value, { stream: true });
        let boundary;
        while ((boundary = /\r?\n\r?\n/.exec(buffer))) {
          const event = buffer.slice(0, boundary.index);
          buffer = buffer.slice(boundary.index + boundary[0].length);
          const lines = event.split(/\r?\n/);
          const data = lines.filter((line) => line.startsWith("data:"))
            .map((line) => line.slice(5).trimStart()).join("\n");
          if (!data) continue;
          if (data.trim() === "[DONE]") {
            if (evidence.finishReason !== "stop") throw new ModelError("incomplete", INCOMPLETE, evidence, answer);
            return answer;
          }
          let chunk;
          try { chunk = JSON.parse(data); } catch {
            throw new ModelError("invalid_response", INCOMPLETE, evidence, answer);
          }
          recordBody(chunk, evidence);
          const error = responseError(chunk, evidence, answer);
          if (error) throw error;
          if (lines.some((line) => line.startsWith("event:") && line.slice(6).trim() === "error")) {
            throw new ModelError("provider", `The model could not finish the answer. ${INCOMPLETE}`, evidence, answer);
          }
          const choice = chunk && chunk.choices && chunk.choices[0];
          if (evidence.finishReason && choice && choice.delta && choice.delta.content) {
            throw new ModelError("invalid_response", INCOMPLETE, evidence, answer);
          }
          checkChoice(choice, evidence, answer);
          const text = choice && choice.delta && choice.delta.content;
          if (text != null && typeof text !== "string") {
            throw new ModelError("invalid_response", INCOMPLETE, evidence, answer);
          }
          if (typeof text === "string" && text) {
            answer += text;
            opts.onToken(text);
            if (opts.signal && opts.signal.aborted) {
              throw new ModelError("cancelled", `The request was cancelled. ${INCOMPLETE}`, evidence, answer);
            }
          }
        }
      }
      throw new ModelError("incomplete", INCOMPLETE, evidence, answer);
    } catch (error) {
      if (error instanceof ModelError) throw error;
      const aborted = opts.signal && opts.signal.aborted;
      throw new ModelError(aborted ? "cancelled" : "transport",
        aborted ? `The request was cancelled. ${INCOMPLETE}`
          : `The connection ended before the answer was complete. ${INCOMPLETE}`, evidence, answer);
    } finally {
      await reader.cancel().catch(() => {});
      reader.releaseLock();
    }
  }
})();
