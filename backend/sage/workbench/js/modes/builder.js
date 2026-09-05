window.SW = window.SW || {};

(function () {
  const { createElement: h, useState, useEffect, useRef, Fragment } = React;
  const { Button, Tooltip, Input, Dropdown, Modal, Checkbox, Alert } = antd;
  const {
    ExportOutlined, SearchOutlined, MoreOutlined, PlusOutlined, DownOutlined, LoadingOutlined,
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
        // "its record of what it needs to run" rather than "its Bindings" (ADR-0025), and a
        // *record* rather than the things themselves: deleting the app takes the grants, never the
        // Resources — those stay in the Project and can be picked again (ADR-0011).
        h('div', null, SW.brand.text('This app’s code, its plan and its record of what it needs '
          + 'to run are removed and can’t be recovered. Your other {builtAppPlural} and this '
          + 'conversation stay.')),
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
        SW.brand.text('Only this app goes out. Your other {builtAppPlural} and this conversation stay.')
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
  //
  // "New app" lives at the top of this panel rather than as its own button beside the picker: it is
  // the one other thing this control already knows how to do — put a different app on screen — and
  // a person reaching for "which app" is the person a blank one belongs in front of.
  function AppPicker({ apps, activeApp }) {
    const [open, setOpen] = useState(false);
    const [query, setQuery] = useState('');

    const needle = query.trim().toLowerCase();
    const filtered = apps.filter((a) => !needle || a.name.toLowerCase().includes(needle));

    const pick = (app) => {
      setOpen(false);
      // The rail follows the pick, so the two halves of the screen name the same app. Written HERE
      // and not in an effect on `activeApp`: the selection is per-Project on the server and the 30s
      // poll moves it under you, so an effect would let a second tab silently re-filter this tab's
      // rail. Only a person's own click may move the filter.
      SW.store.set({ railAppFilter: app.id });
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
            h(
              'button',
              {
                className: 'sw-app-picker-newapp',
                type: 'button',
                onClick: () => {
                  setOpen(false);
                  SW.store.createApp();
                },
              },
              h(PlusOutlined, null),
              'New app'
            ),
            h('div', { className: 'sw-app-picker-divider' }),
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
                SW.brand.text('{builtAppPlural} in this {project}')),
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
        // Plural, not "Choose a {builtApp}": there is no article engine, and a pack whose noun
        // starts with a vowel read "Choose a Archive". The picker only draws with apps in it, so
        // the plural is also true.
        h('span', { className: 'sw-app-picker-name' },
          activeApp ? activeApp.name : SW.brand.text('Choose from your {builtAppPlural}')),
        h(DownOutlined, { style: { fontSize: 9 } })
      )
    );
  }

  // The Build header. It names the app the preview is showing, and it is where the app is chosen
  // now that the rail lists Conversations in both modes.
  function AppBar({ resumed }) {
    const { apps, activeApp, touched, previewStatus } = SW.store.get();

    // Where the app name would be, rather than inside the control that lists apps: somebody with
    // no apps has no reason to open an app picker, so guidance hidden in one is not reachable by
    // the person it is written for. Apps.length > 0 reaches New app through the picker instead
    // (see `AppPicker`), which is why this button only exists here.
    if (!apps.length) {
      return h(
        Fragment,
        null,
        h(
          'span',
          { className: 'sw-caption' },
          SW.brand.text('No {builtAppPlural} yet. Start one with New app, or approve a plan '
            + 'in {chat}.')
        ),
        h(
          Button,
          {
            size: 'small',
            type: 'primary',
            icon: h(PlusOutlined, null),
            onClick: () => SW.store.createApp(),
          },
          'New app'
        )
      );
    }

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

  // The kinds the header's door offers (#141, #142). Data Sources joined the list when a Scope
  // stopped being the cascade position the creator was standing on: binding and scoping are two
  // acts now, so a picker row has everything the first one needs, and `ScopeDoor` below is the
  // second (ADR-0021). A control that cannot complete is the dead end this door exists to remove,
  // and the split is what made this one able to.
  //
  // A Model API IS here, and it is here deliberately. The panel's own door admitted LLM Aliases
  // only, on the ground that a Model API needs a credential, and this list never took that rule over
  // — then #144 took the panel's door away, so this is the only rule there is. Sage refuses to
  // record a Model API it holds no
  // demonstrated call for, and the refusal it sends is not a dead end — it is the instruction, in
  // the server's own words: open the model's Overview page in Domino, copy the sample request, paste
  // it. `bindToApp` puts that sentence on the screen unchanged, which is why nothing here rewrites
  // it. Filtering the kind out instead would need the client to know which models Sage holds a token
  // for, and no client surface reads `/api/model-api-credentials` — asking would be a new seam for
  // the sake of hiding a row whose refusal already says what to do about it.
  //
  // What is still missing is the box that takes the paste, and that is #128's. Until it exists there
  // is nothing here to point at, and a sentence promising one would rebuild the dead end.
  const BINDABLE_KINDS = ['model_llm', 'model_predictive', 'dataset', 'datasource'];

  // How many catalogue rows the picker shows before it stops and says how many it kept back. Only
  // the catalogue is capped. The working set is what somebody deliberately picked into this Project
  // and the row beside this control already lists it, so truncating THAT would hide what the header
  // is otherwise claiming; the catalogue is everything in Domino this viewer can reach, which has
  // no ceiling and is Browse Domino's list to walk properly.
  const CATALOGUE_SHOWN = 8;

  // The door into the selected app's Bindings, on the app's own surface (ADR-0021).
  //
  // Here rather than on the Resource Browser's rows because the surface that owns a scope owns the
  // act that writes it. This opened first and the rail's `Use in {app}` stayed live beside it, so
  // no window ever existed with no door open; #144 closed the rail's copy. This is the door a person
  // goes looking for — the refusal card's repair is the other one, and it comes to them. Both reach
  // `store.bindToApp`, which is where the receipt, the id-space rule and the membership re-read live
  // in one copy.
  //
  // The picker draws the working set first and the wider Domino catalogue behind it, through the
  // same `workingSetFirst` the composer's @ menu orders itself with. Shared rather than copied: the
  // two menus offer the same choice, and a person carries what they learn from one to the other.
  function AddToApp({ app }) {
    const { resourceGroups, catalogueParents, bindings, addToAppOpen } = SW.store.get();

    // Already-bound rows are left out rather than shown ticked. Re-binding an Alias or a Dataset
    // rewrites the same record with the same values, so the row would be an act with no effect —
    // and what the app already holds is named two inches to the left, by this very row.
    const bound = new Set((bindings || []).map((b) => SW.util.bindingId(b)));
    // `bindingKey` is the Binding identity, and a row without one cannot be posted: the prefixed
    // Project id resolves to no Resource, answers 404, and leaves the header redrawing unchanged —
    // a failure shaped exactly like success (#127).
    const offer = (rows) => (rows || []).filter((r) => r.bindingKey && !bound.has(r.id));

    const working = BINDABLE_KINDS.map((kind) => offer(resourceGroups[kind]));
    const catalogue = offer(
      (catalogueParents || []).filter((r) => BINDABLE_KINDS.indexOf(r.kind) !== -1)
    );
    // No cap on the whole list. One applied here would come off the END, which is the catalogue —
    // so a Project holding a dozen bindable Resources would silently lose the entire second group,
    // and the ordering criterion would be met by a menu that never showed the half it orders last.
    const rows = SW.util.workingSetFirst({ groups: working, catalogue });
    // Which half each row came from, asked as "is this in the working set" rather than "is this in
    // the catalogue". The two are the same question only while the two lists are disjoint, and a
    // row in both dedupes to the working-set copy — which the catalogue's question would then label
    // "joins this project" about something the project already holds.
    const inProject = new Set(working.flat().map((r) => r.id));
    const held = rows.filter((r) => inProject.has(r.id));
    const wider = rows.filter((r) => !inProject.has(r.id));

    // A row Domino no longer holds carries its mark into the label, because a disabled item never
    // fires and this one must stay pickable: the mark informs and the bind is refused downstream by
    // code that knows why, if it is refused at all (ADR-0034). In the label rather than a node of
    // its own — antd draws this list, and the working set's own rows are the only ones that can be
    // missing, so the catalogue half below never wears it.
    const option = (r) => ({
      key: r.id,
      label: `${SW.util.iconFor(r.kind)} ${r.name}`
        + (SW.util.isMissing(r) ? ` — ${SW.util.missingMark()}` : ''),
    });
    // A disabled item never fires, so the reason has to be the label — and the two empties are two
    // different states with two different ways out. Everything bound is a finished app; nothing to
    // bind at all is a Project nobody has picked anything into yet, and Browse Domino is where that
    // is fixed.
    const nothingToAdd = bound.size
      ? { key: 'none', disabled: true,
          label: `${app.name} already uses everything you can add here` }
      : { key: 'none', disabled: true,
          label: SW.brand.text('Nothing to add yet — pick one in Browse {platformName}') };

    const items = rows.length === 0
      ? [nothingToAdd]
      : [
          ...(held.length
            ? [{ key: 'in-project', type: 'group', label: 'In this project',
                 children: held.map(option) }]
            : []),
          // Last, and named for what picking one costs: these are the rows that are not here yet,
          // and binding one joins this Project on the way in (ADR-0018).
          //
          // Truncated with a count rather than quietly, and the heading stays the same sentence
          // either way — what a person has to know before clicking is what the click writes, and
          // that does not change with the length of the list. The remainder is named as Browse
          // Domino's, which is the surface that owns walking the whole catalogue.
          ...(wider.length
            ? [{ key: 'wider', type: 'group',
                 label: SW.brand.text('Elsewhere in {platformName} — joins this project'),
                 children: [
                   ...wider.slice(0, CATALOGUE_SHOWN).map(option),
                   ...(wider.length > CATALOGUE_SHOWN
                     ? [{ key: 'more', disabled: true, label: SW.brand.text(
                         `${wider.length - CATALOGUE_SHOWN} more in Browse {platformName}`) }]
                     : []),
                 ] }]
            : []),
        ];

    return h(
      Dropdown,
      {
        trigger: ['click'],
        placement: 'bottomRight',
        // Controlled, because this door is pointed at from somewhere else on the screen: the
        // refusal card's credential repair opens it (#143) now that the act it repairs lives here
        // rather than in the panel. A dropdown antd alone owned could only ever be clicked open.
        open: addToAppOpen,
        onOpenChange: (next) => (next ? SW.store.openAddToApp() : SW.store.closeAddToApp()),
        menu: {
          items,
          // Returned rather than fired and forgotten, the way the panel's row menu returns its own:
          // the act is a request, and a caller that cannot wait on it cannot tell a bind that landed
          // from one still in flight.
          onClick: ({ key, domEvent }) => {
            if (domEvent) domEvent.stopPropagation();
            // Shut by hand, which is what being controlled costs: antd closes an uncontrolled menu
            // on a click, and this one would otherwise stand open over its own receipt.
            SW.store.closeAddToApp();
            const row = rows.find((r) => r.id === key);
            return row ? SW.store.bindToApp(row) : undefined;
          },
        },
      },
      h(
        Tooltip,
        {
          // Claims no type, because the picker holds two: a Resource and an Asset both bind here,
          // and a tooltip naming one of them would be wrong under half its own menu (ADR-0014).
          //
          // It names the list the click writes to by the label that list actually carries, so the
          // promise and the destination read the same. It said `Bindings` until ADR-0025 — a word
          // this reader never sees anywhere else, least of all on the list the click writes to.
          //
          // One literal with the app passed as a value, never interpolated in: that is what keeps
          // the whole sentence readable to the lint, and an app a user named with braces in it is
          // not scanned again on the way through.
          title: SW.brand.text(
            'Record what this app depends on. It joins what {app} needs to run.',
            { app: app.name }
          ),
        },
        h(
          Button,
          { size: 'small', icon: h(PlusOutlined, null) },
          // The same words the refusal card quotes and the panel's row menu said until #144, because
          // it is one act and a second label would be a second thing to learn (ADR-0011). The words
          // outlived the other door: they are what the server's refusal sends the reader looking for.
          `Use in ${app.name}`
        )
      )
    );
  }

  // Which part of a Data Source the app reads, and the door that chooses it (#142, ADR-0021).
  //
  // The second of the two acts the bind was split into, and the cheaper one: it runs against a
  // Binding that already exists, so nothing about what the app depends on moves when it is used —
  // only which part of one thing it points at. It is on this surface because the Binding is, and a
  // Scope is a part of a Binding.
  //
  // Beside the name rather than at the end of the row, because it is a fact about THAT record and
  // the row can hold several. The unscoped state is the door's own label: the one Binding that is
  // unfinished is also the one that says how to finish it. It is not an error — "the Resource is
  // recorded but the part of it the app reads is not" is a state the glossary already had a word
  // for, and it is the only answer a store Sage has no dialect for can ever give.
  //
  // `open` is controlled, and for a different reason from the picker above: a menu that shut on
  // every click could not walk a ladder, and the position lives in the store anyway, because this
  // walk ends in a POST.
  function ScopeDoor({ binding, app }) {
    const { scopePick } = SW.store.get();
    const pick = scopePick && scopePick.id === binding.id ? scopePick : null;
    const scope = SW.util.scopeText(binding);
    // What the walk has answered so far. Empty at the top, which is the one position with nothing
    // to commit — a bind there would name the whole source, and that is what the Binding already
    // says without this control's help.
    const at = pick ? SW.util.scopeText({ database: pick.database, schema: pick.schema }) : '';

    const rungs = () => {
      // No ladder at all: a connector Sage has no dialect for. The Binding is still a real record —
      // "this app uses this Data Source" was the whole of what one meant before Scopes existed — so
      // this says why there is nothing to choose rather than drawing an empty list.
      if (pick.unreadable) {
        return [{ key: 'unreadable', disabled: true,
                  label: SW.brand.text('{assistantName} cannot look inside this {dataSource}') }];
      }
      if (pick.items === null) return [{ key: 'reading', disabled: true, label: 'Reading…' }];
      // A disabled item never fires, so the reason has to be the label. The levels already chosen
      // stay on offer above it: a listing that would not answer is not a choice anybody has lost.
      if (pick.error) {
        return [{ key: 'unavailable', disabled: true,
                  label: SW.brand.text('{assistantName} couldn’t look inside this {dataSource}') }];
      }
      if (!pick.items.length) return [{ key: 'empty', disabled: true, label: 'Nothing here' }];
      return pick.items.map((name) => ({ key: `at:${name}`, label: name }));
    };

    const items = !pick
      ? []
      : [
          // Stopping is an answer at every level below the top — a database alone is a Scope — so
          // the commit is offered wherever there is something to commit, beside the way back out
          // of it. Without the second, the first rung would be permanent for as long as the door
          // is open.
          ...(at
            ? [{ key: 'use', label: `Use ${at}` },
               { key: 'reset', label: 'Start again' },
               { type: 'divider' }]
            // At the top with a Scope already recorded, the answer on offer is the OTHER one: no
            // part in particular. "Not scoped yet" is a state the product names and draws, and a
            // door that could reach every state but that one would make the first choice a
            // one-way trip — the cost the split was made to remove. It is the same empty body the
            // route already takes from the bind.
            : pick.recorded
              ? [{ key: 'clear', label: 'Read all of it — no Scope' }, { type: 'divider' }]
              : []),
          ...rungs(),
        ];

    return h(
      Dropdown,
      {
        trigger: ['click'],
        placement: 'bottomRight',
        open: Boolean(pick),
        onOpenChange: (next) => (next
          ? SW.store.openScopePick(binding)
          : SW.store.closeScopePick()),
        menu: {
          items,
          onClick: ({ key, domEvent }) => {
            if (domEvent) domEvent.stopPropagation();
            if (!pick) return undefined;
            if (key === 'use') {
              return SW.store.saveScope({ database: pick.database, schema: pick.schema });
            }
            if (key === 'reset') return SW.store.scopePickReset();
            if (key === 'clear') return SW.store.saveScope({});
            // The table stage has no rung below it, so a name chosen there IS the Scope. Which
            // level a name answers is the store's question, not this menu's — the ladder is not
            // the same height for every store.
            if (key.indexOf('at:') === 0) return SW.store.scopePickStep(key.slice(3));
            return undefined;
          },
        },
      },
      h(
        Tooltip,
        {
          title: scope
            ? `${app.name} reads ${scope} in ${binding.display_name || binding.name}. `
              + 'Choose again to move it.'
            : `Choose which database, schema or table ${app.name} reads in `
              + `${binding.display_name || binding.name}. You can change it later.`,
        },
        h(
          Button,
          { size: 'small', type: 'link', className: 'sw-appdeps-door' },
          scope || SW.util.NO_SCOPE_YET
        )
      )
    );
  }

  // What the selected app depends on, and now the whole of the app's scope: the Bindings someone
  // picked and the files someone attached, both written per app and re-read by `loadBuild` when the
  // app switches.
  //
  // This is the one surface for that scope. It began as an always-on row under the header (#92),
  // moved behind a menu item because the resources panel showed "In {app}" beside it, and has now
  // absorbed that section outright. The panel is the Project's list; putting an app's list in it
  // made a project-scoped surface do double duty, and the app's own doors — Add, Scope, and now
  // Remove — are what ADR-0021 says belong on the app's own surface. ADR-0011's rule is unchanged
  // and better served: an object is still removed from the list that owns its scope, and this is
  // now that list. What ADR-0011 called "a second copy of the guard" was only a risk while there
  // were two lists; there is one.
  //
  // `used === false` is the last build turn having looked and found nothing calling it; `undefined`/
  // `null` is no turn having left an answer for this app. Only the first draws a mark (#93) — this
  // is advisory (ADR-0010) and never a gate, so the mark is a word beside a name, not a warning.
  function AppDependenciesModal() {
    const { activeApp, bindings, appAttachments, appDependenciesOpen, appRemoval } = SW.store.get();
    const close = () => SW.store.closeAppDependencies();
    if (!activeApp) return null;

    const bound = bindings || [];
    const files = appAttachments || [];
    const empty = bound.length === 0 && files.length === 0;

    // Every label names the scope it acts on, which is ADR-0011's rule and the only thing telling
    // this Remove apart from the Project's. `Delete from {dataset}` is the door onto Sage's own
    // bytes and never onto a Dataset file somebody already had (ADR-0023).
    const menuFor = (record, attachment) => ({
      items: [
        { key: 'remove', label: `Remove from ${activeApp.name}`, danger: true },
        ...(attachment && SW.util.isSageUpload(attachment)
          ? [{ key: 'delete', label: `Delete from ${attachment.dataset}`, danger: true }]
          : []),
      ],
      onClick: ({ key }) => {
        if (key === 'delete') return SW.store.deleteAttachmentFromApp(attachment);
        return attachment
          ? SW.store.removeAttachmentFromApp(attachment)
          : SW.store.removeBindingFromApp(record);
      },
    });

    // Both labels, always, over an app that records anything — including the one with nothing under
    // it. That rule was the panel section's (ADR-0011, ADR-0025) and it comes here with the list:
    // this is where somebody arrived intending to act, and "Files it carries — none" answers the
    // question they came with. `held` is what the APP records, which is the only thing that can
    // make a group empty here.
    const group = (label, held, rows) =>
      h(
        'div',
        { key: label, className: 'sw-appdeps-section' },
        h(
          'div',
          { className: 'sw-group-label sw-app-group' },
          SW.brand.text(held ? label : `${label} — none`)
        ),
        rows
      );

    // The kind, as the icon the panel's rows wear rather than as the record's own word. This list
    // groups by the app's relationship to a thing and never by type (ADR-0025), so the kind cannot
    // be a heading — but a row saying only a name leaves "is that a Data Source or an Alias"
    // unanswered on the one surface where it decides what the Scope door beside it even means.
    const KIND_ICON = {
      data_source: 'datasource', llm_alias: 'model_llm', model_api: 'model_predictive',
    };

    const row = (key, name, { kind, mark, door, menu }) =>
      h(
        'div',
        { key, className: 'sw-appdeps-row' },
        h('span', { className: 'sw-appdeps-icon' }, SW.util.iconFor(KIND_ICON[kind] || kind)),
        h('span', { className: 'sw-appdeps-name' }, name),
        door,
        // Two words beside a name cannot say what looked, when, or that nothing is blocked by the
        // answer — and a creator who reads "not used" as "this will not publish" has been told the
        // opposite of ADR-0010. The header's strip carried this sentence in the tooltip over the
        // whole kind; here it belongs to the one row it qualifies. It explains and never acts: the
        // mark is a word, and the acts on this row are in the menu beside it.
        mark &&
          h(
            Tooltip,
            { title: '“not used” is what the last build saw, and it publishes either way.' },
            h('span', { className: 'sw-appdeps-unused' }, ' (not used)')
          ),
        h(
          Dropdown,
          { menu, trigger: ['click'], placement: 'bottomRight' },
          h(
            'button',
            { className: 'sw-res-more', 'aria-label': `Actions for ${name}` },
            h(MoreOutlined, null)
          )
        )
      );

    return h(
      Modal,
      {
        open: !!appDependenciesOpen,
        onCancel: close,
        title: `App dependencies · ${activeApp.name}`,
        width: 480,
        footer: h(Button, { onClick: close }, 'Done'),
      },
      h(
        'div',
        { className: 'sw-appdeps-body' },
        // What the last removal reported, after the act. Here rather than in a toast because it is
        // only worth having if it can be acted on, and five seconds is not long enough to read a
        // file list and decide (ADR-0011). It followed the removal door out of the panel.
        appRemoval &&
          h(
            'div',
            { className: 'sw-appdeps-notice' },
            h('span', { className: 'sw-appdeps-notice-text' }, appRemoval.text),
            // Writes the prompt into the composer and stops. Firing the turn from here could be
            // refused by the turn lock, and would put work past a plan gate nobody read.
            appRemoval.prompt &&
              h(
                Button,
                {
                  type: 'link',
                  size: 'small',
                  style: { padding: 0, height: 'auto' },
                  onClick: () => {
                    SW.store.seedComposer(appRemoval.prompt);
                    close();
                  },
                },
                `Ask ${SW.brand.assistant()} to clean this up`
              ),
            h(
              Button,
              {
                type: 'link',
                size: 'small',
                style: { padding: 0, height: 'auto' },
                onClick: () => SW.store.dismissAppRemoval(),
              },
              'Dismiss'
            )
          ),
        // One sentence, written once in `SW.util`, so this and anything else describing an empty
        // app never drift into two answers about the same thing (ADR-0011).
        empty
          ? h('div', { className: 'sw-appdeps-intro' }, SW.util.appScopeEmpty('Nothing yet.'))
          : h(
              Fragment,
              null,
              // Named by what the app cannot do without them, never by the record's own word
              // (ADR-0025). Through `SW.brand.text` because that is a marked position: neither
              // label carries a token today, and routing them anyway is what puts them where the
              // lint can read them.
              group(
                'Needs to run',
                bound.length,
                bound.map((b) => row(
                  SW.util.bindingId(b),
                  b.display_name || b.name || b.id,
                  {
                    kind: b.kind,
                    mark: b.used === false,
                    // The Scope door, for the one kind that has a part to choose (#142): an Alias
                    // has no part to name and a Dataset is read whole, so every other kind draws
                    // none.
                    door: SW.util.recordsScope(b.kind)
                      ? h(ScopeDoor, { binding: b, app: activeApp })
                      : null,
                    menu: menuFor(b, null),
                  }
                ))
              ),
              group(
                'Files it carries',
                files.length,
                // No "not used" marks here: whether the source uses an ATTACHMENT is
                // `_data_usage`'s question, asked live by detach and delete, not this list's.
                // Through the same derivation the @ menu offers and the turn resolves
                // (`SW.util.attachmentRow`, #148) rather than a second split of the path here.
                // This list is now the ONLY place an Attachment is drawn, so it is also the one
                // that has to name it the way every other reader of that record does.
                files.map((a) => row(
                  a.path,
                  SW.util.attachmentRow(a).name,
                  { kind: 'file', menu: menuFor(null, a) }
                ))
              )
            ),
        h('div', { className: 'sw-appdeps-foot' }, h(AddToApp, { app: activeApp }))
      )
    );
  }

  // Exposed for the same reason `SW.ResourcePanel` is: it is now a surface with a list, a set of
  // doors and an empty state of its own, and a reader that has to mount the whole of Build to see
  // it is a reader nothing will check it with.
  SW.AppDependenciesModal = AppDependenciesModal;

  function PreviewPane({ resumed }) {
    const { previewSrc, previewStatus, activeApp, costUrl, buildRunning } = SW.store.get();
    const starting = previewStatus === 'starting';
    const failed = previewStatus === 'err';
    const stalled = previewStatus === 'stalled';

    // Every app action in one place, grouped by what it's about rather than left spread across a
    // kebab and three loose toolbar buttons (Rename and Delete on Reset's precedent, #38: text-
    // labelled items in an overflow, Delete danger-styled and last below a divider).
    //
    // "App" is the app outside in Domino plus the two facts about the app in here — its name, and
    // whether it stays. "Manage" is the read-only glances and the dependencies door. Reload preview
    // joins App rather than staying its own icon button: it acts on this same preview.
    //
    // Refused mid-build rather than silent about it: publishing commits and pushes the working
    // tree, and a turn still writing files into it has not finished writing them. `building` is the
    // row's own fact and survives a reload; `buildRunning` is this tab's, and catches a turn another
    // app in this Project is streaming into — the commit is the Project's, so that is equally in
    // the way.
    const appMenu = activeApp && {
      items: [
        {
          type: 'group',
          label: 'App',
          children: [
            {
              key: 'publish',
              label: activeApp.building || buildRunning
                ? 'Publish — wait for this build to finish' : 'Publish',
              disabled: !!(activeApp.building || buildRunning),
            },
            // Opens the deployed App. `Open preview in a new tab`, beside the menu trigger, opens
            // the LOCAL preview instead — two destinations, kept as two controls rather than one
            // that changes where it goes.
            {
              key: 'open',
              label: activeApp.published ? 'Open app' : 'Open app — publish it first',
              disabled: !activeApp.published,
            },
            { key: 'reload', label: 'Reload preview' },
            { key: 'rename', label: 'Rename' },
            { type: 'divider' },
            { key: 'delete', label: 'Delete', danger: true },
          ],
        },
        { type: 'divider' },
        {
          type: 'group',
          label: 'Manage',
          children: [
            { key: 'dependencies', label: 'App dependencies' },
            // A link, not a click handler, so the same open-in-new-tab affordances (middle click,
            // right click) work here that a plain anchor gives for free. Hidden with no URL, which
            // is a run pointed at no Domino gateway — there is no dashboard with this project's
            // spend in it to open.
            costUrl && {
              key: 'cost',
              label: h(
                'a',
                { href: costUrl, target: '_blank', rel: 'noreferrer' },
                'Cost & activity'
              ),
            },
            { key: 'history', label: 'Build history' },
          ].filter(Boolean),
        },
      ],
      onClick: ({ key, domEvent }) => {
        domEvent.stopPropagation();
        if (key === 'publish') publishApp(activeApp);
        // A new tab, so the Build you published from is still behind it — Gallery's cards open the
        // same way for the same reason. Opened as given: nothing here builds the URL.
        if (key === 'open' && activeApp.url) window.open(activeApp.url, '_blank', 'noopener');
        if (key === 'reload') SW.store.refreshPreview();
        if (key === 'rename') renameApp(activeApp);
        if (key === 'delete') deleteApp(activeApp);
        if (key === 'dependencies') SW.store.openAppDependencies();
        if (key === 'history') SW.store.openBuildHistory();
      },
    };

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
        // It opens the LOCAL preview; `Open app`, in the menu beside it, opens the deployed App.
        // Two controls, two labels, two destinations — never one button that changes where it
        // goes, which is also why this one stays its own icon rather than folding into the menu.
        h(
          Tooltip,
          { title: 'Open preview in a new tab' },
          h(Button, {
            size: 'small',
            icon: h(ExportOutlined, null),
            'aria-label': 'Open preview in a new tab',
            onClick: () => window.open('./preview/', '_blank'),
          })
        ),
        // Everything else this app can do, in one right-aligned menu (see `appMenu` above) rather
        // than spread across a kebab and a run of loose buttons.
        activeApp &&
          h(
            Dropdown,
            { menu: appMenu, trigger: ['click'], placement: 'bottomRight' },
            h(
              Tooltip,
              { title: `Actions for ${activeApp.name}` },
              h(Button, {
                size: 'small',
                icon: h(MoreOutlined, null),
                'aria-label': `Actions for ${activeApp.name}`,
              })
            )
          )
      ),
      h(AppDependenciesModal, null),
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
            projectPlan, runningTurn } = SW.store.get();
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
    // Two facts, and they are independent (#172). "Is there an app" is the rail row's `built`; "is
    // a plan waiting" is the pin's status. Both were read off the pin alone, which made them
    // mutually exclusive by construction — `status: built` means plan.md has been ARCHIVED, so the
    // pair (app built, plan live) had no branch and fell to the plan note, under a greeting
    // offering to write the app that was rendering in the preview.
    //
    // `built` is a latch: it is set by a build that finished and cleared only by Reset app, so an
    // app whose latest phased build died at step 4 still reads true. That is the right fact for the
    // note below — it is about what the PREVIEW is serving, and it is serving that app.
    const appBuilt = !!(activeApp && activeApp.built);
    // A new conversation clears the transcript while the preview keeps serving the app the rail
    // has selected. That is the truth, but unlabelled it reads as this conversation's work, so say
    // whose app it is. (The preview following the app a build is running in is #77.)
    const resumed = noAppTurns && appBuilt;
    // The sibling case #77 missed: a plan can sit live (the rail's pin already shows it) with a
    // build still owed on it. A new conversation clears the transcript there too, and without a
    // note it reads the same as a plan that was never written — the rail disagrees, but only the
    // rail is looking.
    const pendingPlan = noAppTurns && !!projectPlan && projectPlan.status !== 'built';
    // And WHY it is still live, which is three different pieces of news off one number the server
    // reads from the workspace: 0 is a plan no build has consumed, 1 is a build that did not
    // finish, and N > 1 is a phased build that stopped at step N with the steps before it on disk.
    // No total beside it — the pin's `steps` counts numbered lines under the Plan heading and this
    // is the phased parser's step, so the pair could read "step 4 of 3".
    const owedStep = (pendingPlan && projectPlan.retryStep) || 0;
    // WHERE it stopped is a claim only a build that is not running can make: a phased build writes
    // the resume point BEFORE each phase runs, so mid-build the number names the phase EXECUTING.
    //
    // Read off the turn rather than off `buildRunning` alone, which is any turn holding the
    // Project's lock — Chat's included. Falling back on one of those would be worse than vague: the
    // sentence below presents a plan whose build died as one nobody has built from, and drops
    // "try again", the only words that resume a build instead of proposing a second plan for a
    // request already approved. A Chat question is enough to trigger it, which is not rare.
    //
    // A wedge counts, and is not covered by `buildRunning`: the server reports `running` as "locked
    // AND not wedged", so a build whose session went quiet mid-phase reads as idle to every tab but
    // the one streaming it. The lock is still held there, the step still names the phase that was
    // executing, and the "try again" this would offer would queue behind the wedge rather than run.
    //
    // A nameless turn counts as a build too: `runningTurn` is null for the gap between two queued
    // turns, and that must not read as "nothing is building". It is also null while a publish or a
    // reset holds the raw lock, which costs this sentence its step for as long as one runs —
    // accepted, because the client cannot tell those apart (the store's own `runningTurnElsewhere`
    // gives up on the same question) and the other way round announces a stop mid-build.
    //
    // A turn carrying no app matches whatever is on screen, the same way the Stop bar reads one.
    // `buildTyping` would not do at all: it is only this tab's stream, so a second tab, or a new
    // conversation opened beside a build in flight, has an empty transcript and reads as idle.
    const buildInFlight = (buildRunning || turnWedged)
      && (!runningTurn
          || (runningTurn.kind === 'build'
              && (!runningTurn.app || !activeApp || runningTurn.app === activeApp.id)));
    const stoppedAt = buildInFlight ? 0 : owedStep;
    // The step is where it stopped, never "it wrote nothing": the resume point is written before
    // the phase runs, so step 1 owed can mean step 1 started and failed halfway with its files on
    // disk. And no promise about where the retry resumes — an unphased build has no seam and runs
    // the plan whole however far the last attempt got.
    const planNote = stoppedAt > 1
      ? `A build started from this plan and stopped at step ${stoppedAt}; the steps before it are `
        + 'already in the app. Say “try again” to run it again, or describe a change to replace it.'
      : stoppedAt === 1
        ? 'A build started from this plan and did not finish. Say “try again” to run it again, or '
          + 'describe a change to replace it.'
        // Without the shared stem when the note above has just said it. The two notes drawing
        // together is the state this whole screen was rewritten for, so "a new conversation clears
        // the transcript, not the X" twice running is now the ordinary reading rather than a rare
        // one, and it reads as a stutter.
        : (resumed ? '' : 'A new conversation clears the transcript, not the plan. ')
          + 'Open it in the rail to review it, or describe a change to replace it.';
    // Whether there is app code in the preview at all, which is NOT `built`: `mark_built` runs only
    // on a build that finished every phase, so a FIRST phased build that died at step 4 left three
    // phases on disk (they are deliberately not reverted) under a row that still says false. A
    // resume point past step 1 is the server saying those earlier steps exist. The note above stays
    // on `built` — "an app you already built" must not be said of a build that never finished — but
    // the greeting must not offer to write an app whose files are already being served.
    //
    // Off `owedStep` rather than `stoppedAt`: a turn running somewhere else does not un-write the
    // phases the last attempt left on disk, and reading the silenced number here would put the
    // "write this app" offer back on screen for exactly the app this is about.
    //
    // Step 1 is deliberately NOT in: it is the only number that says nothing about files. A build
    // that owes step 1 may have written half of it before dying, or may have failed at the gateway
    // before touching anything, and nothing the client can read tells the two apart. So this holds
    // the offer to write, which is honest either way — approving that plan IS what writes the app,
    // and the note under it names the shorter route ("try again") for the case where a build has
    // already started. Claiming "keep building this app" here would be a guess about a workspace
    // that may hold nothing at all.
    const appHasCode = appBuilt || owedStep > 1;

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
                  // An app that exists cannot be written, so the offer to write it is not the
                  // greeting for it. Gated on the app rather than on the plan, because this line
                  // was wrong on EVERY row where something is built and not only on the pair below.
                  h(
                    'div',
                    { className: 'sw-empty-title' },
                    appHasCode ? 'Keep building this app' : 'Build the app from a plan'
                  ),
                  h(
                    'div',
                    { className: 'sw-empty-detail' },
                    appHasCode
                      ? 'Describe a change, or approve a plan to build it again.'
                      : 'Approve a plan to write this app, or describe a change.'
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
                    ),
                  pendingPlan &&
                    h(
                      'div',
                      { className: 'sw-build-resume-note' },
                      h(
                        'div',
                        { className: 'sw-empty-title' },
                        'There is already a plan waiting'
                      ),
                      h('div', { className: 'sw-empty-detail' }, planNote)
                    )
                ),
              buildTyping && h(SW.TypingIndicator, { label: buildTyping })
            ),
            h(
              'div',
              { className: 'sw-builder-chat-composer' },
              // Stop refers to the turn you can see; the spinner refers to the Project (#126).
              // `buildRunning` is still the project-wide fact — it is what disables Reset app and
              // the app switcher below — so it decides whether anything shows here at all. What it
              // no longer decides is whether that thing is a Stop: a Chat turn holding the lock
              // gets the line and the link instead, because a button under the Build composer that
              // ends a chat question is aimed at something nobody on this screen can see.
              buildRunning &&
                h(
                  'div',
                  { className: 'sw-build-stop' },
                  SW.store.runningTurnHere('build', thread && thread.id,
                                           activeApp && activeApp.id)
                    ? h(
                      Button,
                      { size: 'small', danger: true, onClick: () => SW.store.stopBuild() },
                      'Stop'
                    )
                    : (() => {
                      const away = SW.store.runningTurnElsewhere('build', thread && thread.id,
                                                                 activeApp && activeApp.id);
                      return h(
                        'span',
                        { className: 'sw-caption' },
                        away.href
                          ? h('a', { href: away.href }, away.text)
                          : away.text
                      );
                    })()
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
