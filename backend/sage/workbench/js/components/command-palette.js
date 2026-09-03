window.SW = window.SW || {};

(function () {
  const { createElement: h, useState, useEffect, useRef } = React;
  const { Modal, Input } = antd;
  const { SearchOutlined } = icons;

  // Going to Chat or Build keeps the conversation you are in: switching modes is turning your
  // head, not starting over (docs/workbench/handoff.md). A route that dropped the Thread would
  // now drop Build's transcript with it.
  function goTo(mode) {
    const { thread } = SW.store.get();
    SW.router.go(thread ? SW.conversationRoute(thread, mode) : `#/${mode}`);
  }

  // Same as the rail's button: clear, then land on a mode with nothing open. The first message
  // is what opens the conversation.
  function newConversation() {
    const mode = SW.router.get().mode === 'build' ? 'build' : 'chat';
    const { activeApp } = SW.store.get();
    SW.store.newConversation();
    SW.router.go(mode === 'build' && activeApp ? `#/build?app=${activeApp.id}` : `#/${mode}`);
  }

  const STATIC_ACTIONS = [
    { id: 'go_chat', group: 'Go to', label: 'Chat', run: () => goTo('chat') },
    { id: 'go_build', group: 'Go to', label: 'Build', run: () => goTo('build') },
    { id: 'go_code', group: 'Go to', label: 'Code', run: () => SW.router.go('#/code') },
    // Manage is a Domino App outside this Workbench, so the palette opens it the way the platform
    // bar does — and, like the bar, drops the row entirely on a deployment that has no Manage to
    // open rather than offering a destination that goes nowhere.
    {
      id: 'go_manage',
      group: 'Go to',
      label: 'Manage',
      available: () => !!SW.store.get().manageUrl,
      run: () => window.open(SW.util.mainHostUrl(SW.store.get().manageUrl), '_blank', 'noreferrer'),
    },
    // A getter, because this list is built when the file is evaluated — before GET /api/brand has
    // answered — and the label is both what is shown and what the query is matched against.
    {
      id: 'go_gallery',
      group: 'Go to',
      get label() { return SW.brand.text('{gallery}'); },
      run: () => SW.router.go('#/gallery'),
    },
    { id: 'new_thread', group: 'Actions', label: 'New conversation', run: newConversation },
    { id: 'switch', group: 'Actions', label: 'Switch project', run: () => SW.store.set({ scopePickerOpen: true }) },
    { id: 'people', group: 'Actions', label: 'Add people', run: () => SW.store.set({ peopleOpen: true }) },
    { id: 'resources', group: 'Actions', label: 'Open resources', run: () => SW.store.openDock('resources') },
  ];

  SW.CommandPalette = function CommandPalette() {
    const { paletteOpen } = SW.store.get();
    const [query, setQuery] = useState('');
    const [results, setResults] = useState([]);
    const [cursor, setCursor] = useState(0);
    const inputRef = useRef(null);

    useEffect(() => {
      if (!paletteOpen) {
        setQuery('');
        setResults([]);
        setCursor(0);
      }
    }, [paletteOpen]);

    useEffect(() => {
      if (!paletteOpen || !query.trim()) {
        setResults([]);
        return undefined;
      }
      let cancelled = false;
      const timer = setTimeout(() => {
        SW.api.search(query).then((found) => {
          if (!cancelled) setResults(found.results || []);
        });
      }, 120);
      return () => {
        cancelled = true;
        clearTimeout(timer);
      };
    }, [query, paletteOpen]);

    if (!paletteOpen) return null;
    const close = () => SW.store.set({ paletteOpen: false });

    const q = query.trim().toLowerCase();
    const actions = STATIC_ACTIONS.filter(
      (a) => (!a.available || a.available()) && (!q || a.label.toLowerCase().includes(q))
    );
    const GROUP = {
      thread: 'Conversations',
      plan: 'Plans',
      app: 'Apps',
      resource: 'Resources',
    };
    const found = results.map((r) => ({
      id: `${r.kind}_${r.id}`,
      group: GROUP[r.kind] || 'Results',
      label: r.title,
      icon: r.kind === 'resource' ? SW.util.iconFor('dataset') : null,
      run: () =>
        r.route
          ? SW.router.go(r.route)
          : SW.store.previewResource(r.id),
    }));
    const all = [...actions, ...found];

    const groups = [];
    all.forEach((item) => {
      let group = groups.find((g) => g.label === item.group);
      if (!group) {
        group = { label: item.group, items: [] };
        groups.push(group);
      }
      group.items.push(item);
    });

    const run = (item) => {
      close();
      item.run();
    };

    const onKeyDown = (e) => {
      if (e.key === 'ArrowDown') {
        e.preventDefault();
        setCursor((c) => Math.min(c + 1, all.length - 1));
      } else if (e.key === 'ArrowUp') {
        e.preventDefault();
        setCursor((c) => Math.max(c - 1, 0));
      } else if (e.key === 'Enter' && all[cursor]) {
        e.preventDefault();
        run(all[cursor]);
      }
    };

    let index = -1;

    return h(
      Modal,
      {
        open: true,
        onCancel: close,
        footer: null,
        closable: false,
        width: 560,
        styles: { body: { padding: 0 } },
        className: 'sw-palette-modal',
        destroyOnClose: true,
      },
      h(
        'div',
        { className: 'sw-palette', onKeyDown },
        h(Input, {
          ref: inputRef,
          autoFocus: true,
          bordered: false,
          size: 'large',
          prefix: h(SearchOutlined, { style: { color: '#8F8FA3' } }),
          placeholder: 'Search apps, plans, conversations, resources…',
          value: query,
          onChange: (e) => {
            setQuery(e.target.value);
            setCursor(0);
          },
        }),
        h(
          'div',
          { className: 'sw-palette-results sw-scroll' },
          groups.length === 0
            ? h('div', { className: 'sw-palette-empty sw-secondary' }, SW.util.noMatch(query))
            : groups.map((group) =>
                h(
                  'div',
                  { key: group.label, className: 'sw-palette-group' },
                  h('div', { className: 'sw-group-label' }, group.label),
                  group.items.map((item) => {
                    index += 1;
                    const active = index === cursor;
                    const myIndex = index;
                    return h(
                      'button',
                      {
                        key: item.id,
                        className: `sw-palette-item${active ? ' is-active' : ''}`,
                        onMouseEnter: () => setCursor(myIndex),
                        onClick: () => run(item),
                      },
                      item.icon && h('span', { className: 'sw-res-icon' }, item.icon),
                      h('span', { className: 'sw-palette-label' }, item.label),
                      item.detail && h('span', { className: 'sw-palette-detail' }, item.detail)
                    );
                  })
                )
              )
        ),
        h(
          'div',
          { className: 'sw-palette-footer' },
          h('span', null, h('kbd', null, '↑'), h('kbd', null, '↓'), ' to navigate'),
          h('span', null, h('kbd', null, '⏎'), ' to open'),
          h('span', null, h('kbd', null, 'esc'), ' to close')
        )
      )
    );
  };
})();
