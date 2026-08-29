window.SW = window.SW || {};

(function () {
  const { createElement: h, useState, useEffect } = React;
  const { Result, Button, Spin, Tag, Empty } = antd;

  // Gallery is Built Apps — the things people published from a Sage Builder — not the chip's
  // Project list (#48). A Project with nothing published has nothing to show here, and an empty
  // Gallery stays empty rather than falling back to listing Projects.
  SW.GalleryMode = function GalleryMode({ appId }) {
    const [state, setState] = useState({ loading: true, items: [], error: null, provisioning: true });

    useEffect(() => {
      let live = true;
      SW.api.gallery()
        .then((d) => live && setState({
          loading: false,
          items: d.items || [],
          error: null,
          provisioning: d.provisioning !== false,
        }))
        .catch((err) => live && setState({
          loading: false,
          items: [],
          error: String((err && err.message) || err),
          provisioning: true,
        }));
      return () => { live = false; };
    }, []);

    // A new tab, so the Workbench you came from is still here — opening an app is not a move to
    // another Project.
    const open = (app) => window.open(app.url, '_blank', 'noopener');

    if (state.loading) {
      // Spin's own `tip` only renders in wrapper mode, so the line is its own element.
      return h('div', { className: 'sw-gallery-body', style: { textAlign: 'center', paddingTop: 64 } },
        h(Spin, null),
        h('p', { className: 'sw-secondary', style: { marginTop: 12 } }, 'Finding apps you can open…'));
    }

    if (state.error) {
      // Title and reason are ours and carry the pack's words; what came back is the platform's and
      // is quoted rather than retold (#121). Reload is the resolution step.
      return h(Result, {
        status: 'warning',
        title: SW.brand.text('{assistantName} couldn’t list the {builtAppPlural}'),
        subTitle: h(SW.PlatformError, {
          reason: SW.brand.text('{platformName} answered with an error.'),
          body: state.error,
          fix: SW.brand.text('Try again. If it keeps happening, check your access in {platformName}.'),
        }),
        extra: h(Button, { onClick: () => window.location.reload() }, 'Try again'),
      });
    }

    if (!state.items.length) {
      return h(Result, {
        icon: h(Empty, { image: Empty.PRESENTED_IMAGE_SIMPLE, description: null }),
        title: state.provisioning ? 'No Built Apps yet' : 'Gallery needs Domino',
        subTitle: state.provisioning
          ? 'Apps published from a Sage Builder show up here, for everyone who can open them. ' +
            'Build something in Chat, then publish it.'
          : 'This build runs outside Domino, so there are no published Apps to list.',
        extra: state.provisioning
          ? h(Button, { type: 'primary', onClick: () => SW.router.go('#/chat') }, 'Go to Chat')
          : null,
      });
    }

    return h(
      'div',
      { className: 'sw-gallery' },
      h(
        'div',
        { className: 'sw-gallery-main' },
        h(
          'div',
          { className: 'sw-gallery-head' },
          h('h2', { style: { margin: 0, fontSize: 20, fontWeight: 600 } }, 'Gallery'),
          h('p', { className: 'sw-secondary', style: { margin: '4px 0 0' } },
            `${state.items.length} ${state.items.length === 1 ? 'app' : 'apps'} you can open`)
        ),
        h(
          'div',
          { className: 'sw-gallery-body' },
          h(
            'div',
            { className: 'sw-gallery-grid' },
            state.items.map((app) =>
              h(
                'button',
                {
                  key: app.id,
                  className: 'sw-appcard',
                  style: appId === app.id ? { borderColor: 'var(--purple-500)' } : null,
                  onClick: () => open(app),
                },
                h(
                  'div',
                  { className: 'sw-appcard-body' },
                  h('span', { className: 'sw-appcard-name' }, app.name),
                  h(
                    'span',
                    { className: 'sw-appcard-meta' },
                    h('span', { className: 'sw-appcard-origin' }, app.project),
                    // Only when it isn't up: a card that says Running on every tile teaches nothing.
                    app.status && app.status.toLowerCase() !== 'running'
                      ? h(Tag, { color: 'default' }, app.status)
                      : null
                  )
                )
              )
            )
          )
        )
      )
    );
  };
})();
