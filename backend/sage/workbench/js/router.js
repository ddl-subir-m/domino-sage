window.SW = window.SW || {};

(function () {
  const listeners = new Set();

  function parse() {
    const raw = (window.location.hash || '#/chat').replace(/^#\/?/, '');
    const [pathPart, queryPart] = raw.split('?');
    const segments = pathPart.split('/').filter(Boolean);
    const query = {};
    (queryPart || '').split('&').filter(Boolean).forEach((pair) => {
      const [key, value] = pair.split('=');
      query[decodeURIComponent(key)] = decodeURIComponent(value || '');
    });
    return {
      mode: segments[0] || 'chat',
      a: segments[1] || null,
      b: segments[2] || null,
      query,
      path: `#/${segments.join('/')}`,
    };
  }

  let current = parse();

  function handleChange() {
    current = parse();
    listeners.forEach((fn) => fn(current));
  }

  window.addEventListener('hashchange', handleChange);

  SW.router = {
    get: () => current,
    subscribe(fn) {
      listeners.add(fn);
      return () => listeners.delete(fn);
    },
    go(path) {
      const next = path.startsWith('#') ? path : `#${path}`;
      if (window.location.hash === next) {
        handleChange();
      } else {
        window.location.hash = next;
      }
    },
    replace(path) {
      const next = path.startsWith('#') ? path : `#${path}`;
      window.history.replaceState(null, '', next);
      handleChange();
    },
  };

  // Build's route grammar. It was housed in the Build rail until the rail stopped listing apps
  // (#82), but it never belonged to it: `store.js` calls it after a delete and after a handoff, and
  // the transcript's app card calls it too. One grammar, beside the router that reads it.
  //
  // Picking an app goes through the ROUTE, never straight to the store. Build selects whatever
  // `?app=` names WHEN IT CHANGES (see BuildMode), so a click that only told the store would leave
  // the route naming the app nobody is looking at any more. One writer: the route says which app,
  // the store follows it.
  //
  // Only when it changes, since #100 — the URL seeds the selection and then follows the server,
  // rather than re-asserting itself over every other tab's choice.
  SW.appRoute = function appRoute(app) {
    const { thread } = SW.store.get();
    return `#/build${thread ? `/${thread.id}` : ''}?app=${app.id}`;
  };
})();
