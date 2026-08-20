// The app's Model API, called straight from the viewer's browser (#9).
//
// This is NOT the same recipe as `./sageLlm.ts`, and the difference matters. The LLM Gateway is
// another App on the `apps.` host, so that call is same-origin and the viewer's own Domino session
// cookie authenticates it — no key, and each viewer spends their own grant. A Model API is served
// from the MAIN Domino host, so every call from a published page is cross-origin, and the ingress
// answers `Access-Control-Allow-Origin: *` with no `Allow-Credentials` — which, per the CORS spec,
// means a credentialed request is refused before it is sent. The viewer's cookie can never reach it.
//
// So the model's own access token is the credential, sent as `Basic base64(token:token)` — the one
// shape a Model API accepts, verified against every alternative Domino offers. It lives in
// `./sageModelApi.config`, which means IT IS IN THIS BUNDLE and anyone who opens the app can read it
// in devtools. That is Domino's own documented pattern for calling a Model API from a page, and
// whoever added the model in Sage was told as much before pasting it. Two consequences for the code
// here: `credentials: "omit"` is explicit, because sending the cookie would get the whole request
// blocked by the wildcard above; and every viewer's call is the SAME identity, so a failure is never
// "you lack access" — it is the app's, and the message says so.
//
// Sage owns this file and `./sageModelApi.config`. Do not edit either: the config is rewritten
// whenever the app's Resources change, and an edit here is overwritten.
import { sageModelApiConfig } from "./sageModelApi.config";

type Config = { name: string | null; url: string | null; token: string | null };

// Widened on purpose, for the reason sageLlm.ts widens its own: the generated config annotates
// nothing, so `url: null` would otherwise have type `null` and every comparison read as dead code.
const config: Config = sageModelApiConfig;

const NO_MODEL_API =
  "This app has no Model API yet. Whoever built it can add one in Sage: open the Resources panel " +
  "and choose Use on a Model API.";

/**
 * A failed prediction, with a `message` written for the viewer.
 *
 * `detail` is the model's OWN words, when the model is what refused — a 400 naming the argument it
 * wanted, or the traceback the deployed function raised. Render it raw in a monospace block and do
 * not reword it: it is user code output, and it is the only part that says what to change.
 */
export class ModelApiError extends Error {
  readonly status: number | null;
  readonly detail: string | null;

  constructor(message: string, status: number | null = null, detail: string | null = null) {
    super(message);
    this.name = "ModelApiError";
    this.status = status;
    this.detail = detail;
  }
}

/** One sentence per way a Model API says no, written for whoever is looking at the page.
 *
 * None of these blame the viewer. The app calls with one shared token, so a refusal is the same for
 * everyone who opens it — telling this viewer to ask for access would send them after a permission
 * that would not help.
 */
function httpMessage(status: number, name: string): string {
  if (status === 401 || status === 403) {
    return (
      `${name} refused this app's access token. It was probably regenerated in Domino. Whoever built ` +
      "this app needs to paste the current one into Sage."
    );
  }
  if (status === 404) {
    return `${name} is no longer deployed in Domino. Whoever built this app needs to point it at another model.`;
  }
  if (status === 429) return `${name} is busy right now. Wait a moment and try again.`;
  if (status === 503) return `${name} is not running in Domino. It needs to be started before this app can use it.`;
  if (status >= 500) return `${name} failed while answering. Try again in a moment.`;
  return `${name} did not answer (error ${status}). Try again in a moment.`;
}

/** The model's own message out of an error body, best effort.
 *
 * A Model API's error body is whatever the deployed function raised, so this may be JSON with an
 * `errors` array, a bare string, or an HTML page from something sitting in front of the model.
 */
async function detailOf(res: Response): Promise<string | null> {
  let raw: string;
  try {
    raw = (await res.text()).trim();
  } catch {
    return null;
  }
  if (!raw) return null;
  try {
    const parsed = JSON.parse(raw);
    for (const key of ["errors", "error", "message", "detail"]) {
      const value = (parsed as Record<string, unknown>)?.[key];
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
 * `input` is the model's own input — whatever the deployed function's arguments are. Sage does not
 * know that shape and neither does Domino: no listing, endpoint or SDK publishes a Model API's
 * signature, so it comes from whoever built the app. It is wrapped as Domino's `{"data": …}`
 * envelope here, so pass the arguments themselves:
 *
 *     const result = await callModelApi({ score: 0.9 });
 *
 * Rejects with a `ModelApiError`. Show `error.message` to the viewer as it is, and `error.detail`
 * beneath it in monospace when it is set.
 */
export async function callModelApi<T = unknown>(
  input: unknown,
  opts: { signal?: AbortSignal } = {},
): Promise<T> {
  if (!config.url || !config.token) throw new ModelApiError(NO_MODEL_API);
  const name = config.name || "The model";

  let res: Response;
  try {
    res = await fetch(config.url, {
      method: "POST",
      // Explicit, and load-bearing: the model ingress answers `Allow-Origin: *` with no
      // `Allow-Credentials`, so a credentialed cross-origin request is refused by the browser
      // before it is sent. The token below is the whole authentication story.
      credentials: "omit",
      signal: opts.signal,
      headers: {
        "Content-Type": "application/json",
        Authorization: "Basic " + btoa(`${config.token}:${config.token}`),
      },
      body: JSON.stringify({ data: input }),
    });
  } catch (e) {
    // An abort is the caller's own doing — unmounting a component, usually — not a failure to report.
    if ((e as { name?: string })?.name === "AbortError") throw e;
    throw new ModelApiError(`${name} is not answering. Check your connection and try again.`);
  }

  if (res.status === 400) {
    // The credential worked and the model turned the request down. Its own words are the answer.
    throw new ModelApiError(
      `${name} rejected this request.`,
      400,
      await detailOf(res),
    );
  }
  if (!res.ok) throw new ModelApiError(httpMessage(res.status, name), res.status, await detailOf(res));

  let body: { result?: unknown };
  try {
    body = await res.json();
  } catch {
    throw new ModelApiError(`${name} sent an answer that could not be read.`, res.status);
  }
  return body?.result as T;
}
