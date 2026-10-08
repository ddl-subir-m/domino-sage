/* Shareable view state (#701): a dashboard's applied selection, kept in this page's own URL
 * fragment so a reload or a copied link reopens it. Sage-owned and refreshed from Sage's template;
 * do not edit.
 *
 *   const VIEW = {                                        // at top level, never inside a component
 *     screen: { type: "enum", values: ["revenue", "orders"], default: "revenue", shareable: true },
 *     month: { type: "string", pattern: /\d{4}-(0[1-9]|1[0-2])/, default: "", shareable: true },
 *     region: { type: "enum", values: ["__all__", "EMEA", "AMER"], default: "__all__", shareable: true },
 *     search: { type: "string", default: "" },          // draft text: kept in state, never in the URL
 *   };
 *   const [view, patchView] = sage.useViewState(VIEW);  // once, in the shell
 *   patchView({ month: "2026-03" });                     // a filter change replaces the history entry
 *   patchView({ screen: "orders" }, { history: "push" }); // navigation or Apply: Back undoes it
 *
 * Types are string, integer (`min`, `max`), boolean and enum (`values`). Only fields marked
 * `shareable` enter the URL, as `#v=1&screen=orders&month=2026-03`: schema order, defaults left
 * out. A shareable string needs a `pattern`, which must match the whole value, so free text never
 * enters the URL. Anything read back that fails its field's rule — a bad value, a repeated key — is
 * that field's default, and an unknown key is ignored, so a query only ever sees a valid value.
 * `#/sage/...` is Sage's own (the keys panel opens at `#/sage/keys`): it is never rewritten on load,
 * and while it is showing the view keeps its last applied state.
 */
(function () {
  var sage = (window.sage = window.sage || {});
  var VERSION = "1";
  var RESERVED = "#/sage/";
  var TYPES = { string: true, integer: true, boolean: true, enum: true };

  function check(schema) {
    Object.keys(schema).forEach(function (key) {
      var field = schema[key];
      if (key === "v" || !field || !TYPES[field.type]) {
        throw new Error("useViewState: field '" + key + "' needs a type of string, integer, boolean or enum.");
      }
      if (field.shareable && field.type === "string" && !(field.pattern && typeof field.pattern.exec === "function")) {
        throw new Error("useViewState: shareable string field '" + key + "' needs a pattern; free text never goes in the URL.");
      }
    });
  }

  function whole(pattern, value) {
    pattern.lastIndex = 0;
    var match = pattern.exec(value);
    return !!match && match.index === 0 && match[0].length === value.length;
  }

  // The value itself when it is valid for the field, else undefined.
  function accept(field, value) {
    if (field.type === "string") {
      return typeof value === "string" && (!field.pattern || whole(field.pattern, value)) ? value : undefined;
    }
    if (field.type === "integer") {
      var ok = Number.isSafeInteger(value) && (field.min == null || value >= field.min) &&
        (field.max == null || value <= field.max);
      return ok ? value : undefined;
    }
    if (field.type === "boolean") return typeof value === "boolean" ? value : undefined;
    return (field.values || []).indexOf(value) !== -1 ? value : undefined;
  }

  function decode(field, raw) {
    if (field.type === "integer") return /^-?\d+$/.test(raw) ? Number(raw) : undefined;
    if (field.type === "boolean") return raw === "true" ? true : raw === "false" ? false : undefined;
    return raw;
  }

  function defaults(schema) {
    var out = {};
    Object.keys(schema).forEach(function (key) { out[key] = schema[key].default; });
    return out;
  }

  // The shareable fields a fragment says, or null for a fragment that is not this hook's to read:
  // Sage's reserved routes, and another grammar's hash an app had before it opted in.
  function fromHash(schema, hash) {
    var text = hash.charAt(0) === "#" ? hash.slice(1) : hash;
    var params = new URLSearchParams(text);
    if (text && (hash.indexOf(RESERVED) === 0 || !params.has("v"))) return null;
    var ours = params.getAll("v").length === 1 && params.get("v") === VERSION;
    var out = {};
    Object.keys(schema).forEach(function (key) {
      var field = schema[key];
      if (!field.shareable) return;
      var raw = ours ? params.getAll(key) : [];
      var value = raw.length === 1 ? accept(field, decode(field, raw[0])) : undefined;
      out[key] = value === undefined ? field.default : value;
    });
    return out;
  }

  function toHash(schema, state) {
    var params = new URLSearchParams();
    Object.keys(schema).forEach(function (key) {
      var field = schema[key];
      if (field.shareable && !Object.is(state[key], field.default)) params.append(key, String(state[key]));
    });
    var text = params.toString();
    return text ? "#v=" + VERSION + "&" + text : "";
  }

  sage.useViewState = function useViewState(schema) {
    check(schema);
    var pair = React.useState(function () {
      return Object.assign(defaults(schema), fromHash(schema, window.location.hash));
    });
    var state = pair[0];
    var setState = pair[1];
    var latest = React.useRef(state);
    latest.current = state;

    function commit(next) {
      latest.current = next;
      setState(next);
    }

    var patch = React.useCallback(function (values, options) {
      var current = latest.current;
      var next = Object.assign({}, current);
      var changed = false;
      Object.keys(values || {}).forEach(function (key) {
        var field = Object.prototype.hasOwnProperty.call(schema, key) ? schema[key] : null;
        var value = field ? accept(field, values[key]) : undefined;
        if (value === undefined || Object.is(value, current[key])) return;
        next[key] = value;
        changed = true;
      });
      if (!changed) return;
      commit(next);
      var location = window.location;
      var hash = toHash(schema, next);
      if (hash === location.hash) return;
      var method = options && options.history === "push" ? "pushState" : "replaceState";
      window.history[method](window.history.state, "", location.pathname + location.search + hash);
    }, [schema]);

    // Back, Forward and a hand-edited address. These only read: a write here would loop.
    React.useEffect(function () {
      function sync() {
        var parsed = fromHash(schema, window.location.hash);
        if (!parsed) return;
        var current = latest.current;
        var differs = Object.keys(parsed).some(function (key) { return !Object.is(parsed[key], current[key]); });
        if (differs) commit(Object.assign({}, current, parsed));
      }
      window.addEventListener("hashchange", sync);
      window.addEventListener("popstate", sync);
      return function () {
        window.removeEventListener("hashchange", sync);
        window.removeEventListener("popstate", sync);
      };
    }, [schema]);

    return [state, patch];
  };
})();
