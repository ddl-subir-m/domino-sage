window.SW = window.SW || {};

(function () {
  const { createElement: h, useEffect, useRef } = React;
  const { Button, Tooltip } = antd;
  const { ReloadOutlined, ExportOutlined } = icons;

  function Rail() {
    const { railHidden } = SW.store.get();
    if (railHidden) return h(SW.ConversationRail, { mode: 'build' });
    return h('div', { className: 'sw-rail' }, h(SW.ConversationRail, { mode: 'build' }));
  }

  function PreviewPane() {
    const { previewSrc, previewStatus } = SW.store.get();
    const starting = previewStatus === 'starting';
    const failed = previewStatus === 'err';

    useEffect(() => {
      if (previewStatus !== 'starting') return undefined;
      const id = setInterval(() => SW.store.refreshPreview(), 1500);
      const stop = setTimeout(() => clearInterval(id), 90000);
      return () => {
        clearInterval(id);
        clearTimeout(stop);
      };
    }, [previewStatus]);

    return h(
      'div',
      { className: 'sw-builder-content' },
      h(
        'div',
        { className: 'sw-builder-toolbar' },
        h('span', { className: 'sw-caption' }, 'Preview'),
        h('span', { className: 'sw-topnav-spacer' }),
        h(
          Tooltip,
          { title: 'Reload preview' },
          h(Button, {
            size: 'small',
            icon: h(ReloadOutlined, null),
            'aria-label': 'Reload preview',
            onClick: () => SW.store.refreshPreview(),
          })
        ),
        h(
          Tooltip,
          { title: 'Open in a new tab' },
          h(Button, {
            size: 'small',
            icon: h(ExportOutlined, null),
            'aria-label': 'Open preview',
            onClick: () => window.open('./preview/', '_blank'),
          })
        )
      ),
      h(
        'div',
        { className: 'sw-builder-canvas is-live' },
        (starting || failed) &&
          h(
            'div',
            { className: 'sw-preview-overlay' },
            starting ? 'Starting preview…' : 'Preview didn’t start — click reload to retry.'
          ),
        h('iframe', {
          className: 'sw-preview-frame',
          title: 'App preview',
          src: previewSrc,
        })
      )
    );
  }

  SW.BuildMode = function BuildMode({ conversationId }) {
    const { thread, buildMessages, buildTyping, buildRunning } = SW.store.get();
    const scroller = useRef(null);

    useEffect(() => {
      if (conversationId && (!thread || thread.id !== conversationId)) {
        SW.store.openThread(conversationId).catch(() => {});
      }
    }, [conversationId]);

    useEffect(() => {
      SW.store.loadBuild();
      if (!SW.store.get().dockTab) SW.store.set({ dockTab: 'resources' });
    }, []);

    useEffect(() => {
      const el = scroller.current;
      if (el) el.scrollTop = el.scrollHeight;
    }, [buildMessages.length, buildTyping]);

    const empty = buildMessages.length === 0 && !buildTyping;

    return h(
      'div',
      { className: 'sw-build' },
      h(Rail, null),
      h(
        'div',
        { className: 'sw-builder' },
        h(
          'div',
          { className: 'sw-builder-body' },
          h(
            'div',
            { className: 'sw-builder-chat' },
            h(
              'div',
              { className: 'sw-builder-chat-messages sw-scroll', ref: scroller },
              empty
                ? h(
                    'div',
                    { className: 'sw-build-greeting' },
                    h('div', { className: 'sw-empty-title' }, 'Build the app from a plan'),
                    h(
                      'div',
                      { className: 'sw-empty-detail' },
                      'Approve a plan to write the app, or describe a change. This conversation stays in the rail — Chat is one click away.'
                    )
                  )
                : buildMessages.map((message) => h(SW.Message, { key: message.id, message })),
              buildTyping && h(SW.TypingIndicator, { label: buildTyping })
            ),
            h(
              'div',
              { className: 'sw-builder-chat-composer' },
              buildRunning &&
                h(
                  'div',
                  { className: 'sw-build-stop' },
                  h(
                    Button,
                    { size: 'small', danger: true, onClick: () => SW.store.stopBuild() },
                    'Stop'
                  )
                ),
              h(SW.Composer, {
                onSend: (text) => SW.store.sendBuildPrompt(text),
                placeholder: 'Describe a change, or ask about this app…',
                disabled: buildRunning,
                showMode: true,
                compact: true,
              })
            )
          ),
          h(PreviewPane, null)
        )
      )
    );
  };
})();
