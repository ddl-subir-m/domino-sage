window.SW = window.SW || {};

(function () {
  const { createElement: h, useState, useRef, useEffect } = React;
  const { Popover, Input, Button, Tooltip } = antd;
  const { PlusOutlined, DownOutlined, SearchOutlined } = icons;

  function ScopeRow({ project, active, onSelect }) {
    return h(
      'button',
      { className: `sw-scope-row${active ? ' is-active' : ''}`, onClick: () => onSelect(project) },
      h('span', { className: 'sw-scope-dot', style: { background: project.color } }),
      h(
        'span',
        { className: 'sw-scope-row-main' },
        h('span', { className: 'sw-scope-row-name' }, project.name),
        h(
          'span',
          { className: 'sw-scope-row-meta' },
          `${project.ownerName} · ${project.memberCount} ${project.memberCount === 1 ? 'member' : 'members'}`
        )
      ),
      h(
        'span',
        { className: 'sw-scope-row-count' },
        `${project.appCount} ${project.appCount === 1 ? 'app' : 'apps'}`
      )
    );
  }

  SW.ScopePicker = function ScopePicker({ open, onOpenChange }) {
    const { scope, projects, scopeFlash } = SW.store.get();
    const [query, setQuery] = useState('');
    const [creating, setCreating] = useState(false);
    const [name, setName] = useState('');
    const nameRef = useRef(null);

    useEffect(() => {
      if (creating && nameRef.current) nameRef.current.focus();
    }, [creating]);

    useEffect(() => {
      if (!open) {
        setCreating(false);
        setName('');
        setQuery('');
      }
    }, [open]);

    const select = async (project) => {
      onOpenChange(false);
      await SW.store.setScope(project);
    };

    const create = async () => {
      const trimmed = name.trim();
      if (!trimmed) return;
      onOpenChange(false);
      setCreating(false);
      setName('');
      await SW.store.createProject(trimmed);
    };

    const filtered = projects.filter((p) =>
      p.name.toLowerCase().includes(query.trim().toLowerCase())
    );

    const content = h(
      'div',
      { className: 'sw-scope-pop' },
      creating
        ? h(
            'div',
            { style: { padding: '10px 16px', display: 'flex', gap: 8 } },
            h(Input, {
              ref: nameRef,
              placeholder: 'Project name',
              value: name,
              onChange: (e) => setName(e.target.value),
              onPressEnter: create,
              maxLength: 60,
            }),
            h(Button, { type: 'primary', onClick: create, disabled: !name.trim() }, 'Create')
          )
        : h(
            'button',
            { className: 'sw-scope-pop-new', onClick: () => setCreating(true) },
            h(PlusOutlined, null),
            'New project',
            h('kbd', null, '⏎')
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
              h(ScopeRow, {
                key: project.id,
                project,
                active: scope.id === project.id,
                onSelect: select,
              })
            )
          : h(
              'div',
              { style: { padding: '8px 16px 12px' }, className: 'sw-secondary' },
              `No projects match "${query}". Create one above.`
            )
      ),

      h(
        'div',
        { className: 'sw-scope-pop-footer' },
        h(
          Button,
          { type: 'link', size: 'small', style: { padding: 0 }, onClick: () => { onOpenChange(false); SW.router.go('#/gallery'); } },
          'Browse all projects in the gallery'
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
        { title: open ? '' : 'Switch project · ⌘P', mouseEnterDelay: 0.6 },
        h(
          'button',
          {
            className: `sw-scope-chip${scopeFlash ? ' is-flashing' : ''}`,
          },
          h('span', {
            className: 'sw-scope-dot',
            style: { background: scope.color },
          }),
          h('span', { className: 'sw-scope-name' }, scope.name),
          h(DownOutlined, { style: { fontSize: 10, color: '#8F8FA3' } })
        )
      )
    );
  };
})();
