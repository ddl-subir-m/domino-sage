window.SW = window.SW || {};

(function () {
  const { createElement: h, useState, useRef, useEffect } = React;
  const { Popover, Input, Button, Tooltip } = antd;
  const { PlusOutlined, DownOutlined, SearchOutlined } = icons;

  // Only the project this builder is bound to can be described from here — the others are a name
  // and an id to attach by, so the row says what picking it does rather than inventing counts.
  function ScopeRow({ project, onSelect }) {
    return h(
      'button',
      {
        className: `sw-scope-row${project.current ? ' is-active' : ''}`,
        onClick: () => onSelect(project),
      },
      h('span', { className: 'sw-scope-dot', style: { background: project.color } }),
      h(
        'span',
        { className: 'sw-scope-row-main' },
        h('span', { className: 'sw-scope-row-name' }, project.name)
      ),
      h(
        'span',
        { className: 'sw-scope-row-count' },
        project.current ? 'You are here' : 'Open'
      )
    );
  }

  SW.ScopePicker = function ScopePicker({ open, onOpenChange }) {
    const { scope, projects, scopeFlash, canProvision } = SW.store.get();
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
      await SW.store.attachProject(project);
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
            Tooltip,
            {
              // Say why it can't be used, rather than offering a button that fails on click.
              title: canProvision ? '' : SW.brand.text('{assistantName} can’t reach {platformName} '
                + 'from this container, so it can’t create a {project}.'),
              placement: 'right',
            },
            h(
              'button',
              {
                className: 'sw-scope-pop-new',
                disabled: !canProvision,
                onClick: () => setCreating(true),
              },
              h(PlusOutlined, null),
              'New project',
              h('kbd', null, '⏎')
            )
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
              h(ScopeRow, { key: project.id, project, onSelect: select })
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
