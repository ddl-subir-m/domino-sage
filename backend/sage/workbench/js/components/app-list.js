window.SW = window.SW || {};

// The Build rail. A Project holds many Built Apps (ADR-0008), so what the rail lists follows the
// mode: Threads in Chat, Built Apps here. Same furniture either way — a rail you have to
// relearn when you turn your head is a rail that costs more than it lists.
(function () {
  const { createElement: h, useState, useEffect, Fragment } = React;
  const { Tooltip, Input, Dropdown, Modal } = antd;
  const { SearchOutlined, MoreOutlined, EditOutlined } = icons;

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
        { title: `ID ${app.id}`, placement: 'right', mouseEnterDelay: 0.5 },
        h(
          'div',
          { className: 'sw-thread-main' },
          h('div', { className: 'sw-thread-title' }, app.name),
          h('div', { className: 'sw-thread-meta' }, app.built ? 'Built' : 'Not built yet')
        )
      ),
      h(
        Dropdown,
        {
          menu: {
            items: [{ key: 'rename', label: 'Rename', icon: h(EditOutlined, null) }],
            onClick: ({ domEvent }) => {
              domEvent.stopPropagation();
              renameApp(app);
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
        h('div', { className: 'sw-group-label', style: { flex: 1 } }, 'Built Apps in this Project'),
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
        filtered.length === 0
          ? h(
              'div',
              { className: 'sw-rail-empty sw-secondary' },
              needle
                ? `No Built Apps match "${query}".`
                : 'No Built Apps yet. Approve a plan in Chat and Sage builds one here.'
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
