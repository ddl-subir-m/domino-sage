window.SW = window.SW || {};

// The Build rail. A Project holds many Built Apps (ADR-0008), so what the rail lists follows the
// mode: Threads in Chat, Built Apps here. Same furniture either way — a rail you have to
// relearn when you turn your head is a rail that costs more than it lists.
(function () {
  const { createElement: h, useState, useEffect, Fragment } = React;
  const { Tooltip, Input, Dropdown, Modal, Button, Checkbox } = antd;
  const { SearchOutlined, MoreOutlined, EditOutlined, PlusOutlined, DeleteOutlined, LoadingOutlined } = icons;

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

  // Picking an app goes through the ROUTE, never straight to the store. Build re-asserts whatever
  // `?app=` names (see BuildMode), so a click that only told the store would be undone by the next
  // render — the row would light up and snap back. One writer: the route says which app, the store
  // follows it.
  SW.appRoute = function appRoute(app) {
    const { thread } = SW.store.get();
    return `#/build${thread ? `/${thread.id}` : ''}?app=${app.id}`;
  };

  function AppRow({ app, active }) {
    return h(
      'div',
      {
        className: `sw-thread${active ? ' is-active' : ''}`,
        onClick: () => SW.router.go(SW.appRoute(app)),
        role: 'button',
      },
      // The id is on hover rather than in the row. Two apps can carry the same name and only the
      // id tells them apart, but it is 25 characters of hex nobody reads on the way past — in the
      // row it pushed the name it was there to qualify into second place. Scoped to the name and
      // state, not the whole row: over the actions button the answer is what that button does.
      h(
        Tooltip,
        {
          title: [
            app.building ? 'A build is running in this app.' : '',
            // Said in words as well as shown as a badge: the badge is what you notice from across
            // the rail, and this is what tells you what it means the first time you see one.
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
              ? h('span', { className: 'sw-thread-building' }, h(LoadingOutlined, { spin: true }), 'Building\u2026')
              : app.built ? 'Built' : 'Not built yet',
            // Somebody else has pushed to this app (#78). It sits beside the build state rather
            // than replacing it, because the two are about different people and both still hold:
            // your build is running AND their work is waiting.
            app.behind && h('span', { className: 'sw-thread-behind' }, 'Changes to pull')
          )
        )
      ),
      h(
        Dropdown,
        {
          menu: {
            items: [
              { key: 'rename', label: 'Rename', icon: h(EditOutlined, null) },
              // Danger-styled and last, the way Delete conversation is in the Chat rail. It is
              // also the only place Delete lives: Reset is in the composer, so the action that
              // ends an app and the action that starts it over are never side by side (#76).
              { key: 'delete', label: 'Delete', danger: true, icon: h(DeleteOutlined, null) },
            ],
            onClick: ({ key, domEvent }) => {
              domEvent.stopPropagation();
              if (key === 'rename') renameApp(app);
              if (key === 'delete') deleteApp(app);
            },
          },
          trigger: ['click'],
          placement: 'bottomRight',
        },
        h(
          Tooltip,
          { title: `Actions for ${app.name}` },
          h('button', {
            className: 'sw-thread-more',
            'aria-label': `Actions for ${app.name}`,
            onClick: (e) => e.stopPropagation(),
          }, h(MoreOutlined, null))
        )
      )
    );
  }

  SW.AppRail = function AppRail() {
    const { apps, activeApp, railHidden } = SW.store.get();
    const [query, setQuery] = useState('');

    useEffect(() => {
      SW.store.loadApps();
      // The badge is the point of the check being a background one (#78): a teammate's push has to
      // reach the rail without anyone opening an app to find out. The server does the fetching on
      // its own schedule and this only re-reads the answer, so the interval is cheap.
      const id = setInterval(() => SW.store.loadApps(), 30000);
      return () => clearInterval(id);
    }, []);

    if (railHidden) {
      return h(
        'div',
        { className: 'sw-rail is-hidden' },
        h(
          Tooltip,
          { title: 'Show Built Apps', placement: 'right' },
          h(
            'button',
            {
              className: 'sw-icon-btn is-dark-text',
              'aria-label': 'Show Built Apps',
              onClick: () => SW.store.toggleRail(),
            },
            h(icons.MenuUnfoldOutlined, null)
          )
        )
      );
    }

    const needle = query.trim().toLowerCase();
    const filtered = apps.filter((a) => !needle || a.name.toLowerCase().includes(needle));

    return h(
      Fragment,
      null,
      h(
        'div',
        { className: 'sw-rail-head' },
        // Where Chat's rail puts New conversation, so neither rail has to be relearned. Nothing is
        // asked for on the way in: no Thread and no plan, because the plan gate fires on the first
        // turn of an app that has not been built and that is the review (#74).
        h(
          Button,
          {
            type: 'primary',
            icon: h(PlusOutlined, null),
            block: true,
            onClick: () => SW.store.createApp(),
          },
          'New app'
        ),
        h(
          Tooltip,
          { title: 'Hide Built Apps' },
          h(
            'button',
            {
              className: 'sw-icon-btn is-dark-text',
              'aria-label': 'Hide Built Apps',
              onClick: () => SW.store.toggleRail(),
            },
            h(icons.MenuFoldOutlined, null)
          )
        )
      ),
      apps.length > 1 &&
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
        { className: 'sw-rail-list sw-scroll' },
        // The label the head used to carry. It says what this rail lists, which is the one thing
        // that changes when you cross between modes.
        h('div', { className: 'sw-rail-group sw-group-label' }, 'Built Apps in this Project'),
        filtered.length === 0
          ? h(
              'div',
              { className: 'sw-rail-empty sw-secondary' },
              needle
                ? `No Built Apps match "${query}".`
                : 'No Built Apps yet. Start one with New app, or approve a plan in Chat.'
            )
          : filtered.map((app) =>
              h(AppRow, {
                key: app.id,
                app,
                active: !!activeApp && activeApp.id === app.id,
              })
            ),
        h(
          'div',
          { className: 'sw-rail-note' },
          'Each Built App has its own code, plan and Resources. Building one leaves the rest alone.'
        )
      )
    );
  };
})();
