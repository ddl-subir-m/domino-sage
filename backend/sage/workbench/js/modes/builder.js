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

  function PreviewPane({ resumed }) {
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
        // Unqualified, "Preview" reads as this conversation's work. It is the Project's.
        h('span', { className: 'sw-caption' }, resumed ? 'Preview · built earlier' : 'Preview'),
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
    const { thread, buildMessages, buildTyping, buildRunning, projectPlan } = SW.store.get();
    const scroller = useRef(null);

    useEffect(() => {
      if (!conversationId) {
        // The route named no conversation, so this is a new one. Build's transcript is per
        // conversation now, so the old turns have to leave the screen with it.
        SW.store.clearConversation();
        return;
      }
      if (!thread || thread.id !== conversationId) {
        SW.store.openThread(conversationId).catch(() => {});
      }
    }, [conversationId]);

    // The transcript follows the open conversation rather than the mount. While the route names
    // one that is still opening, loading would replay the conversation we are leaving.
    const openId = thread ? thread.id : null;
    const opening = !!conversationId && openId !== conversationId;
    useEffect(() => {
      if (opening) return;
      SW.store.loadBuild();
      if (!SW.store.get().dockTab) SW.store.set({ dockTab: 'resources' });
    }, [openId, opening]);

    useEffect(() => {
      const el = scroller.current;
      if (el) el.scrollTop = el.scrollHeight;
    }, [buildMessages.length, buildTyping]);

    const empty = buildMessages.length === 0 && !buildTyping;
    // A Project holds one Built App today, so a new conversation clears the transcript while the
    // preview keeps serving the app already in the workspace. That is the truth, but unlabelled it
    // reads as this conversation's work. Say whose app it is until a Project can hold more than one
    // (ADR-0008, #67/#69/#77) and the preview can follow the app the conversation selected.
    const resumed = empty && !!projectPlan && projectPlan.status === 'built';

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
                    ),
                    resumed &&
                      h(
                        'div',
                        { className: 'sw-build-resume-note' },
                        h(
                          'div',
                          { className: 'sw-empty-title' },
                          'The preview is an app you already built'
                        ),
                        h(
                          'div',
                          { className: 'sw-empty-detail' },
                          'A new conversation clears the transcript, not the app. Describe a change to keep building on it.'
                        )
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
          h(PreviewPane, { resumed }),
          // store.openPlanArtifact() already routes Build to the sheet rather than the plan page;
          // without this it set planViewerId and nothing appeared. Beside the preview, not over it,
          // which is where Chat puts the same sheet.
          h(SW.PlanSheet, null)
        )
      )
    );
  };
})();
