window.SW = window.SW || {};

(function () {
  const { createElement: h, useState, useEffect, useRef, Fragment } = React;
  const { Button, Tooltip, Input, Dropdown, Modal, Checkbox, Alert } = antd;
  const {
    ReloadOutlined, ExportOutlined, SearchOutlined, MoreOutlined, PlusOutlined, DownOutlined,
    LoadingOutlined, HistoryOutlined,
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
      title: SW.brand.text('Rename {builtApp}'),
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
        h('div', null, SW.brand.text('This app’s code, its plan and its Bindings are removed and '
          + 'can’t be recovered. Your other {builtAppPlural} and this conversation stay.')),
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
              SW.brand.text('Also delete the published {platformName} App')
            ),
            h(
              'div',
              { className: 'sw-caption', style: { marginTop: 4, marginLeft: 24 } },
              SW.brand.text('Leave this and its URL goes on serving the version you last '
                + 'published — but {assistantName} can’t update or delete it after this, so you’d '
                + 'do that in {platformName}.')
            )
          )
      ),
      onOk: () =>
        SW.store
          .deleteApp(app.id, { deleteDominoApp: alsoDelete })
          .then((out) => {
            if (out.dominoApp === 'deleted') {
              antd.message.success(
                SW.brand.text('Deleted “{name}” and its {platformName} App.', { name: app.name })
              );
            } else if (out.dominoApp === 'running') {
              // The one outcome worth saying out loud, and worth holding on screen: the Domino App
              // is still costing a container and serving a URL, Sage is no longer the thing that
              // can stop it, and the person has somewhere to go if that is not what they wanted.
              antd.message.warning({
                content: SW.brand.text(
                  'Deleted “{name}”. Its {platformName} App is still running — delete it in '
                    + '{platformName} if you don’t want it, because {assistantName} can no longer '
                    + 'reach it.',
                  { name: app.name }
                ),
                duration: 10,
              });
            } else {
              antd.message.success(`Deleted “${app.name}”.`);
            }
          })
          .catch((err) => {
            // Held open rather than closed on the failure: the app is still there, and the answer
            // to a control plane that refused may well be to delete it without the deployment.
            antd.message.warning(
              err.message || SW.brand.text('{assistantName} could not delete this {builtApp}.')
            );
            return Promise.reject(err);
          }),
    });
  }

  // The pre-publish notice (#35), the surface `GET /api/publish-check` has been answering to nobody
  // since #26. Two things a creator should read before shipping, and neither is a refusal:
  //
  // - the named queries the published app will not answer, which is a defect they can go and fix;
  // - where this app's data goes, which is a consequence they accept or do not (ADR-0012).
  //
  // Silence means silence. A read that failed renders NOTHING — no "Sage could not check where your
  // data goes", no spinner left behind. That is the deliberate asymmetry with `publish_problems`,
  // which refuses when it cannot check: an unverified credential is a hole, an unwritten notice is
  // not, and a sentence about Sage's own reachability costs attention and buys nothing.
  function publishNotice(queries, egress) {
    if (!queries.length && !egress) return null;
    return h(
      Fragment,
      null,
      // Warning, because these queries WILL fail for a viewer. Info below it, because nothing about
      // the model is broken — it is what publishing means, said once, so the choice is knowing.
      queries.length
        ? h(Alert, {
          type: 'warning',
          showIcon: true,
          style: { marginTop: 12 },
          message: queries.length === 1
            ? 'One of this app’s queries won’t answer once it’s published'
            : `${queries.length} of this app’s queries won’t answer once it’s published`,
          // The app's OWN sentences, which is the property #26 was built on: the creator reads what
          // the viewer would read, so what they go and fix is the thing that will complain.
          description: h(
            'ul',
            { className: 'sw-publish-warnings' },
            queries.map((q, i) => h('li', { key: i }, q))
          ),
        })
        : null,
      egress
        ? h(Alert, {
          type: 'info',
          showIcon: true,
          style: { marginTop: 12 },
          message: 'Where this app’s data goes',
          description: egress,
        })
        : null
    );
  }

  // Publishing is consequential, so it confirms on Reset's and Delete's pattern (#76): say what it
  // does, and say in the same breath what it does NOT touch. The fear on the way to this button is
  // that the whole Project goes out, or that shipping the app moves the files and the conversation
  // it was built from — neither happens, and a confirm is the only place anybody is reading.
  //
  // Two sentences for the two states of one act, because the creator's question is different on
  // each side of the first publish: before it, "where does this end up"; after it, "does the link
  // I already sent people change". The control above stays one control (#86) — it is the sentence
  // that moves, not the word on the item.
  //
  // The name is QUOTED for the reason Reset quotes it: a display name starts as the title of the
  // plan the app was built from, and those end in a full stop, which unquoted lands one in the
  // middle of this question.
  function publishApp(app) {
    const again = !!app.published;
    const body = (notice) => h(
      Fragment,
      null,
      h(
        'div',
        null,
        SW.brand.text(
          again
            ? 'The {platformName} App you published before starts serving this app’s latest code. '
              + 'The URL doesn’t change, so anybody already holding it sees the new version.'
            : 'This app’s code is saved and deployed on {platformName} as an App with a URL of its '
              + 'own. Who can open it is set in {platformName}, so nobody sees it until you '
              + 'share it.'
        )
      ),
      // Deliberately silent about the attached files. `public/data/` is gitignored, so the push
      // does not carry the bytes and the deployed app rehydrates them from the manifest — which
      // makes "your files are published" and "your files stay here" both wrong, and a confirm is
      // the last place to be approximately right. What publishing does to data is its own
      // question with its own answer to find.
      h(
        'div',
        { style: { marginTop: 12 } },
        SW.brand.text(
          'Only this app goes out. Your other {builtAppPlural} and this conversation stay '
            + 'where they are.'
        )
      ),
      // LAST, so nothing above it moves when it arrives. The confirm's own explanation is what a
      // creator starts reading, and a notice inserted over it would shift the paragraph under their
      // eyes for the one case in which they are being asked to read carefully.
      notice
    );
    const instance = Modal.confirm({
      title: again ? `Publish a new version of “${app.name}”?` : `Publish “${app.name}”?`,
      okText: again ? 'Publish new version' : 'Publish',
      cancelText: 'Cancel',
      content: body(null),
      onOk: () =>
        SW.store
          // The app this confirm NAMED, not whichever one is selected when it is answered. The
          // request carries no app id, so the store refuses if the selection moved underneath.
          .publishApp(app)
          .then((out) => {
            // A deploy is not done when the call answers — Domino is still bringing the container
            // up. Saying so is what stops the first click on `Open app` reading as a broken app.
            antd.message.success(
              out && out.republished
                ? `Published a new version of “${app.name}”. It takes a few minutes to serve the new code.`
                : `Published “${app.name}”. It takes a few minutes to come up — Open app opens it.`
            );
          })
          .catch((err) => {
            // Held open rather than closed on the failure, on Delete's precedent: nothing was
            // published, the app is exactly as it was, and the answer to a refusal is usually to
            // read it and press this again.
            antd.message.warning(
              err.message || SW.brand.text('{assistantName} could not publish this {builtApp}.')
            );
            // Unless the app it named is no longer the selected one. Then the question itself is
            // void — pressing again would only earn the same sentence — so the modal goes and the
            // creator is left looking at the app they actually moved to.
            if (err && err.moved) return undefined;
            return Promise.reject(err);
          }),
    });
    // The notice FILLS IN, after the confirm is already on screen. Awaiting a network read before
    // the modal opened would leave the click looking like a control that did nothing, and one of
    // these two reads can be as slow as the gateway is.
    //
    // Through the instance's `update` rather than a component nested in `content`: `Modal.confirm`
    // renders its config once, outside this tree's render cycle, so a nested component has no state
    // change to re-render on. `body` is rebuilt whole because `update` replaces the config value.
    //
    // TWO independent handlers, never one `Promise.all`. Awaiting both would put the query warnings
    // — already sitting on the workspace disk, and answered in microseconds — behind a gateway read
    // that can be slow or never answer at all, which is the exact coupling the second route exists
    // to prevent. Each read renders what it knows the moment it knows it, and the second one
    // updates over the first.
    //
    // The reads are swallowed on failure and the modal keeps whatever it has — see `publishNotice`.
    // Nothing here can reach `onOk`, so a read still in flight when somebody presses Publish delays
    // nothing and blocks nothing. Answer the confirm before they land and `update` renders into a
    // container antd has already detached: invisible, and not worth a flag that no test could tell
    // from the real thing.
    let queries = [];
    let egress = '';
    const fill = () => {
      const notice = publishNotice(queries, egress);
      if (notice) instance.update({ content: body(notice) });
    };
    SW.api.publishCheck().then((r) => { queries = (r && r.queries) || []; fill(); }, () => {});
    SW.api.publishEgress().then((r) => { egress = (r && r.notice) || ''; fill(); }, () => {});
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
                  placeholder: SW.brand.text('Search {builtAppPlural}'),
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
              h('div', { className: 'sw-rail-group sw-group-label' },
                SW.brand.text('{builtAppPlural} in this Project')),
              filtered.length === 0
                ? h('div', { className: 'sw-rail-empty sw-secondary' },
                    SW.brand.text('No {builtAppPlural} match "{query}".', { query }))
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
                SW.brand.text(
                  'Each {builtApp} has its own code, plan and Resources. Building one leaves '
                    + 'the rest alone.'
                )
              )
          ),
      },
      h(
        'button',
        {
          className: 'sw-app-picker',
          type: 'button',
          'aria-label': SW.brand.text('{builtApp} — {name}', {
            name: activeApp ? activeApp.name : 'choose one',
          }),
          // The panel is rows with actions on them, not options in a listbox, and a reader that is
          // told listbox waits for a selection model this does not have.
          'aria-haspopup': true,
          'aria-expanded': open,
        },
        // The one place the no-article-engine rule cannot be honoured here: the copy is pinned by
        // `test_build_keeps_the_conversation_rail.py`, which this batch does not own. A pack whose
        // {builtApp} starts with a vowel reads "Choose a Archive" until that reword lands with it.
        h('span', { className: 'sw-app-picker-name' },
          activeApp ? activeApp.name : SW.brand.text('Choose a {builtApp}')),
        h(DownOutlined, { style: { fontSize: 9 } })
      )
    );
  }

  // The Build header. It names the app the preview is showing, and it is where the app is chosen
  // now that the rail lists Conversations in both modes.
  function AppBar({ resumed }) {
    const { apps, activeApp, touched, previewStatus, buildRunning } = SW.store.get();

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
          SW.brand.text('No {builtAppPlural} yet. Start one with New app, or approve a plan '
            + 'in Chat.')
        ),
        newApp
      );
    }

    // Rename and Delete land here on Reset's precedent (#38): text-labelled items in an overflow,
    // Delete danger-styled and last below a divider. Not a per-row `…` inside the picker, which
    // would be a menu inside a menu — and beside the app the header names there is no ambiguity
    // about which app they act on, which is what the per-row `…` was solving.
    //
    // Publish and Open app join them on the same shape (#89), above Rename because both are about
    // the App outside in Domino while the two below are about the app in here — its name, and
    // whether it stays. Delete keeps the three things Reset's shape is made of: danger, last, and
    // below the divider.
    const appMenu = activeApp && {
      items: [
        // ONE control, not a Rebuild/Update pair (#86): describing a change in the composer is
        // what rebuilds an app, so a second control under a second word would be a second way to
        // do one thing. The first publish and every one after are the same act on the same object
        // — what differs is what the creator already has out there, which the confirm says and
        // the control does not have to.
        //
        // Refused mid-build, and it says why rather than going quiet. Publishing commits and
        // pushes the working tree, and a turn writing files into it has not finished writing them:
        // this would commit half an edit and merge on top of it. The server refuses it too, with
        // the same sentence and for the same reason — this is the half that says so before the
        // click, which is the shape the composer's Reset already has.
        //
        // Two signals, because one build is visible two ways and neither sees the other. `building`
        // is the row's, so it survives a reload and catches a turn another tab started in THIS app.
        // `buildRunning` is this tab's, and it is the one that catches the hazard the row cannot
        // name: the commit is the PROJECT's, so a turn streaming into another app is equally in the
        // way, and that app's row is not the one being read here.
        {
          key: 'publish',
          label: activeApp.building || buildRunning
            ? 'Publish — wait for this build to finish' : 'Publish',
          disabled: !!(activeApp.building || buildRunning),
        },
        // A second door, never the first one wearing another word: the control in the toolbar
        // opens the LOCAL preview and this opens the deployed App. Two items, two labels, two
        // destinations — one button that changed where it went would be unreadable in exactly
        // the moment the difference matters.
        //
        // Unpublished, it stays and says why. Vanishing would leave the creator to work out on
        // their own that there is nothing to open yet, which is the dead end Reset's disabled
        // label avoids in the composer.
        {
          key: 'open',
          label: activeApp.published ? 'Open app' : 'Open app — publish it first',
          disabled: !activeApp.published,
        },
        { key: 'rename', label: 'Rename' },
        { type: 'divider' },
        { key: 'delete', label: 'Delete', danger: true },
      ],
      onClick: ({ key, domEvent }) => {
        domEvent.stopPropagation();
        if (key === 'publish') publishApp(activeApp);
        // A new tab, so the Build you published from is still behind it — Gallery's cards open
        // the same way for the same reason. The URL arrives host-relative and the browser resolves
        // it against this page's origin, which is the host Domino serves the App's page from.
        // Opened as given: nothing here builds it, which is why nothing here knows its grammar.
        if (key === 'open' && activeApp.url) window.open(activeApp.url, '_blank', 'noopener');
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
  // control above changes app. Whether the app's code actually calls a Binding is the DERIVED
  // answer, and it rides in beside the record as `used` (#93): a written answer off the disk, not
  // a scan this row could ever run. `_scan_app_sources` walks the whole app tree, and this redraws
  // on every app switch.
  //
  // It marks the exception and nothing else. ADR-0010 keeps the derived answer advisory and off
  // every gate — a Binding made two minutes ago, before the agent wrote its first query, is used
  // by nothing and still publishes — so the mark is a word beside a name, never a warning and
  // never a control. `used === false` is the scan having looked; `undefined`/`null` is no turn
  // having left an answer for this app, which draws no mark rather than calling everything unused.
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
    // Which of them the last build turn found nothing calling. Positional, against `bound`, so the
    // mark cannot drift onto the name beside it.
    const unused = (bindings || []).map((b) => b.used === false);
    // Two Datasets can each hold a `margins.csv`, and the row has room for the leaf name only. So
    // the strip shows the name and the tooltip carries the path that tells the two apart — without
    // it the row would print the same word twice and offer nothing that says why.
    const paths = (appAttachments || []).map((a) => a.path || a.file || '');
    const files = paths.map((p) => p.split('/').pop());

    // Read-only, and said so rather than left to be discovered: a row that reports a Binding and
    // says nothing about where it is dealt with is the same dead end the empty state below avoids.
    // The pointer is where the kind is DEALT WITH, not a second Remove — unbind and detach each
    // report the app source that still uses what just went, and a second copy of either here would
    // be a second guard to keep in step with the first.
    //
    // It names the destination by the head the reader will actually see there and names the action,
    // because the destination can now act (#96). It used to say "listed in", a read-only word, and
    // the Attachments one pointed at a group that did not exist.
    const pointer = `in Project resources, under ${activeApp.name} — remove it there`;

    // One span per name, so one of them can carry a mark the others do not. The separator sits
    // INSIDE the span that follows it rather than between spans: `sw-app-scope-names` truncates the
    // run with an ellipsis, and a comma of its own would be the thing left dangling at the cut.
    const nameNodes = (names, marks) =>
      names.map((name, i) =>
        h(
          'span',
          { key: `${name}-${i}`, className: 'sw-app-scope-name' },
          i > 0 ? ', ' : '',
          name,
          (marks || [])[i] && h('span', { className: 'sw-app-scope-unused' }, ' (not used)')
        )
      );

    const kindRow = (label, names, where, full, marks) =>
      h(
        Tooltip,
        {
          key: label,
          // The marks go in the tooltip too, because the strip truncates and the tooltip is where a
          // narrow preview's reader finds out which name the mark was on. The clause after them is
          // what two words beside a name cannot say: what looked, when, and that nothing is blocked
          // by the answer (ADR-0010). It sits BEFORE the pointer, which stays last in both kinds'
          // tooltips because it is the only half the reader can act on.
          title:
            `${(full || names).map((n, i) => ((marks || [])[i] ? `${n} (not used)` : n)).join(', ')}`
            + ((marks || []).some(Boolean)
              ? ' · “not used” is what this app’s source said at the last build, and it publishes either way'
              : '')
            + ` · ${where}`,
        },
        h(
          'span',
          { className: 'sw-app-scope-kind' },
          h('span', { className: 'sw-app-scope-kind-label' }, label),
          h('span', { className: 'sw-app-scope-names' }, nameNodes(names, marks))
        )
      );

    return h(
      'div',
      { className: 'sw-app-scope' },
      h('span', { className: 'sw-app-scope-label' }, `${activeApp.name} ships`),
      // Always here, including for a brand-new app with neither: hiding the row would make the
      // header jump the moment the first Binding lands, and would teach a first-time creator
      // nothing at the one moment they have not seen either word yet.
      // One sentence, written once in `SW.util` and said by the panel's section too — the two
      // surfaces answer the same question and had drifted into two answers to it (ADR-0011).
      bound.length === 0 && files.length === 0
        ? h(
            'span',
            { className: 'sw-app-scope-empty' },
            SW.util.appScopeEmpty('nothing yet.')
          )
        // A kind with nothing in it is not the same state, so it is not named. `Attachments —`
        // over an empty list says the app ships a kind of thing it does not. The panel does name
        // it, because a destination someone arrived at intending to act is not a glance.
        : [
            bound.length > 0 && kindRow('Bindings', bound, pointer, null, unused),
            // No marks for the files. Whether the source uses an ATTACHMENT is `_data_usage`'s
            // question, which detach and delete ask live because they refuse on the answer — a
            // different scanner for a different job, and #85 named it in place of this one.
            files.length > 0 && kindRow('Attachments', files, pointer, paths),
          ]
    );
  }

  function PreviewPane({ resumed }) {
    const { previewSrc, previewStatus, activeApp } = SW.store.get();
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
        // Where #86's proposal put a History TAB, and doing the same job without the cost that
        // refused the Plan tab: what it opens overlays the preview instead of replacing it, so the
        // builds stay readable against the app they built (#88).
        //
        // Labelled rather than icon-only. The two controls to its right are about the preview and
        // are recognisable from their icons; "the app's build log" is not a thing an icon says, and
        // an icon-only control here would be a tooltip standing in for a name.
        //
        // Hidden with no app rather than disabled: a Project with nothing built has no builds to
        // list, and the header already says so in words beside `New app`.
        activeApp &&
          h(
            Button,
            {
              size: 'small',
              icon: h(HistoryOutlined, null),
              'aria-label': 'Build history',
              onClick: () => SW.store.openBuildHistory(),
            },
            'Build history'
          ),
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
        // It opens the LOCAL preview; `Open app`, in the header's `…`, opens the deployed App.
        // Two controls, two labels, two destinations — never one button that changes where it
        // goes. Unqualified, this one turned actively misleading the moment the second door
        // landed beside it (#89), which is why it was named before that happened.
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
              SW.brand.text('Nothing answered on the preview port for 90 seconds, so '
                + '{assistantName} stopped checking. A first build installs dependencies and can '
                + 'take longer than that.')),
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
    const { thread, activeApp, buildMessages, buildTranscript, buildTyping, buildRunning, turnWedged,
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

    // A deep link naming an app SEEDS the selection, once, and the server holds it from there
    // (#100). `selectApp` WRITES — it moves the per-Project selection every other tab reads and
    // reloads the whole of Build — so an effect that re-asserted `?app=` whenever `activeApp`
    // drifted was not holding a view, it was overwriting the app the other tab is looking at. Two
    // tabs naming different apps traded the selection back and forth every 30 seconds, because
    // each one's poll saw the other's write as drift. Fires on the app the URL NAMES changing,
    // never on the app the server has: the same shape as the resolution effect below, for the same
    // reason it gives.
    //
    // Picking an app still goes through the route (see `SW.appRoute`), and that is a change to
    // `appId`, so the click is honoured exactly as before.
    const followed = useRef(null);
    useEffect(() => {
      // A rewrite this tab made to follow the server is not somebody asking for an app. Selecting
      // on it would put the selection back where it had just come from.
      if (!appId || appId === followed.current) return;
      SW.store.selectApp(appId);
    }, [appId]);

    // The other half of it, and not optional: server-wins on its own picks a winner and leaves the
    // address bar naming the loser. When the selection moves under this tab, the URL moves with it.
    // `replaceState` rather than a push, because following somebody else's selection is not a place
    // the Back button should be able to return to.
    //
    // Only when the URL NAMES an app that is not the one on screen. A link naming none disagrees
    // with nothing, and pinning one into it would take the resolution below away from the next
    // person to open it.
    useEffect(() => {
      const shown = activeApp && activeApp.id;
      if (!appId || !shown || shown === appId) return;
      // `SW.appRoute` names the conversation off the STORE's thread. While the route names one that
      // is still opening, the grammar would name the conversation being left and this would send
      // the tab back to it.
      if ((thread ? thread.id : null) !== (conversationId || null)) return;
      followed.current = shown;
      SW.router.replace(SW.appRoute(activeApp));
    }, [appId, activeApp && activeApp.id, conversationId, thread && thread.id]);

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
                // Open while a turn runs: a second change queues behind it now, and the queued row
                // the composer draws is where it says so (#79). A wedged workspace is the one that
                // still refuses, because that lock never frees.
                disabled: turnWedged,
                showMode: true,
                compact: true,
              })
            )
          ),
          h(PreviewPane, { resumed }),
          // store.openPlanArtifact() already routes Build to the sheet rather than the plan page;
          // without this it set planViewerId and nothing appeared. Beside the preview, not over it,
          // which is where Chat puts the same sheet.
          h(SW.PlanSheet, null),
          // OVER the preview, not beside it, which is the opposite of the sheet above and for the
          // opposite reason (#88). The sheet is checked against the app while you keep building;
          // the history is read, then closed. It overlays so the preview is never displaced to
          // reach it, and it is mounted here — inside Build — so it goes when Build does.
          h(SW.BuildHistoryDrawer, null)
        )
      )
    );
  };
})();
