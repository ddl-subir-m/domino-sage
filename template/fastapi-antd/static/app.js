// The app. Replace this file — it is the placeholder the starter ships with.
//
// Everything is on the page already: `React`, `ReactDOM`, `antd`, `icons`, `dayjs`, `Highcharts`,
// and Sage's helpers under `sage` (see AGENTS.md). There is no build step and no JSX: build elements
// with `React.createElement`, aliased to `h` below.
//
// Keep the two wrappers. `ConfigProvider` with `sage.theme` is what makes every Ant Design component
// draw in the Domino theme, and `sage.ErrorBoundary` is how a crash reaches the screen and reaches
// Sage instead of leaving a blank page.
(function () {
  const { createElement: h } = React;
  const { ConfigProvider } = antd;

  function App() {
    return h(ConfigProvider, { theme: sage.theme },
      h('main', { className: 'sage-placeholder' },
        h('h1', null, 'Your app will appear here')));
  }

  ReactDOM.createRoot(document.getElementById('root')).render(
    h(sage.ErrorBoundary, null, h(App)));
})();
