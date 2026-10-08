// Shareable view state (#701): a dashboard's applied selection, kept in this page's own URL fragment
// so a reload or a copied link reopens it. Sage-owned and refreshed from Sage's template; do not edit.
//
//   const VIEW = {                                       // at module level, never inside a component
//     screen: { type: "enum", values: ["revenue", "orders"], default: "revenue", shareable: true },
//     month: { type: "string", pattern: /\d{4}-(0[1-9]|1[0-2])/, default: "", shareable: true },
//     region: { type: "enum", values: ["__all__", "EMEA", "AMER"], default: "__all__", shareable: true },
//     search: { type: "string", default: "" },          // draft text: kept in state, never in the URL
//   } satisfies ViewSchema;
//   const [view, patchView] = useViewState(VIEW);        // once, in the shell
//   patchView({ month: "2026-03" });                     // a filter change replaces the history entry
//   patchView({ screen: "orders" }, { history: "push" }); // navigation or Apply: Back undoes it
//
// Types are string, integer (`min`, `max`), boolean and enum (`values`). Only fields marked
// `shareable` enter the URL, as `#v=1&screen=orders&month=2026-03`: schema order, defaults left out.
// A shareable string needs a `pattern`, which must match the whole value, so free text never enters
// the URL. Anything read back that fails its field's rule — a bad value, a repeated key — is that
// field's default, and an unknown key is ignored, so a query only ever sees a valid value.
// `#/sage/...` is Sage's own: it is never rewritten on load, and while it is showing the view keeps
// its last applied state.
import { useCallback, useEffect, useRef, useState } from "react";

export type ViewField =
  | { type: "string"; default: string; pattern?: RegExp; shareable?: boolean }
  | { type: "integer"; default: number; min?: number; max?: number; shareable?: boolean }
  | { type: "boolean"; default: boolean; shareable?: boolean }
  | { type: "enum"; default: string; values: readonly string[]; shareable?: boolean };

export type ViewSchema = Record<string, ViewField>;

export type ViewState<S extends ViewSchema> = { -readonly [K in keyof S]: S[K]["default"] };

export type PatchView<S extends ViewSchema> = (
  values: Partial<ViewState<S>>,
  options?: { history?: "replace" | "push" },
) => void;

type Value = string | number | boolean;

const VERSION = "1";
const RESERVED = "#/sage/";
const TYPES = new Set(["string", "integer", "boolean", "enum"]);

function check(schema: ViewSchema): void {
  for (const [key, field] of Object.entries(schema)) {
    if (key === "v" || !field || !TYPES.has(field.type)) {
      throw new Error(`useViewState: field '${key}' needs a type of string, integer, boolean or enum.`);
    }
    if (field.shareable && field.type === "string" && typeof field.pattern?.exec !== "function") {
      throw new Error(`useViewState: shareable string field '${key}' needs a pattern; free text never goes in the URL.`);
    }
  }
}

function whole(pattern: RegExp, value: string): boolean {
  pattern.lastIndex = 0;
  const match = pattern.exec(value);
  return !!match && match.index === 0 && match[0].length === value.length;
}

// The value itself when it is valid for the field, else undefined.
function accept(field: ViewField, value: unknown): Value | undefined {
  switch (field.type) {
    case "string":
      return typeof value === "string" && (!field.pattern || whole(field.pattern, value)) ? value : undefined;
    case "integer":
      return typeof value === "number" && Number.isSafeInteger(value) &&
        (field.min == null || value >= field.min) && (field.max == null || value <= field.max)
        ? value : undefined;
    case "boolean":
      return typeof value === "boolean" ? value : undefined;
    case "enum":
      return typeof value === "string" && field.values.includes(value) ? value : undefined;
  }
}

function decode(field: ViewField, raw: string): Value | undefined {
  if (field.type === "integer") return /^-?\d+$/.test(raw) ? Number(raw) : undefined;
  if (field.type === "boolean") return raw === "true" ? true : raw === "false" ? false : undefined;
  return raw;
}

function defaults(schema: ViewSchema): Record<string, Value> {
  return Object.fromEntries(Object.entries(schema).map(([key, field]) => [key, field.default]));
}

// The shareable fields a fragment says, or null for a fragment that is not this hook's to read:
// Sage's reserved routes, and another grammar's hash an app had before it opted in.
function fromHash(schema: ViewSchema, hash: string): Record<string, Value> | null {
  const text = hash.startsWith("#") ? hash.slice(1) : hash;
  const params = new URLSearchParams(text);
  if (text && (hash.startsWith(RESERVED) || !params.has("v"))) return null;
  const ours = params.getAll("v").length === 1 && params.get("v") === VERSION;
  const out: Record<string, Value> = {};
  for (const [key, field] of Object.entries(schema)) {
    if (!field.shareable) continue;
    const raw = ours ? params.getAll(key) : [];
    const value = raw.length === 1 ? accept(field, decode(field, raw[0])) : undefined;
    out[key] = value === undefined ? field.default : value;
  }
  return out;
}

function toHash(schema: ViewSchema, state: Record<string, Value>): string {
  const params = new URLSearchParams();
  for (const [key, field] of Object.entries(schema)) {
    if (field.shareable && !Object.is(state[key], field.default)) params.append(key, String(state[key]));
  }
  const text = params.toString();
  return text ? `#v=${VERSION}&${text}` : "";
}

export function useViewState<S extends ViewSchema>(schema: S): [ViewState<S>, PatchView<S>] {
  check(schema);
  const [state, setState] = useState<Record<string, Value>>(
    () => ({ ...defaults(schema), ...fromHash(schema, window.location.hash) }));
  const latest = useRef(state);
  latest.current = state;

  const commit = useCallback((next: Record<string, Value>) => {
    latest.current = next;
    setState(next);
  }, []);

  const patch = useCallback<PatchView<S>>((values, options) => {
    const current = latest.current;
    const next = { ...current };
    let changed = false;
    for (const [key, raw] of Object.entries(values)) {
      const field = Object.prototype.hasOwnProperty.call(schema, key) ? schema[key] : undefined;
      const value = field ? accept(field, raw) : undefined;
      if (value === undefined || Object.is(value, current[key])) continue;
      next[key] = value;
      changed = true;
    }
    if (!changed) return;
    commit(next);
    const { location, history } = window;
    const hash = toHash(schema, next);
    if (hash === location.hash) return;
    const url = location.pathname + location.search + hash;
    if (options?.history === "push") history.pushState(history.state, "", url);
    else history.replaceState(history.state, "", url);
  }, [schema, commit]);

  // Back, Forward and a hand-edited address. These only read: a write here would loop.
  useEffect(() => {
    const sync = () => {
      const parsed = fromHash(schema, window.location.hash);
      if (!parsed) return;
      const current = latest.current;
      if (Object.keys(parsed).some((key) => !Object.is(parsed[key], current[key]))) {
        commit({ ...current, ...parsed });
      }
    };
    window.addEventListener("hashchange", sync);
    window.addEventListener("popstate", sync);
    return () => {
      window.removeEventListener("hashchange", sync);
      window.removeEventListener("popstate", sync);
    };
  }, [schema, commit]);

  return [state as ViewState<S>, patch];
}
