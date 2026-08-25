window.SW = window.SW || {};

(function () {
  const { createElement: h } = React;
  const { Result, Button } = antd;

  SW.GalleryMode = function GalleryMode() {
    return h(Result, {
      status: 'info',
      title: 'Gallery is not in this slice',
      subTitle: 'Finding apps other people built is later work.',
      extra: h(Button, { type: 'primary', onClick: () => SW.router.go('#/chat') }, 'Go to Chat'),
    });
  };
})();
