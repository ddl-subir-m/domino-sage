window.SW = window.SW || {};

(function () {
  const listeners = new Set();

  // How many navigations have been emitted, counting the ones that did not change the hash.
  // `go` deliberately re-emits an identical hash (see below), but until this existed there was
  // nothing on the route object that SAID so: a mode keying its open on `[threadId]` saw the same
  // string and did not re-run, so clicking the row already in the hash could not retry an open
  // that lost its generation, and the row was inert from then on (#455).
  //
  // Read as an effect key, never as a value: what it means is "you were sent here again", and the
  // number itself says nothing about where. A mode that re-runs on it must be safe to re-run when
  // it is already where it is being sent — every one of them guards on that already.
  let nav = 0;

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
      nav,
    };
  }

  let current = parse();

  function handleChange() {
    nav += 1;
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
