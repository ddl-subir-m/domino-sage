window.SW = window.SW || {};

(function () {
  const { createElement: h } = React;
  const { Result } = antd;

  SW.ManageMode = function ManageMode() {
    return h(Result, {
      status: 'info',
      title: 'Manage is on a parallel branch',
      subTitle: 'Cost and app health will land here. Chat and Build are the modes that work in this slice.',
    });
  };
})();
