window.SW = window.SW || {};

(function () {
  const { createElement: h } = React;
  const { Result } = antd;

  SW.CodeMode = function CodeMode() {
    return h(Result, {
      status: 'info',
      title: "Code isn't available yet.",
      subTitle: 'Use Chat or Build.',
    });
  };
})();
