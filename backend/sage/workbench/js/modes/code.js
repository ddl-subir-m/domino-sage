window.SW = window.SW || {};

(function () {
  const { createElement: h } = React;
  const { Result } = antd;

  SW.CodeMode = function CodeMode() {
    return h(Result, {
      status: 'info',
      title: 'Code is on a parallel branch',
      subTitle: 'This tab will land when that work merges. Chat and Build are the modes that work here.',
    });
  };
})();
