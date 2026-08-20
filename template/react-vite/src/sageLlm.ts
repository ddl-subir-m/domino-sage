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
// Sage owns this file and `./sageLlm.config`. Do not edit either: the config is rewritten whenever
// the app's Resources change, and an edit here is overwritten.
import { sageLlmConfig } from "./sageLlm.config";

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
};

type Config = { alias: string | null; displayName: string | null; base: string | null; project: string | null };

// Widened on purpose: the generated config annotates nothing, so `alias: null` would otherwise be
// of type `null` and every comparison against a string would read as dead code.
const config: Config = sageLlmConfig;

const NO_MODEL =
  "This app has no language model yet. Whoever built it can add one in Sage: open the Resources " +
  "panel and choose Use on an LLM Alias.";

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

function endpoint(path: string): string {
  return `${(config.base || "").replace(/\/$/, "")}${path}`;
}

// `include` rather than the same-origin default: the base is an absolute URL, and being explicit
// says the cookie is the whole authentication story here.
const CREDENTIALS: RequestCredentials = "include";

/** One sentence for the viewer, per failure. A model call can fail for reasons that need opposite
 * responses — sign in again, ask for access, wait and retry — and "something went wrong" sends
 * everyone down the wrong one. */
function httpMessage(status: number): string {
  const model = config.displayName || config.alias || "this app's model";
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
export async function checkModel(): Promise<ModelStatus> {
  if (!config.alias || !config.base) return { ok: false, message: NO_MODEL };
  let res: Response;
  try {
    res = await fetch(endpoint("/models"), { credentials: CREDENTIALS });
  } catch {
    return { ok: false, message: "Domino's LLM Gateway is not answering. Check your connection and reload the page." };
  }
  if (!res.ok) return { ok: false, message: httpMessage(res.status) };
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
  if (!ids.includes(config.alias)) return { ok: false, message: httpMessage(403) };
  return { ok: true, alias: config.alias, displayName: config.displayName || config.alias };
}

/**
 * Ask the app's model a question, and resolve with its whole answer.
 *
 * Rejects with an `Error` whose `message` is written for the viewer — show it as-is.
 *
 *     const answer = await askModel([{ role: "user", content: question }]);
 *
 * Pass `onToken` to render the answer as it arrives:
 *
 *     await askModel(messages, { onToken: (t) => setAnswer((a) => a + t) });
 *
 * Streaming is off unless `onToken` is given, because not every Alias offers it — the capability is
 * per-Alias in the gateway, and asking for a stream from one that has none fails the whole call.
 */
export async function askModel(messages: ChatMessage[], opts: AskOptions = {}): Promise<string> {
  if (!config.alias || !config.base) throw new Error(NO_MODEL);
  const stream = typeof opts.onToken === "function";
  let res: Response;
  try {
    res = await fetch(endpoint("/chat/completions"), {
      method: "POST",
      credentials: CREDENTIALS,
      signal: opts.signal,
      headers: { "Content-Type": "application/json", ...tagHeaders() },
      body: JSON.stringify({
        model: config.alias,
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
  if (!res.ok) throw new Error(httpMessage(res.status));
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
