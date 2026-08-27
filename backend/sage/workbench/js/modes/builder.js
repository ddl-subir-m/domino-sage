window.SW = window.SW || {};

(function () {
  const { createElement: h, useEffect, useRef } = React;
  const { Button, Tooltip } = antd;
  const { ReloadOutlined, ExportOutlined } = icons;

  // Build's rail lists Built Apps where Chat's lists Threads (ADR-0008). A Project holds many of
  // each, and in Build the thing you switch between is the app.
  function Rail() {
    const { railHidden } = SW.store.get();
    if (railHidden) return h(SW.AppRail, null);
    return h('div', { className: 'sw-rail' }, h(SW.AppRail, null));
  }

  function PreviewPane({ resumed }) {
    const { previewSrc, previewStatus, activeApp } = SW.store.get();
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
        // Unqualified, "Preview" reads as this conversation's work. It is one of the Project's
        // apps, so it is named: with several in the rail, which one is on screen is the question.
        h(
          'span',
          { className: 'sw-caption' },
          activeApp ? `Preview · ${activeApp.name}` : 'Preview',
          resumed && ' · built earlier'
        ),
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

  SW.BuildMode = function BuildMode({ conversationId, appId }) {
    const { thread, activeApp, buildMessages, buildTyping, buildRunning, projectPlan } = SW.store.get();
    const scroller = useRef(null);

    // A deep link naming an app is a request to be looking at that one. Only when it differs from
    // what the server is already pointed at: selecting reloads the whole of Build, and doing that
    // on every render would be a loop.
    useEffect(() => {
      if (appId && (!activeApp || activeApp.id !== appId)) SW.store.selectApp(appId);
    }, [appId, activeApp && activeApp.id]);

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
    // A new conversation clears the transcript while the preview keeps serving the app the rail
    // has selected. That is the truth, but unlabelled it reads as this conversation's work, so say
    // whose app it is. (The preview following the app a build is running in is #77.)
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
