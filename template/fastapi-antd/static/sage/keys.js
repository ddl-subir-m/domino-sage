/* Your keys (#644): where a viewer of this App sets their own value for a key the App's code reads
 * with `secret("NAME")`. Sage-owned; `sage_serve.py` adds it to every page. Do not edit.
 *
 * A small button, shown only when the App reads a key, opens the panel; so does `#/sage/keys`.
 * Values go to the App's server once and are never read back: the server seals them into a cookie
 * this page cannot read, and answers only whether each one is set.
 */
(function () {
  var base = window.__SAGE_BASE__ || "";
  var panel = null;
  var button = null;

  function el(tag, attrs, children) {
    var node = document.createElement(tag);
    Object.keys(attrs || {}).forEach(function (k) {
      if (k === "style") node.style.cssText = attrs[k];
      else if (k.slice(0, 2) === "on") node.addEventListener(k.slice(2), attrs[k]);
      else node.setAttribute(k, attrs[k]);
    });
    (children || []).forEach(function (c) {
      node.appendChild(typeof c === "string" ? document.createTextNode(c) : c);
    });
    return node;
  }

  function request(method, path, body) {
    return fetch(path, {
      method: method,
      credentials: "same-origin",
      headers: body ? { "Content-Type": "application/json" } : {},
      body: body ? JSON.stringify(body) : undefined,
    }).then(function (res) {
      return res.json().catch(function () { return {}; }).then(function (data) {
        return { ok: res.ok, status: res.status, data: data };
      });
    });
  }

  function load() {
    return request("GET", "sage/keys");
  }

  function close() {
    if (panel) panel.remove();
    panel = null;
    if (location.hash === "#/sage/keys") history.replaceState(null, "", location.pathname + location.search);
  }

  function render(answer) {
    if (panel) panel.remove();
    var rows = Array.isArray(answer.data) ? answer.data : [];
    var body = [];
    if (!answer.ok) {
      body.push(el("p", { style: "color:#C20A29" }, [answer.data.error || "Your keys could not be loaded."]));
    } else if (!rows.length) {
      body.push(el("p", {}, ["This App reads no keys."]));
    }
    rows.forEach(function (row) {
      var input = el("input", { type: "password", autocomplete: "off", placeholder: row.set ? "Replace your key" : "Your key",
        "aria-label": row.name, style: "flex:1;padding:4px 8px;border:1px solid #D1D1D6;border-radius:4px" });
      var status = el("span", { style: "font-size:12px;color:#65657B" }, [row.set ? "Using your key" : "Using the builder's key"]);
      var error = el("div", { style: "font-size:12px;color:#C20A29" });
      function done(res) {
        if (!res.ok) { error.textContent = res.data.error || "That did not save."; return; }
        load().then(render);
      }
      var save = el("button", { type: "button", onclick: function () {
        if (!input.value) return;
        request("POST", "sage/keys", { name: row.name, value: input.value, base: base }).then(done);
        input.value = "";
      } }, ["Save"]);
      var actions = [input, save];
      if (row.set) {
        actions.push(el("button", { type: "button", onclick: function () {
          request("DELETE", "sage/keys/" + encodeURIComponent(row.name) + "?base=" + encodeURIComponent(base)).then(done);
        } }, ["Clear"]));
      }
      body.push(el("div", { style: "margin:12px 0" }, [
        el("div", {}, [el("code", {}, [row.name]), " ", status]),
        row.note ? el("div", { style: "font-size:12px;color:#65657B" }, [row.note]) : "",
        el("div", { style: "display:flex;gap:8px;margin-top:4px" }, actions),
        error,
      ]));
    });
    panel = el("div", { role: "dialog", "aria-label": "Your keys",
      style: "position:fixed;right:16px;bottom:56px;z-index:10000;width:360px;max-height:70vh;overflow:auto;" +
        "background:#fff;border:1px solid #D1D1D6;border-radius:8px;padding:16px;" +
        "box-shadow:0 4px 16px rgba(0,0,0,.15);font:14px Inter,sans-serif;color:#2E2E38" }, [
      el("div", { style: "display:flex;justify-content:space-between;align-items:center" }, [
        el("strong", {}, ["Your keys"]),
        el("button", { type: "button", "aria-label": "Close", onclick: close }, ["\u00d7"]),
      ]),
      el("p", { style: "font-size:12px;color:#65657B" }, [
        "The App uses its builder's key when you have not set one. Yours is kept in an encrypted " +
        "cookie in this browser, for this App only, and is never shown again.",
      ]),
    ].concat(body));
    document.body.appendChild(panel);
  }

  function open() {
    load().then(render);
  }

  function start() {
    load().then(function (answer) {
      var reads = answer.status === 503 || (answer.ok && Array.isArray(answer.data) && answer.data.length);
      if (!reads) return;
      button = el("button", { type: "button", "aria-label": "Your keys", onclick: function () { panel ? close() : open(); },
        style: "position:fixed;right:16px;bottom:16px;z-index:10000;padding:4px 12px;border-radius:16px;" +
          "border:1px solid #D1D1D6;background:#fff;font:12px Inter,sans-serif;color:#2E2E38;cursor:pointer" },
        ["Your keys"]);
      document.body.appendChild(button);
      if (location.hash === "#/sage/keys") render(answer);
    });
    window.addEventListener("hashchange", function () {
      if (location.hash === "#/sage/keys") open();
    });
  }

  if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", start);
  else start();
})();
