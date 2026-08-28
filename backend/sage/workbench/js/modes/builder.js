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
    const { apps, activeApp, touched, previewStatus } = SW.store.get();

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

    // What state the app the header names is really in. `Running` is the Gallery's word for a
    // deployed App and is deliberately not borrowed: a Built App is not deployed, and the Gallery
    // suppresses the word even on the tiles where it is true — "a card that says Running on every
    // tile teaches nothing" (`modes/gallery.js:101`). A published App's deployment status is a
    // different fact about a different object and arrives with publish (#70).
    //
    // `idle` says nothing, because it is first paint before the probe has landed and the header
    // would be inventing the answer it exists to report.
    //
    // `ok` says nothing either, and that is the same rule rather than an exception to it.
    // `probePreview()` runs from `loadBuild()` and `refreshPreview()` only, and the polling in
    // `PreviewPane` stops the moment the status leaves `starting` — so nothing re-reads a preview
    // once it is live. A process that dies mid-session leaves `ok` behind it and the word becomes a
    // claim nobody is checking. The two states left are the two the canvas cannot show you: a pane
    // that is blank because it is still coming up, and one that is blank because it failed. A live
    // preview is its own evidence, and the Gallery precedent this word already answers to says the
    // rest — "a card that says Running on every tile teaches nothing".
    //
    // Re-probing on a heartbeat was the alternative. It buys a truthful `ok` and pays for it twice:
    // `probePreview` re-stamps `previewSrc` with a new cache-buster, which reloads the iframe under
    // whoever is using it, and it maps a thrown fetch to `starting`, so one blip on a live preview
    // would replace this stale word with a false one. Silence costs nothing and cannot go stale.
    const previewWord = {
      starting: 'Starting preview…',
      err: 'Preview didn’t start',
      // Nothing answered and Build stopped waiting (#90). Its own word, because it is its own
      // fact: `err` is the preview answering with something bad, this is it never answering.
      stalled: 'Preview never came up',
    }[previewStatus];

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
      // A build the person walked away from goes on running (#77). The app list already says so
      // on the row, but that row is behind a click — the app in the preview is the one nobody
      // should have to open a menu to ask about.
      activeApp && activeApp.building &&
        h(
          'span',
          { className: 'sw-build-state is-building' },
          h(LoadingOutlined, { spin: true }),
          'Building…'
        ),
      // Somebody else has pushed to this app (#78). Beside the build state rather than instead
      // of it, on the row's precedent: the two are about different people and both still hold.
      // The row says it too, but that row is behind a click.
      activeApp && activeApp.behind &&
        h('span', { className: 'sw-build-state is-behind' }, 'Changes to pull'),
      // The third producer, and the only one that is about the process rather than the app: your
      // turn can be writing files WHILE the preview restarts to show them, so this is said beside
      // the two above rather than instead of either.
      activeApp && previewWord && h('span', { className: 'sw-build-state' }, previewWord),
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

  // What the selected app ships (#92), in the row #87 reserved for it.
  //
  // The reason the layout could not wait is the problem #85 was filed about. The composer's chips
  // are Session context, which belongs to the Conversation and must not follow the selected app
  // (#84, `CONTEXT.md:176-177`). Flush against an app-scoped pane with nothing between them, a
  // Conversation-scoped row reads as the app's — somebody saw `market-data-eod` under a news app
  // and took it for something that app uses. Two rows, two scopes, and this one says whose it is.
  //
  // `ships`, not `uses`. What is read here is the DECLARED record — the Bindings someone picked
  // and the files someone attached, both written per app, both re-read by `loadBuild` when the
  // control above changes app. Whether the app's code actually calls any of it is the derived
  // answer, which ADR-0010 keeps advisory and off any surface that has to gate: a Binding made two
  // minutes ago, before the agent wrote its first query, is used by nothing and still publishes.
  // The usage label that would say so is #93, and the scan behind it walks the whole app tree.
  //
  // Two names rather than one umbrella (#85 Q3): `.sage/bindings.json` is read at run time by the
  // published app's own server to decide what a query may touch, `.sage/attachments.json` at
  // deploy time to rebuild `public/data/`. Different consumers, different moments.
  function AppScopeRow() {
    const { activeApp, bindings, appAttachments } = SW.store.get();
    // A row headed by no app is a row about nothing, and a Project with none is the one screen
    // whose only job is `New app`.
    if (!activeApp) return null;

    const bound = (bindings || []).map((b) => b.display_name || b.name || b.id);
    // Two Datasets can each hold a `margins.csv`, and the row has room for the leaf name only. So
    // the strip shows the name and the tooltip carries the path that tells the two apart — without
    // it the row would print the same word twice and offer nothing that says why.
    const paths = (appAttachments || []).map((a) => a.path || a.file || '');
    const files = paths.map((p) => p.split('/').pop());

    // Read-only, and said so rather than left to be discovered: a row that reports a Binding and
    // says nothing about where it is dealt with is the same dead end the empty state below avoids.
    // The pointer is where the kind is LISTED, not a second Remove — unbind and detach each report
    // the app source that still uses what just went, and a second copy of either here would be a
    // second guard to keep in step with the first.
    const kindRow = (label, names, where, full) =>
      h(
        Tooltip,
        { key: label, title: `${(full || names).join(', ')} · ${where}` },
        h(
          'span',
          { className: 'sw-app-scope-kind' },
          h('span', { className: 'sw-app-scope-kind-label' }, label),
          h('span', { className: 'sw-app-scope-names' }, names.join(', '))
        )
      );

    return h(
      'div',
      { className: 'sw-app-scope' },
      h('span', { className: 'sw-app-scope-label' }, `${activeApp.name} ships`),
      // Always here, including for a brand-new app with neither: hiding the row would make the
      // header jump the moment the first Binding lands, and would teach a first-time creator
      // nothing at the one moment they have not seen either word yet.
      // Named the way the panel's own empty state names it, and for the reason it does: the
      // handoff is what actually fills BOTH lists. `_promote_chat_file` turns a Dataset chip into
      // an app Attachment and `_bind_from_handoff` records the Bindings, so a sentence telling a
      // first-timer to attach a file in Build would send them to the composer's upload — which
      // writes scratch and a Conversation chip, and leaves this row still saying nothing yet.
      bound.length === 0 && files.length === 0
        ? h(
            'span',
            { className: 'sw-app-scope-empty' },
            'nothing yet. Resources and data files from Chat land here after Open Builder.'
          )
        // A kind with nothing in it is not the same state, so it is not named. `Attachments —`
        // over an empty list says the app ships a kind of thing it does not.
        : [
            bound.length > 0 && kindRow('Bindings', bound,
              'listed in Project resources, under “In this app”'),
            files.length > 0 && kindRow('Attachments', files,
              'listed in Project resources, with the app’s files', paths),
          ]
    );
  }

  function PreviewPane({ resumed }) {
    const { previewSrc, previewStatus } = SW.store.get();
    const starting = previewStatus === 'starting';
    const failed = previewStatus === 'err';
    const stalled = previewStatus === 'stalled';

    useEffect(() => {
      if (previewStatus !== 'starting') return undefined;
      const id = setInterval(() => SW.store.refreshPreview(), 1500);
      // Giving up used to stop the polling and leave the status alone, so the overlay went on
      // saying `Starting preview…` with nothing behind it checking (#90). It says so now.
      const stop = setTimeout(() => {
        clearInterval(id);
        SW.store.previewGaveUp();
      }, 90000);
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
        // It opens the LOCAL preview. Unqualified, it did not say which of the two doors it is,
        // and it becomes actively misleading the moment `Open app` lands beside it (#89, behind
        // #70). It names its destination now, before the second door arrives.
        h(
          Tooltip,
          { title: 'Open preview in a new tab' },
          h(Button, {
            size: 'small',
            icon: h(ExportOutlined, null),
            'aria-label': 'Open preview in a new tab',
            onClick: () => window.open('./preview/', '_blank'),
          })
        )
      ),
      // Beneath the row that names the app, because it is about the app that row names.
      h(AppScopeRow, null),
      h(
        'div',
        { className: 'sw-builder-canvas is-live' },
        (starting || failed) &&
          h(
            'div',
            { className: 'sw-preview-overlay' },
            starting ? 'Starting preview…' : 'Preview didn’t start — click reload to retry.'
          ),
        // The way out is a button here rather than the toolbar's Reload (#90). Reload is an
        // icon-only control at the other end of the row, and the person this overlay is written
        // for has just been told the thing they were waiting for is not coming — sending them
        // hunting for the fix is the part that made it a dead end.
        stalled &&
          h(
            'div',
            { className: 'sw-preview-overlay is-stalled' },
            h('div', { className: 'sw-preview-overlay-text' },
              'Nothing answered on the preview port for 90 seconds, so Sage stopped '
              + 'checking. A first build installs dependencies and can take longer than that.'),
            h(
              Button,
              {
                size: 'small',
                type: 'primary',
                style: { marginTop: 12 },
                onClick: () => SW.store.refreshPreview(),
              },
              'Check again'
            )
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
