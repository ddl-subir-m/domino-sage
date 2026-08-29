// The app's language model, called straight from the viewer's browser (#7).
//
// A published app is served from `apps.<domino-host>`, and Domino's LLM Gateway is another App on
// that same host — so a request to it is SAME-ORIGIN, and the viewer's own Domino session cookie
// authenticates it. Verified live on cloud-dogfood 2026-08-19: cookies alone, no Authorization
// header, `GET /v1/models` -> 200 and `POST /v1/chat/completions` -> 200, streaming included, and
// `/v1/whoami` resolves the BROWSING user rather than whoever published the app.
//
// That is why there is no key in this file and no server hop. Both would be worse than useless
// here: a key shipped to the browser is a key given away, and a server hop would spend the
// publisher's access on the viewer's behalf, which is exactly the sharing this avoids. Each viewer
// spends their own grant, and the gateway's usage log attributes the call to them.
//
// The one thing a browser call does NOT carry is project context — `/v1/whoami` returns an empty
// project_name for it — so the gateway's first-class per-project columns are blank for this traffic.
// The `sage-project` tag below is the only thing that says which app the spend came from.
//
// Sage owns this file and `./appLlm.config`. Do not edit either: the config is rewritten whenever
// the app's Resources change, and an edit here is overwritten.
import { appLlmConfig } from "./appLlm.config";

export type ChatMessage = { role: "system" | "user" | "assistant"; content: string };

/** What `checkModel` found. `ok: false` carries the sentence to show the viewer, already written. */
export type ModelStatus =
  | { ok: true; alias: string; displayName: string }
  | { ok: false; message: string };

export type AskOptions = {
  /** Called with each chunk of text as it arrives. Passing it turns on streaming. */
  onToken?: (chunk: string) => void;
  maxTokens?: number;
  temperature?: number;
  /** Abort the request — pass `AbortController.signal` to cancel on unmount. */
  signal?: AbortSignal;
  /**
   * Which model to ask, by Alias name — one of `models` in `./appLlm.config`. Omit it and the
   * app's default model answers, so a call written before this app used a second model is
   * unchanged. An Alias this app is not recorded as using is refused rather than quietly swapped
   * for the default: a summary that silently came from another model is a wrong answer nobody sees.
   */
  alias?: string;
};

type Model = { alias: string; displayName: string | null };
type Config = {
  alias: string | null;
  displayName: string | null;
  base: string | null;
  project: string | null;
  models?: Model[];
};

// Widened on purpose: the generated config annotates nothing, so `alias: null` would otherwise be
// of type `null` and every comparison against a string would read as dead code.
const config: Config = appLlmConfig;

/** Every Alias this app may call, the first being its default. `alias`/`displayName` are the same
 * first entry, kept beside the list so a config written by a newer Sage still reads in an app whose
 * helper predates the list — and so this helper reads a config written by an older one. */
const models: Model[] = config.models?.length
  ? config.models
  : config.alias
    ? [{ alias: config.alias, displayName: config.displayName }]
    : [];

const NO_MODEL =
  "This app has no language model yet. Whoever built it can add one in Sage: open the Resources " +
  "panel and choose Use on an LLM Alias.";

/** The model a call means, or null when it names one this app does not use. */
function pick(alias?: string): Model | null {
  if (!alias) return models[0] || null;
  return models.find((m) => m.alias === alias) || null;
}

function unknownModel(alias: string): string {
  const known = models.map((m) => m.alias).join(", ");
  return known
    ? `This app is not set up to use the model ${alias}. It uses: ${known}.`
    : NO_MODEL;
}

function labelOf(model: Model | null): string {
  return model ? model.displayName || model.alias : "this app's model";
}

/** Cost attribution. All keys are `sage-`-namespaced because the gateway silently DROPS its
 * reserved ones (`user`, `model`, `alias`, `project`, `cost`, …) rather than rejecting them, so a
 * plain `project` tag would look accepted and never appear in the dashboard. */
function tagHeaders(): Record<string, string> {
  const headers: Record<string, string> = {
    "X-LLM-Tag-sage-source": "domino-sage",
    "X-LLM-Tag-sage-component": "built-app",
  };
  if (config.project) headers["X-LLM-Tag-sage-project"] = config.project;
  return headers;
}

// Where the call actually goes. The published app calls the gateway directly, which is the whole
// design above — same origin, the viewer's own cookie, no key and no server hop.
//
// The PREVIEW cannot. It is served from Sage's own origin, so the identical request is cross-origin,
// and a credentialed cross-origin fetch needs CORS headers the gateway does not send: the fetch
// throws before it leaves the page, and the app reports the gateway as not answering when nothing is
// wrong with the gateway. Sage's preview proxy therefore makes the call server-side and this points
// at it — so an app with a model can be tried while it is being built, not only after it ships.
//
// `import.meta.env.DEV` rather than a runtime sniff: Vite replaces it at build time, so the
// published bundle contains only the direct call and cannot take this branch by accident.
//
// `BASE_URL` rather than importing `./appBase`: in the dev server the two are the same string —
// `appBase` only prefers `window.__SAGE_BASE__`, which `serve.py` stamps and which therefore never
// exists here — and this file has to be droppable into an app built before `appBase.ts` shipped.
function endpoint(path: string): string {
  if (import.meta.env.DEV) {
    return `${import.meta.env.BASE_URL.replace(/\/$/, "")}/api/llm${path}`;
  }
  return `${(config.base || "").replace(/\/$/, "")}${path}`;
}

// `include` rather than the same-origin default: the base is an absolute URL, and being explicit
// says the cookie is the whole authentication story here.
const CREDENTIALS: RequestCredentials = "include";

/** One sentence for the viewer, per failure. A model call can fail for reasons that need opposite
 * responses — sign in again, ask for access, wait and retry — and "something went wrong" sends
 * everyone down the wrong one. */
function httpMessage(status: number, called: Model | null = models[0] || null): string {
  const model = labelOf(called);
  if (status === 401) return "Your Domino session has expired. Reload the page to sign in again.";
  if (status === 403) {
    return `Your Domino account cannot use ${model}. Ask a Domino administrator for access to it.`;
  }
  if (status === 404) {
    return `${model} is no longer registered in Domino's LLM Gateway. Whoever built this app needs to point it at another model.`;
  }
  if (status === 429) return "The model is busy right now. Wait a moment and try again.";
  return `The model did not answer (error ${status}). Try again in a moment.`;
}

/**
 * Is the model this app was built on available to the person looking at the page?
 *
 * Worth asking on load. The answer is a property of the VIEWER, not of the app — the app pins one
 * Alias at build time, but each viewer's own grants decide whether they can call it — so an app
 * that skips this check works perfectly for its creator and fails on a button click for the
 * colleague they sent it to.
 *
 * Resolved against `/v1/models`, the permission-filtered list the gateway resolves a call against,
 * so an Alias missing from it is one that will fail at request time.
 */
export async function checkModel(alias?: string): Promise<ModelStatus> {
  if (!config.base || !models.length) return { ok: false, message: NO_MODEL };
  const model = pick(alias);
  if (!model) return { ok: false, message: unknownModel(alias as string) };
  let res: Response;
  try {
    res = await fetch(endpoint("/models"), { credentials: CREDENTIALS });
  } catch {
    return { ok: false, message: "Domino's LLM Gateway is not answering. Check your connection and reload the page." };
  }
  if (!res.ok) return { ok: false, message: httpMessage(res.status, model) };
  let ids: string[];
  try {
    const body = await res.json();
    const rows: unknown[] = Array.isArray(body) ? body : body?.data || body?.items || [];
    ids = rows.map((r) => (r as { id?: unknown })?.id).filter((id): id is string => typeof id === "string");
  } catch {
    // A signed-out session is served an HTML login page with a 200, so a body that will not parse
    // means the session, not the gateway.
    return { ok: false, message: "Your Domino session has expired. Reload the page to sign in again." };
  }
  if (!ids.includes(model.alias)) return { ok: false, message: httpMessage(403, model) };
  return { ok: true, alias: model.alias, displayName: labelOf(model) };
}

/**
 * Ask one of this app's models a question, and resolve with its whole answer.
 *
 * Rejects with an `Error` whose `message` is written for the viewer — show it as-is.
 *
 *     const answer = await askModel([{ role: "user", content: question }]);
 *
 * Pass `onToken` to render the answer as it arrives:
 *
 *     await askModel(messages, { onToken: (t) => setAnswer((a) => a + t) });
 *
 * Pass `alias` when this app uses more than one model and this call is for a particular one —
 * the names are in `models` in `./appLlm.config`:
 *
 *     await askModel(messages, { alias: "gpt-5.4" });
 *
 * Streaming is off unless `onToken` is given, because not every Alias offers it — the capability is
 * per-Alias in the gateway, and asking for a stream from one that has none fails the whole call.
 */
export async function askModel(messages: ChatMessage[], opts: AskOptions = {}): Promise<string> {
  if (!config.base || !models.length) throw new Error(NO_MODEL);
  const model = pick(opts.alias);
  if (!model) throw new Error(unknownModel(opts.alias as string));
  const stream = typeof opts.onToken === "function";
  let res: Response;
  try {
    res = await fetch(endpoint("/chat/completions"), {
      method: "POST",
      credentials: CREDENTIALS,
      signal: opts.signal,
      headers: { "Content-Type": "application/json", ...tagHeaders() },
      body: JSON.stringify({
        model: model.alias,
        messages,
        stream,
        ...(opts.maxTokens === undefined ? {} : { max_tokens: opts.maxTokens }),
        ...(opts.temperature === undefined ? {} : { temperature: opts.temperature }),
      }),
    });
  } catch (e) {
    // An abort is the caller's own doing, not a failure to report to the viewer.
    if ((e as { name?: string })?.name === "AbortError") throw e;
    throw new Error("Domino's LLM Gateway is not answering. Check your connection and try again.");
  }
  if (!res.ok) throw new Error(httpMessage(res.status, model));
  return stream ? readStream(res, opts.onToken!) : readWhole(res);
}

async function readWhole(res: Response): Promise<string> {
  try {
    const body = await res.json();
    return String(body?.choices?.[0]?.message?.content ?? "");
  } catch {
    throw new Error("The model's answer could not be read. Try again in a moment.");
  }
}

// Server-sent events: `data: {json}` per chunk, `data: [DONE]` to finish. Split on the blank line
// that terminates an event, and keep the tail — a chunk boundary lands mid-event often enough that
// parsing whatever arrived would drop text at random.
async function readStream(res: Response, onToken: (chunk: string) => void): Promise<string> {
  if (!res.body) return readWhole(res);
  const reader = res.body.getReader();
  const decoder = new TextDecoder();
  let buffer = "";
  let answer = "";
  for (;;) {
    const { done, value } = await reader.read();
    if (done) break;
    buffer += decoder.decode(value, { stream: true });
    const events = buffer.split("\n\n");
    buffer = events.pop() ?? "";
    for (const event of events) {
      for (const line of event.split("\n")) {
        if (!line.startsWith("data:")) continue;
        const data = line.slice(5).trim();
        if (!data || data === "[DONE]") continue;
        try {
          const chunk = JSON.parse(data);
          const text = chunk?.choices?.[0]?.delta?.content;
          if (typeof text === "string" && text) {
            answer += text;
            onToken(text);
          }
        } catch {
          // One unparseable event is not worth losing the answer already streamed.
        }
      }
    }
  }
  return answer;
}
