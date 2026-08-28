window.SW = window.SW || {};

(function () {
  const { createElement: h, useState, useEffect, useRef, Fragment } = React;
  const { Button, Tooltip, Input, Dropdown, Modal, Checkbox } = antd;
  const {
    ReloadOutlined, ExportOutlined, SearchOutlined, MoreOutlined, PlusOutlined, DownOutlined,
    LoadingOutlined,
  } = icons;

  // One rail, both modes (#82). It used to swap its contents — Threads in Chat, Built Apps here —
  // so crossing into Build took your history off screen while the transcript beside it claimed to
  // be one Conversation. What you switch between in Build is still the app; that moved to the
  // header, beside the preview it controls.
  function Rail() {
    const { railHidden } = SW.store.get();
    if (railHidden) return h(SW.ConversationRail, { mode: 'build' });
    return h('div', { className: 'sw-rail' }, h(SW.ConversationRail, { mode: 'build' }));
  }

  function renameApp(app) {
    let value = app.name;
    Modal.confirm({
      title: 'Rename Built App',
      // The id is the app's directory and a published App's entry point is fixed at creation, so
      // it is deliberately shown and deliberately not editable.
      content: h(
        Fragment,
        null,
        h(Input, {
          defaultValue: app.name,
          'aria-label': 'Name',
          onChange: (e) => {
            value = e.target.value;
          },
        }),
        h('div', { className: 'sw-caption', style: { marginTop: 8 } }, `ID ${app.id} — can't change`)
      ),
      okText: 'Rename',
      onOk: () => {
        // Rejecting holds the modal open, which on its own reads as a dead button — antd shows
        // nothing for a rejected onOk. Say what is wrong, then hold it open.
        if (!value.trim()) {
          antd.message.warning('Give it a name.');
          return Promise.reject(new Error('empty name'));
        }
        return SW.store.renameApp(app.id, value.trim());
      },
    });
  }

  // Delete takes the app away; Reset — in the composer, a different word in a different place —
  // empties it and keeps it. The copy carries that difference rather than leaning on the labels:
  // "removed and can't be recovered" is the whole reason the two must never be mistaken (#76).
  //
  // A published app is offered its Domino App, UNCHECKED. Both answers are irreversible, so the one
  // that arrives by not reading is the one that destroys less: a Domino App still serving can be
  // deleted later, and one already deleted cannot come back. It is also what makes the offer an
  // offer — the criterion is that Sage deletes the App "if accepted", and a box already ticked is
  // not something anybody accepted. The cost of the safe answer is a Domino App Sage can no longer
  // reach, so that outcome is said out loud afterwards rather than defaulted around.
  function deleteApp(app) {
    let alsoDelete = false;
    Modal.confirm({
      title: `Delete “${app.name}”?`,
      okText: 'Delete app',
      okButtonProps: { danger: true },
      cancelText: 'Cancel',
      content: h(
        Fragment,
        null,
        h('div', null, 'This app’s code, its plan and its Bindings are removed and can’t be '
          + 'recovered. Your other Built Apps and this conversation stay.'),
        app.published &&
          h(
            'div',
            { style: { marginTop: 12 } },
            h(
              Checkbox,
              {
                onChange: (e) => {
                  alsoDelete = e.target.checked;
                },
              },
              'Also delete the published Domino App'
            ),
            h(
              'div',
              { className: 'sw-caption', style: { marginTop: 4, marginLeft: 24 } },
              'Leave this and its URL goes on serving the version you last published — but Sage '
                + 'can’t update or delete it after this, so you’d do that in Domino.'
            )
          )
      ),
      onOk: () =>
        SW.store
          .deleteApp(app.id, { deleteDominoApp: alsoDelete })
          .then((out) => {
            if (out.dominoApp === 'deleted') {
              antd.message.success(`Deleted “${app.name}” and its Domino App.`);
            } else if (out.dominoApp === 'running') {
              // The one outcome worth saying out loud, and worth holding on screen: the Domino App
              // is still costing a container and serving a URL, Sage is no longer the thing that
              // can stop it, and the person has somewhere to go if that is not what they wanted.
              antd.message.warning({
                content: `Deleted “${app.name}”. Its Domino App is still running — delete it in `
                  + 'Domino if you don’t want it, because Sage can no longer reach it.',
                duration: 10,
              });
            } else {
              antd.message.success(`Deleted “${app.name}”.`);
            }
          })
          .catch((err) => {
            // Held open rather than closed on the failure: the app is still there, and the answer
            // to a control plane that refused may well be to delete it without the deployment.
            antd.message.warning(err.message || 'Sage could not delete this Built App.');
            return Promise.reject(err);
          }),
    });
  }

  // A row of the header's app list — the rail's row, unchanged apart from where it lives. It keeps
  // its per-app facts because they are the reason this control is a list rather than a line: a
  // selector naming only the app you already have open would throw away every badge below.
  function AppRow({ app, active, onPick }) {
    return h(
      'div',
      {
        className: `sw-thread${active ? ' is-active' : ''}`,
        // What a click acts on, said in the markup rather than only in the closure, so the row is
        // findable without mounting it.
        'data-app': app.id,
        onClick: onPick,
        role: 'button',
      },
      // The id is on hover rather than in the row. Two apps can carry the same name and only the
      // id tells them apart, but it is 25 characters of hex nobody reads on the way past — in the
      // row it pushed the name it was there to qualify into second place.
      h(
        Tooltip,
        {
          title: [
            app.building ? 'A build is running in this app.' : '',
            // Said in words as well as shown as a badge: the badge is what you notice from across
            // the list, and this is what tells you what it means the first time you see one.
            app.behind ? 'Somebody else has pushed changes to this app.' : '',
            `ID ${app.id}`,
          ].filter(Boolean).join(' '),
          placement: 'right',
          mouseEnterDelay: 0.5,
        },
        h(
          'div',
          { className: 'sw-thread-main' },
          h('div', { className: 'sw-thread-title' }, app.name),
          h(
            'div',
            { className: 'sw-thread-meta' },
            // A build the person walked away from goes on running (#77), so the row it is running
            // in says so. It replaces the built/not-built line rather than sitting beside it: what
            // an app is mid-build is the more useful of the two, and the other one comes back the
            // moment the turn ends.
            app.building
              ? h('span', { className: 'sw-thread-building' }, h(LoadingOutlined, { spin: true }), 'Building…')
              : app.built ? 'Built' : 'Not built yet',
            // Somebody else has pushed to this app (#78). It sits beside the build state rather
            // than replacing it, because the two are about different people and both still hold:
            // your build is running AND their work is waiting.
            app.behind && h('span', { className: 'sw-thread-behind' }, 'Changes to pull')
          )
        )
      )
    );
  }

  // The list behind the header's app name. A searchable dropdown of the rail's rows rather than a
  // bare select, for one reason: #77's `Building…` and #78's `Changes to pull` are worth seeing
  // across the whole list without a click, and a one-line selector silently destroys both.
  function AppPicker({ apps, activeApp }) {
    const [open, setOpen] = useState(false);
    const [query, setQuery] = useState('');

    const needle = query.trim().toLowerCase();
    const filtered = apps.filter((a) => !needle || a.name.toLowerCase().includes(needle));

    const pick = (app) => {
      setOpen(false);
      SW.router.go(SW.appRoute(app));
    };

    return h(
      Dropdown,
      {
        trigger: ['click'],
        open,
        // Closing forgets the search. A filter that outlives the panel it was typed into comes
        // back as a list with apps missing from it, and nothing on screen saying why.
        onOpenChange: (next) => {
          setOpen(next);
          if (!next) setQuery('');
        },
        placement: 'bottomLeft',
        dropdownRender: () =>
          h(
            'div',
            { className: 'sw-app-picker-panel' },
            // Also while a search is live: deleting down to one app would otherwise hide the box
            // that holds the filter, leaving the last app filtered out and unreachable.
            (apps.length > 1 || needle) &&
              h(
                'div',
                { className: 'sw-rail-search' },
                h(Input, {
                  size: 'small',
                  prefix: h(SearchOutlined, { style: { color: '#8F8FA3' } }),
                  placeholder: 'Search Built Apps',
                  value: query,
                  allowClear: true,
                  onChange: (e) => setQuery(e.target.value),
                })
              ),
            h(
              'div',
              { className: 'sw-app-picker-list sw-scroll' },
              // The label the rail's head carried. It says what this list is, which is also what
              // the button above it is naming one of.
              h('div', { className: 'sw-rail-group sw-group-label' }, 'Built Apps in this Project'),
              filtered.length === 0
                ? h('div', { className: 'sw-rail-empty sw-secondary' }, `No Built Apps match "${query}".`)
                : filtered.map((app) =>
                    h(AppRow, {
                      key: app.id,
                      app,
                      active: !!activeApp && activeApp.id === app.id,
                      onPick: () => pick(app),
                    })
                  )
            ),
            // Teaching that only makes sense beside a list of several, so it is only there then.
            apps.length > 1 &&
              h(
                'div',
                { className: 'sw-rail-note' },
                'Each Built App has its own code, plan and Resources. Building one leaves the rest alone.'
              )
          ),
      },
      h(
        'button',
        {
          className: 'sw-app-picker',
          type: 'button',
          'aria-label': `Built App — ${activeApp ? activeApp.name : 'choose one'}`,
          // The panel is rows with actions on them, not options in a listbox, and a reader that is
          // told listbox waits for a selection model this does not have.
          'aria-haspopup': true,
          'aria-expanded': open,
        },
        h('span', { className: 'sw-app-picker-name' }, activeApp ? activeApp.name : 'Choose a Built App'),
        h(DownOutlined, { style: { fontSize: 9 } })
      )
    );
  }

  // The Build header. It names the app the preview is showing, and it is where the app is chosen
  // now that the rail lists Conversations in both modes.
  function AppBar({ resumed }) {
    const { apps, activeApp, touched } = SW.store.get();

    const newApp = h(
      Button,
      {
        size: 'small',
        // The one clear action of a Project with nothing in it, and an ordinary control once there
        // is something to build on — by then the composer is what the screen is for.
        type: apps.length ? 'default' : 'primary',
        icon: h(PlusOutlined, null),
        onClick: () => SW.store.createApp(),
      },
      'New app'
    );

    // Where the app name would be, rather than inside the control that lists apps: somebody with
    // no apps has no reason to open an app picker, so guidance hidden in one is not reachable by
    // the person it is written for.
    if (!apps.length) {
      return h(
        Fragment,
        null,
        h(
          'span',
          { className: 'sw-caption' },
          'No Built Apps yet. Start one with New app, or approve a plan in Chat.'
        ),
        newApp
      );
    }

    // Rename and Delete land here on Reset's precedent (#38): text-labelled items in an overflow,
    // Delete danger-styled and last below a divider. Not a per-row `…` inside the picker, which
    // would be a menu inside a menu — and beside the app the header names there is no ambiguity
    // about which app they act on, which is what the per-row `…` was solving.
    const appMenu = activeApp && {
      items: [
        { key: 'rename', label: 'Rename' },
        { type: 'divider' },
        { key: 'delete', label: 'Delete', danger: true },
      ],
      onClick: ({ key, domEvent }) => {
        domEvent.stopPropagation();
        if (key === 'rename') renameApp(activeApp);
        if (key === 'delete') deleteApp(activeApp);
      },
    };

    // What else this Conversation changed. One Conversation can drive several apps (ADR-0008) and
    // the preview holds one, so the count is the header's answer to "where did the rest of my work
    // go" — the rail's tags are where it goes back to.
    // "Other" is other than the one the header names, so with nothing named there is no count to
    // give: every touched app would be counted as an other, against an app picker reading
    // "Choose a Built App".
    const others = activeApp
      ? (touched || []).filter((t) => t.appId !== activeApp.id)
      : [];

    return h(
      Fragment,
      null,
      h(AppPicker, { apps, activeApp }),
      activeApp &&
        h(
          Dropdown,
          { menu: appMenu, trigger: ['click'], placement: 'bottomRight' },
          h(
            Tooltip,
            { title: `Actions for ${activeApp.name}` },
            h('button', {
              className: 'sw-icon-btn is-dark-text',
              'aria-label': `Actions for ${activeApp.name}`,
            }, h(MoreOutlined, null))
          )
        ),
      // Qualifies the name rather than the toolbar, so it stays beside the name (#77).
      resumed && h('span', { className: 'sw-caption' }, '· built earlier'),
      newApp,
      others.length > 0 &&
        h(
          Tooltip,
          { title: `Also changed here: ${others.map((t) => t.appName).join(', ')}` },
          h(
            'span',
            { className: 'sw-caption sw-build-others' },
            `${others.length} other app${others.length === 1 ? '' : 's'} changed here`
          )
        )
    );
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
        // The header names the app rather than the pane. "Preview" was only ever worth a word
        // while it was unqualified, and it never was the question: with several apps in a Project,
        // WHICH one is on screen is. Naming it and choosing it are now the same control (#82).
        h(AppBar, { resumed }),
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
    const { thread, activeApp, buildMessages, buildTranscript, buildTyping, buildRunning,
            projectPlan } = SW.store.get();
    const scroller = useRef(null);

    // The only thing keeping app state fresh, and it moved here with the rail it used to live in
    // (#82). The badge is the point of the check being a background one (#78): a teammate's push
    // has to reach the screen without anyone opening an app to find out. The server does the
    // fetching on its own schedule and this only re-reads the answer, so the interval is cheap.
    useEffect(() => {
      SW.store.loadApps();
      const id = setInterval(() => SW.store.loadApps(), 30000);
      return () => clearInterval(id);
    }, []);

    // A deep link naming an app is a request to be looking at that one. Only when it differs from
    // what the server is already pointed at: selecting reloads the whole of Build, and doing that
    // on every render would be a loop.
    useEffect(() => {
      if (appId && (!activeApp || activeApp.id !== appId)) SW.store.selectApp(appId);
    }, [appId, activeApp && activeApp.id]);

    // A link naming NO app still names one: the Built App this conversation bound last. Resolving
    // it is what keeps an older link landing where it landed, rather than on whichever app the
    // server happens to have selected. Once per conversation — `activeApp` is deliberately not a
    // dependency, because selecting the resolved app would re-run this and ask again.
    useEffect(() => {
      if (appId || !conversationId) return;
      SW.store
        .resolveConversationApp(conversationId)
        .then((bound) => bound && SW.store.selectApp(bound))
        .catch(() => {});
    }, [appId, conversationId]);

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
    }, [buildTranscript.length, buildTyping]);

    // The orientation's question, which is NOT "is this pane empty". It asks whether THIS app has
    // turns in this conversation, and since #74 a brand-new Built App can be started inside a
    // conversation full of talk — that app has none, so the orientation fires, and there it is
    // right: the person needs telling what to do in the app they just made. What it must not do is
    // pretend the conversation never happened, which is why it sits under the transcript below
    // rather than in place of it. Under the split view there is no transcript to sit under, and
    // this is the screen Build has always drawn.
    const noAppTurns = buildMessages.length === 0 && !buildTyping;
    // A new conversation clears the transcript while the preview keeps serving the app the rail
    // has selected. That is the truth, but unlabelled it reads as this conversation's work, so say
    // whose app it is. (The preview following the app a build is running in is #77.)
    const resumed = noAppTurns && !!projectPlan && projectPlan.status === 'built';

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
              buildTranscript.map((message) => h(SW.Message, { key: message.id, message })),
              noAppTurns &&
                h(
                  'div',
                  { className: 'sw-build-greeting' },
                  h('div', { className: 'sw-empty-title' }, 'Build the app from a plan'),
                  h(
                    'div',
                    { className: 'sw-empty-detail' },
                    // Pointing at the rail is only worth saying while the conversation is somewhere
                    // else. With it on screen above, the thing worth saying is whose turns those
                    // are and why this app has none of them.
                    buildTranscript.length
                      ? 'Approve a plan to write this app, or describe a change. The turns above are this conversation — this app has none of them yet.'
                      : 'Approve a plan to write the app, or describe a change. This conversation stays in the rail — Chat is one click away.'
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
                ),
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
                // Named, because the header names it. "this app" and a header saying which one are
                // two voices on the same screen, and only one of them answers the question.
                placeholder: activeApp
                  ? `Describe a change to ${activeApp.name}…`
                  : 'Describe a change, or ask about this app…',
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
