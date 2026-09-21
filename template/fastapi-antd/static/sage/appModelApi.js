// The app's Model API, called straight from the viewer's browser (#9).
//
// This is NOT the same recipe as `./appLlm.js`, and the difference matters. The LLM Gateway is
// another App on the `apps.` host, so that call is same-origin and the viewer's own Domino session
// cookie authenticates it. A Model API is served from the MAIN Domino host, so every call from a
// published page is cross-origin, and the ingress answers `Access-Control-Allow-Origin: *` with no
// `Allow-Credentials` — which means a credentialed request is refused before it is sent.
//
// So the model's own access token is the credential, sent as `Basic base64(token:token)`. It lives
// in `./appModelApi.config.js`, which means IT IS ON THIS PAGE and anyone who opens the app can read
// it in devtools. That is Domino's own documented pattern for calling a Model API from a page, and
// whoever added the model in Sage was told as much. Two consequences: `credentials: "omit"` is
// explicit, because sending the cookie would get the whole request blocked; and every viewer's call
// is the SAME identity, so a failure is never "you lack access" — it is the app's.
//
// Sage owns this file and `./appModelApi.config.js`. Do not edit either.
window.sage = window.sage || {};

(function () {
  const config = window.appModelApiConfig || {};

  /** Every Model API this app may call, the first being its default. Each entry carries its OWN
   * token, and every one of them is on this page. */
  const models = (config.models && config.models.length)
    ? config.models
    : config.url && config.token
      ? [{ name: config.name || "The model", url: config.url, token: config.token }]
      : [];

  const NO_MODEL_API =
    "This app has no Model API yet. Whoever built it can add one in Sage: in the list of what this " +
    "app ships, choose the Use in action that names this app.";

  function pick(name) {
    if (!name) return models[0] || null;
    return models.find((m) => m.name === name) || null;
  }

  function unknownModelApi(name) {
    const known = models.map((m) => m.name).join(", ");
    return known ? `This app is not set up to call the Model API ${name}. It calls: ${known}.` : NO_MODEL_API;
  }

  /**
   * A failed prediction, with a `message` written for the viewer. `detail` is the model's OWN words
   * when the model is what refused — a 400 naming the argument it wanted, or the traceback the
   * deployed function raised. Render it raw in a monospace block and do not reword it.
   */
  class ModelApiError extends Error {
    constructor(message, status = null, detail = null) {
      super(message);
      this.name = "ModelApiError";
      this.status = status;
      this.detail = detail;
    }
  }
  sage.ModelApiError = ModelApiError;

  /** One sentence per way a Model API says no. None of these blame the viewer: the app calls with
   * one shared token, so a refusal is the same for everyone who opens it. */
  function httpMessage(status, name) {
    if (status === 401 || status === 403) {
      return `${name} refused this app's access token. It was probably regenerated in Domino. Whoever built ` +
        "this app needs to paste the current one into Sage.";
    }
    if (status === 404) return `${name} is no longer deployed in Domino. Whoever built this app needs to point it at another model.`;
    if (status === 429) return `${name} is busy right now. Wait a moment and try again.`;
    if (status === 503) return `${name} is not running in Domino. It needs to be started before this app can use it.`;
    if (status >= 500) return `${name} failed while answering. Try again in a moment.`;
    return `${name} did not answer (error ${status}). Try again in a moment.`;
  }

  /** The model's own message out of an error body, best effort: JSON with an `errors` array, a
   * bare string, or an HTML page from something sitting in front of the model. */
  async function detailOf(res) {
    let raw;
    try {
      raw = (await res.text()).trim();
    } catch {
      return null;
    }
    if (!raw) return null;
    try {
      const parsed = JSON.parse(raw);
      for (const key of ["errors", "error", "message", "detail"]) {
        const value = parsed && parsed[key];
        if (Array.isArray(value)) return value.join("; ");
        if (value) return String(value);
      }
    } catch {
      // Not JSON. The raw text is still the most useful thing we have.
    }
    return raw;
  }

  /**
   * Send one prediction request to the app's Model API and resolve its result.
   *
   * `input` is the model's own input — whatever the deployed function's arguments are, which only
   * whoever built the app knows. It is wrapped as Domino's `{"data": …}` envelope here:
   *
   *     const result = await sage.callModelApi({ score: 0.9 });
   *     const risk = await sage.callModelApi({ score: 0.9 }, { model: "fraud-scorer" });
   *
   * Rejects with a `sage.ModelApiError`. Show `error.message` to the viewer as it is, and
   * `error.detail` beneath it in monospace when it is set.
   */
  sage.callModelApi = async function callModelApi(input, opts = {}) {
    if (!models.length) throw new ModelApiError(NO_MODEL_API);
    const target = pick(opts.model);
    if (!target) throw new ModelApiError(unknownModelApi(opts.model));
    const name = target.name;

    let res;
    try {
      res = await fetch(target.url, {
        method: "POST",
        // Explicit, and load-bearing: the model ingress answers `Allow-Origin: *` with no
        // `Allow-Credentials`, so a credentialed cross-origin request is refused by the browser
        // before it is sent. The token below is the whole authentication story.
        credentials: "omit",
        signal: opts.signal,
        headers: {
          "Content-Type": "application/json",
          Authorization: "Basic " + btoa(`${target.token}:${target.token}`),
        },
        body: JSON.stringify({ data: input }),
      });
    } catch (e) {
      if (e && e.name === "AbortError") throw e;
      throw new ModelApiError(`${name} is not answering. Check your connection and try again.`);
    }

    if (res.status === 400) {
      // The credential worked and the model turned the request down. Its own words are the answer.
      throw new ModelApiError(`${name} rejected this request.`, 400, await detailOf(res));
    }
    if (!res.ok) throw new ModelApiError(httpMessage(res.status, name), res.status, await detailOf(res));

    let body;
    try {
      body = await res.json();
    } catch {
      throw new ModelApiError(`${name} sent an answer that could not be read.`, res.status);
    }
    return body && body.result;
  };
})();
