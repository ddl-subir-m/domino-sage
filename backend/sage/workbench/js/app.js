window.SW = window.SW || {};

(function () {
  const { createElement: h, useState, useEffect } = React;
  const { ConfigProvider, App: AntApp, Result, Button, Spin } = antd;

  // Modes that render inside the shell but are not themselves mode tabs.
  const SUBROUTES = { plan: 'chat' };

  function useStore() {
    const [, force] = useState(0);
    useEffect(() => SW.store.subscribe(() => force((n) => n + 1)), []);
    return SW.store.get();
  }

  function useRoute() {
    const [route, setRoute] = useState(SW.router.get());
    useEffect(() => SW.router.subscribe(setRoute), []);
    return route;
  }

  function useShortcuts() {
    useEffect(() => {
      const onKey = (e) => {
        const meta = e.metaKey || e.ctrlKey;
        const typing = /^(INPUT|TEXTAREA)$/.test(e.target.tagName) || e.target.isContentEditable;

        if (meta && e.key.toLowerCase() === 'k') {
          e.preventDefault();
          SW.store.set({ paletteOpen: true });
        } else if (meta && e.key.toLowerCase() === 'p') {
          e.preventDefault();
          SW.store.set({ scopePickerOpen: true });
        } else if (meta && e.key === '/') {
          e.preventDefault();
          SW.store.toggleDock('resources');
        } else if (meta && e.shiftKey && e.key.toLowerCase() === 'n') {
          e.preventDefault();
          SW.store.newThread().then((thread) => SW.router.go(`#/chat/${thread.id}`));
        } else if (e.key === 'Escape' && !typing) {
          const { previewResourceId, handoffPlanId, paletteOpen, inviteOpen, graduationOpen, helpOpen } =
            SW.store.get();
          if (paletteOpen) SW.store.set({ paletteOpen: false });
          else if (previewResourceId) SW.store.set({ previewResourceId: null });
          else if (handoffPlanId) SW.store.set({ handoffPlanId: null });
          else if (graduationOpen) SW.store.set({ graduationOpen: false });
          else if (inviteOpen) SW.store.set({ inviteOpen: false });
          else if (helpOpen) SW.store.set({ helpOpen: false });
        }
      };
      window.addEventListener('keydown', onKey);
      return () => window.removeEventListener('keydown', onKey);
    }, []);
  }

  function Routes({ route }) {
    switch (route.mode) {
      case 'chat':
        return h(SW.ChatMode, { threadId: route.a });
      // Both modes are rooted on the conversation, so both carry it the same
      // way. Which app Build has in the preview is a view parameter, because
      // one conversation can change several.
      case 'build':
        return h(SW.BuildMode, { conversationId: route.a, appId: route.query.app || null });
      case 'code':
        return h(SW.CodeMode, null);
      // Manage exists at two levels: across everything, and inside the current
      // project. The level is the first segment. App usage is the landing view,
      // so anything that is not explicitly cost lands there — including older
      // #/manage/apps links.
      case 'manage':
        return h(SW.ManageMode, {
          level: route.a === 'project' ? 'project' : 'org',
          tab: route.a === 'cost' || route.b === 'cost' ? 'cost' : 'apps',
        });
      case 'gallery':
        return h(SW.GalleryMode, { appId: route.a });
      case 'plan':
        return h(SW.PlanPage, { planId: route.a, autoReview: route.query.review === '1' });
      default:
        return h(Result, {
          status: '404',
          title: 'Nothing here',
          subTitle: `"${route.path}" is not a page in this workspace.`,
          extra: h(Button, { type: 'primary', onClick: () => SW.router.go('#/chat') }, 'Go to Chat'),
        });
    }
  }

  function Root() {
    const state = useStore();
    const route = useRoute();
    const [error, setError] = useState(null);
    useShortcuts();

    useEffect(() => {
      SW.store.init().catch((err) => setError(err));
    }, []);

    if (error) {
      return h(Result, {
        status: 'error',
        title: 'The workspace could not load',
        subTitle: String(error.message || error),
        extra: h(Button, { type: 'primary', onClick: () => window.location.reload() }, 'Reload'),
      });
    }

    if (!state.ready) {
      return h(
        'div',
        { className: 'sw-boot' },
        h(Spin, { size: 'large' }),
        h('div', { className: 'sw-boot-label' }, 'Loading your workspace…')
      );
    }

    const shellMode = SUBROUTES[route.mode] || route.mode;
    return h(SW.Shell, { mode: shellMode, route }, h(Routes, { route }));
  }

  const root = ReactDOM.createRoot(document.getElementById('root'));
  root.render(
    h(
      ConfigProvider,
      { theme: SW.theme },
      h(AntApp, null, h(Root, null))
    )
  );
})();
