window.SW = window.SW || {};

(function () {
  const { createElement: h, useState } = React;
  const { Popover, Input, Button, Tooltip } = antd;
  const { PlusOutlined, DownOutlined, SearchOutlined } = icons;

  // Only the project this builder is bound to can be described from here — the others are a name
  // and a slug to open by, so the row says what picking it does rather than inventing counts.
  // A row this process hasn't cloned yet (`local: false`) clones on click (ONE-APP-PLAN.md Phase 3
  // step 3 — `ProjectRegistry.clone()`), then opens the same way a local row does.
  function ScopeRow({ project, onSelect }) {
    const [cloning, setCloning] = useState(false);

    const clone = async () => {
      setCloning(true);
      try {
        const cloned = await SW.api.cloneProject(project.dominoProjectId);
        window.location.assign(`../${cloned.slug}/`);
      } catch (e) {
        setCloning(false);
        antd.message.error(e.message);
      }
    };

    return h(
      'button',
      {
        className: `sw-scope-row${project.current ? ' is-active' : ''}`,
        disabled: cloning,
        onClick: cloning ? undefined : () => (project.local ? onSelect(project) : clone()),
      },
      h('span', { className: 'sw-scope-dot' }),
      h(
        'span',
        { className: 'sw-scope-row-main' },
        h('span', { className: 'sw-scope-row-name' }, project.name)
      ),
      h(
        'span',
        { className: 'sw-scope-row-count' },
        project.current ? 'You are here' : (project.local ? 'Open' : (cloning ? 'Cloning…' : 'Clone'))
      )
    );
  }

  SW.ScopePicker = function ScopePicker({ open, onOpenChange }) {
    const { scope, projects, scopeFlash } = SW.store.get();
    const [query, setQuery] = useState('');

    // Switching Project is a same-origin navigation now, not a state swap or a workspace hand-over
    // (ONE-APP-PLAN.md §2.3): every project this one process knows about is a path it already
    // serves, `/p/<slug>/`, so opening one is leaving `/p/<here>/` for `/p/<slug>/` the way a link
    // would. `current` never fires this — the row it names is where the picker already is.
    const select = (project) => {
      if (project.current) return;
      onOpenChange(false);
      window.location.assign(`../${project.slug}/`);
    };

    const filtered = projects.filter((p) =>
      p.name.toLowerCase().includes(query.trim().toLowerCase())
    );

    const content = h(
      'div',
      { className: 'sw-scope-pop' },
      // Creating a Project is a real, heavier flow (repo + seed + Domino project, ONE-APP-PLAN.md
      // Phase 3 step 3) with its own form — the Projects home at root scope, not this popover — so
      // the chip links there rather than reimplementing the form twice.
      h(
        'button',
        {
          className: 'sw-scope-pop-new',
          onClick: () => { onOpenChange(false); window.location.assign('../'); },
        },
        h(PlusOutlined, null),
        'New project'
      ),

      h(
        'div',
        { className: 'sw-scope-pop-search' },
        h(Input, {
          prefix: h(SearchOutlined, { style: { color: '#8F8FA3' } }),
          placeholder: 'Search projects…',
          value: query,
          allowClear: true,
          onChange: (e) => setQuery(e.target.value),
        })
      ),

      h(
        'div',
        { className: 'sw-scope-pop-list' },
        h('div', { className: 'sw-scope-pop-section' }, h('span', { className: 'sw-group-label' }, 'Recent')),
        filtered.length
          ? filtered.map((project) =>
              h(ScopeRow, { key: project.slug, project, onSelect: select })
            )
          : h(
              'div',
              { style: { padding: '8px 16px 12px' }, className: 'sw-secondary' },
              `No projects match "${query}".`
            )
      ),

      h(
        'div',
        { className: 'sw-scope-pop-footer' },
        h(
          Button,
          { type: 'link', size: 'small', style: { padding: 0 }, onClick: () => { onOpenChange(false); SW.router.go('#/gallery'); } },
          'See the apps your team has built'
        )
      )
    );

    return h(
      Popover,
      {
        open,
        onOpenChange,
        content,
        trigger: 'click',
        placement: 'bottomLeft',
        arrow: false,
        overlayInnerStyle: { padding: '12px 16px' },
      },
      h(
        Tooltip,
        { title: open ? '' : `Switch project · ${SW.util.shortcut('⌘P')}`, mouseEnterDelay: 0.6 },
        h(
          'button',
          {
            className: `sw-scope-chip${scopeFlash ? ' is-flashing' : ''}`,
          },
          h('span', { className: 'sw-scope-dot' }),
          h('span', { className: 'sw-scope-name' }, scope.name),
          h(DownOutlined, { style: { fontSize: 10, color: '#8F8FA3' } })
        )
      )
    );
  };
})();
