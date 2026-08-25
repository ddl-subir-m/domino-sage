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
})();
