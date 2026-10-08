// The app's first screen. Replace the placeholder below with the requested screen.
//
// A plain script, like every file under `static/components/`: it adds what it defines to
// `window.app`, and `static/app.js` mounts it. `static/index.html` loads it above `static/app.js`.
(function () {
  const { createElement: h } = React;
  window.app = window.app || {};

  function MainScreen() {
    return h('main', { className: 'sage-placeholder' },
      h('h1', null, 'Your app will appear here'));
  }

  window.app.MainScreen = MainScreen;
})();
