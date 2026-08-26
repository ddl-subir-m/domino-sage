window.SW = window.SW || {};

(function () {
  const { createElement: h, useState, useRef, Fragment } = React;
  const { Input, Tooltip, Button, Tag, Dropdown } = antd;
  const {
    SearchOutlined, DownOutlined, RightOutlined, PlusOutlined, MoreOutlined,
    FolderOutlined, FileTextOutlined, ArrowRightOutlined, CloseOutlined,
  } = icons;

  // What the caller can pick now. Agents / Skills / MCPs stay visible until OpenCode config wires them.
  const GROUPS = [
    {
      key: 'data',
      label: 'Data',
      subgroups: [
        { kind: 'dataset', label: 'Datasets' },
        { kind: 'datasource', label: 'Data sources' },
      ],
    },
    {
      key: 'models',
      label: 'Models',
      subgroups: [
        { kind: 'model_llm', label: 'Language models' },
        { kind: 'model_predictive', label: 'Predictive models' },
      ],
    },
    {
      key: 'agents',
      label: 'Agents',
      placeholder: true,
      subgroups: [{ kind: 'agent' }],
    },
    {
      key: 'skills',
      label: 'Skills',
      placeholder: true,
      subgroups: [{ kind: 'skill' }],
    },
    {
      key: 'mcp',
      label: 'MCPs',
      placeholder: true,
      subgroups: [{ kind: 'mcp' }],
    },
  ];

  const EMPTY_HINT = {
    data: 'No data here yet.',
    models: 'No models here yet.',
    agents: 'No agents here yet.',
    skills: 'No skills here yet.',
    mcp: 'No MCPs here yet.',
  };

  const ERROR_KEYS = {
    data: ['datasets', 'data_sources'],
    models: ['llm_aliases', 'model_apis'],
  };

  function PlanCard({ plan, blessed }) {
    if (!plan) {
      return h(
        'div',
        { className: 'sw-plan-pin is-empty' },
        h('span', { className: 'sw-plan-pin-label' }, 'Plan'),
        h(
          'span',
          { className: 'sw-plan-pin-empty' },
          'No plan yet. Ask Sage to draft one when the work is worth writing down.'
        )
      );
    }

    return h(
      'button',
      {
        className: `sw-plan-pin${blessed ? ' is-blessed' : ''}`,
        onClick: () => SW.store.openPlanArtifact(plan.id),
      },
      h(
        'span',
        { className: 'sw-plan-pin-head' },
        h(FileTextOutlined, { style: { fontSize: 11 } }),
        h('span', { className: 'sw-plan-pin-label' }, blessed ? 'Working from' : 'Plan'),
        h('span', { className: 'sw-plan-pin-open' }, 'Open', h(ArrowRightOutlined, { style: { fontSize: 9 } }))
      ),
      h('span', { className: 'sw-plan-pin-name' }, plan.name),
      h('span', { className: 'sw-plan-pin-sub' }, plan.subtitle)
    );
  }

  function resourceFromAttachment(att) {
    return {
      id: att.resourceId || att.id,
      name: att.resourceName,
      kind: att.resourceKind || 'file',
      path: att.path,
      bindingKey: att.bindingKey,
      subtitle: att.addedBy === 'sage' ? 'Sage added this' : 'You added this',
    };
  }

  SW.ResourceRow = function ResourceRow({
    resource,
    required,
    highlighted,
    app,
    onOpen,
    contextItem,
    attached,
    allowAppActs,
  }) {
    const [menuOpen, setMenuOpen] = useState(false);

    const items = contextItem
      ? [{ key: 'detach', label: 'Remove from this conversation' }]
      : [
          {
            key: attached ? 'detach-resource' : 'mention',
            label: attached ? 'Remove from this conversation' : 'Add to this conversation',
          },
          ...(allowAppActs && app
            ? [
                {
                  key: required ? 'demote' : 'promote',
                  label: required ? `${app.name} no longer needs this` : `${app.name} needs this to run`,
                },
              ]
            : []),
          ...(resource.fromCatalog
            ? [
                { type: 'divider' },
                { key: 'remove', label: `Remove from ${SW.store.get().scope.name}`, danger: true },
              ]
            : []),
        ];

    const onMenu = ({ key }) => {
      setMenuOpen(false);
      if (key === 'mention') return SW.store.addToContext(resource, { quiet: true });
      if (key === 'detach') return SW.store.detach(contextItem);
      if (key === 'detach-resource') return SW.store.detachResource(resource.id);
      if (key === 'promote') return SW.store.promoteResource(resource);
      if (key === 'demote') return SW.store.demoteResource(resource);
      if (key === 'remove') return SW.store.removeFromProject(resource);
      return undefined;
    };

    const used = resource.usedBy || [];
    const showsDependants = ['model_llm', 'model_predictive', 'tool', 'agent', 'skill'].includes(
      resource.kind
    );
    const secondary = required && app
      ? `Required by ${app.name}`
      : showsDependants && used.length
      ? `Used by ${used.length} ${used.length === 1 ? 'app' : 'apps'}`
      : resource.subtitle;

    return h(
      'div',
      {
        className:
          'sw-res-row' +
          (required ? ' is-required' : '') +
          (highlighted ? ' is-highlighted' : '') +
          (contextItem ? ' is-context' : '') +
          (menuOpen ? ' is-menu-open' : ''),
      },
      h(
        'button',
        { className: 'sw-res-open', onClick: () => onOpen(resource) },
        h('span', { className: 'sw-res-icon' }, SW.util.iconFor(resource.kind)),
        h(
          'span',
          { className: 'sw-res-main' },
          h(
            'span',
            { className: 'sw-res-name-line' },
            h('span', { className: 'sw-res-name' }, resource.name)
          ),
          secondary &&
            h(
              Tooltip,
              { title: used.length ? used.join(', ') : null, mouseEnterDelay: 0.4 },
              h('span', { className: 'sw-res-sub' }, secondary)
            )
        ),
        resource.sovereign &&
          h(
            Tooltip,
            { title: 'Runs inside your environment.' },
            h(Tag, { bordered: false, className: 'sw-sens sw-sens-internal' }, 'sovereign')
          )
      ),
      contextItem
        ? h(
            'button',
            {
              className: 'sw-res-more',
              'aria-label': `Remove ${resource.name} from this conversation`,
              onClick: (e) => {
                e.preventDefault();
                e.stopPropagation();
                SW.store.detach(contextItem);
              },
            },
            h(CloseOutlined, { style: { fontSize: 10 } })
          )
        : h(
            Dropdown,
            {
              menu: { items, onClick: onMenu },
              trigger: ['click'],
              open: menuOpen,
              onOpenChange: setMenuOpen,
              placement: 'bottomRight',
            },
            h(
              'button',
              { className: 'sw-res-more', 'aria-label': `Actions for ${resource.name}` },
              h(MoreOutlined, null)
            )
          )
    );
  };

  SW.ResourcePanel = function ResourcePanel() {
    const {
      resourceGroups, resourceErrors, requires, activeApp, panelFilter, activePlanId, bindings, attachments,
      resourcesLoading,
    } = SW.store.get();
    const [query, setQuery] = useState('');
    const [collapsed, setCollapsed] = useState({});
    const [filesOpen, setFilesOpen] = useState(false);
    const fileRef = useRef(null);

    const requiredIds = new Set(requires.map((r) => r.resourceId));
    const attachedIds = new Set((attachments || []).map((a) => a.resourceId));
    const filterGroup = panelFilter && SW.util.RESOURCE_META[panelFilter]
      ? SW.util.RESOURCE_META[panelFilter].group
      : null;

    const matches = (r) => r.name.toLowerCase().includes(query.trim().toLowerCase());
    const visible = (kind) => (resourceGroups[kind] || []).filter(matches);
    const files = (resourceGroups.file || []).filter(matches);

    const plans = resourceGroups.plan || [];
    const pinned = plans.find((p) => p.id === activePlanId) || plans[0] || null;
    const inBuild = SW.router.get().mode === 'build';
    const inChat = SW.router.get().mode === 'chat';
    const needle = query.trim().toLowerCase();
    const kindForBinding = (kind) =>
      kind === 'data_source' ? 'datasource'
        : kind === 'llm_alias' ? 'model_llm'
        : kind === 'model_api' ? 'model_predictive'
        : kind;
    const inApp = (bindings || []).filter((b) =>
      !needle || (b.display_name || b.name || '').toLowerCase().includes(needle)
    );

    const openResource = (resource) =>
      resource.kind === 'plan'
        ? SW.store.openPlanArtifact(resource.id)
        : SW.store.previewResource(resource.id);

    const addFromPanel = (resource) => {
      if (attachedIds.has(resource.id)) return openResource(resource);
      return SW.store.addToContext(resource, { quiet: true });
    };

    const addMenu = {
      items: [
        { key: 'browse', label: `Browse Domino…` },
        { key: 'upload', label: 'Upload a file' },
        { key: 'connect', label: 'Connect a data source' },
      ],
      onClick: ({ key }) => {
        if (key === 'browse') return SW.store.openCatalog();
        if (key === 'upload') return fileRef.current && fileRef.current.click();
        return antd.message.info('Connecting a new data source is not wired up in this prototype.');
      },
    };

    const total = GROUPS.reduce(
      (acc, g) => acc + g.subgroups.reduce((n, s) => n + visible(s.kind).length, 0),
      0
    );

    const rowFor = (resource) =>
      h(SW.ResourceRow, {
        key: resource.id,
        resource,
        required: requiredIds.has(resource.id),
        app: activeApp,
        attached: attachedIds.has(resource.id),
        allowAppActs: inBuild,
        highlighted: Boolean(panelFilter) && SW.util.RESOURCE_META[resource.kind]
          && SW.util.RESOURCE_META[resource.kind].group === filterGroup,
        onOpen: inChat ? addFromPanel : openResource,
      });

    return h(
      'div',
      { className: 'sw-panel' },

      inBuild &&
        h(PlanCard, { plan: pinned, blessed: Boolean(pinned && pinned.id === activePlanId) }),

      inChat &&
        h(
          'div',
          { className: 'sw-panel-section-head' },
          h('span', { className: 'sw-panel-section-title' }, 'In context'),
          h('span', { className: 'sw-panel-section-count' }, (attachments || []).length)
        ),
      inChat &&
        h(
          'div',
          { className: 'sw-in-context' },
          (attachments || []).length === 0
            ? h(
                'div',
                { className: 'sw-caption' },
                'Nothing in this conversation yet. Add a resource from the list below, or type @.'
              )
            : attachments.map((att) =>
                h(SW.ResourceRow, {
                  key: att.id,
                  resource: resourceFromAttachment(att),
                  contextItem: att,
                  onOpen: openResource,
                })
              )
        ),

      inBuild &&
        h(
          'div',
          { className: 'sw-panel-section-head' },
          h('span', { className: 'sw-panel-section-title' }, 'In this app'),
          h('span', { className: 'sw-panel-section-count' }, inApp.length)
        ),
      inBuild &&
        h(
          'div',
          { className: 'sw-in-app' },
          inApp.length === 0
            ? h('div', { className: 'sw-caption' }, 'Nothing recorded yet. Bindings from Chat land here after Open Builder.')
            : inApp.map((b) =>
                h(SW.ResourceRow, {
                  key: `${b.kind}:${b.id}`,
                  resource: {
                    id: `${b.kind}:${b.id}`,
                    name: b.display_name || b.name,
                    kind: kindForBinding(b.kind),
                    subtitle: (b.kind || '').replace(/_/g, ' '),
                  },
                  required: true,
                  app: { name: 'this app' },
                  allowAppActs: false,
                  onOpen: () => {},
                })
              )
        ),

      h(
        'div',
        { className: 'sw-panel-section-head' },
        h('span', { className: 'sw-panel-section-title' }, 'Project resources'),
        h('span', { className: 'sw-panel-section-count' }, resourcesLoading ? '…' : total),
        h(
          Dropdown,
          { menu: addMenu, trigger: ['click'], placement: 'bottomRight' },
          h(
            Button,
            { size: 'small', type: 'primary', icon: h(PlusOutlined, { style: { fontSize: 10 } }) },
            'Add resources'
          )
        )
      ),

      h(
        'div',
        { className: 'sw-panel-search' },
        h(Input, {
          prefix: h(SearchOutlined, { style: { color: '#8F8FA3' } }),
          placeholder: 'Filter this project…',
          value: query,
          allowClear: true,
          size: 'small',
          onChange: (e) => setQuery(e.target.value),
        })
      ),

      panelFilter &&
        h(
          'div',
          { className: 'sw-panel-hint' },
          h('span', null, `Pick a ${SW.util.labelFor(panelFilter)} to continue`),
          h(
            Button,
            {
              type: 'link',
              size: 'small',
              style: { padding: 0, height: 'auto' },
              onClick: () => SW.store.clearPanelFilter(),
            },
            'Dismiss'
          )
        ),

      h(
        'div',
        { className: 'sw-panel-body sw-scroll' },

        GROUPS.map((group) => {
          const items = group.subgroups.map((sub) => ({ sub, rows: visible(sub.kind) }));
          const count = items.reduce((acc, i) => acc + i.rows.length, 0);
          const isCollapsed = collapsed[group.key];
          const listingError = (ERROR_KEYS[group.key] || [])
            .map((k) => (resourceErrors || {})[k])
            .find(Boolean);

          return h(
            Fragment,
            { key: group.key },
            h(
              'div',
              {
                className: 'sw-res-group-label',
                onClick: () => setCollapsed({ ...collapsed, [group.key]: !isCollapsed }),
                role: 'button',
              },
              h(isCollapsed ? RightOutlined : DownOutlined, { style: { fontSize: 9, color: '#8F8FA3' } }),
              h('span', { className: 'sw-group-label' }, `${group.label} (${resourcesLoading ? '…' : count})`)
            ),
            !isCollapsed &&
              (count === 0
                ? h(
                    'div',
                    { className: 'sw-group-empty' },
                    query.trim()
                      ? 'Nothing matches here.'
                      : resourcesLoading
                        ? 'Loading this project…'
                        : (listingError || EMPTY_HINT[group.key]),
                    !query.trim() && !resourcesLoading && !group.placeholder && !listingError &&
                      h(
                        Button,
                        {
                          type: 'link',
                          size: 'small',
                          style: { padding: 0, height: 'auto', fontSize: 12 },
                          onClick: () => SW.store.openCatalog(group.subgroups[0].kind),
                        },
                        'Add from Domino'
                      )
                  )
                : items.map(({ sub, rows }) =>
                    rows.length
                      ? h(
                          Fragment,
                          { key: sub.kind },
                          sub.label &&
                            items.filter((i) => i.rows.length).length > 1 &&
                            h(
                              'div',
                              { className: 'sw-res-subgroup' },
                              h('span', { className: 'sw-group-label' }, sub.label)
                            ),
                          rows.map(rowFor)
                        )
                      : null
                  ))
          );
        }),

        h(
          'section',
          { className: `sw-drawer${filesOpen ? ' is-open' : ''}` },
          h(
            'button',
            {
              className: 'sw-drawer-head',
              onClick: () => setFilesOpen(!filesOpen),
              'aria-expanded': filesOpen,
            },
            h(filesOpen ? DownOutlined : RightOutlined, { style: { fontSize: 9 } }),
            h(FolderOutlined, { style: { fontSize: 12, color: '#8F8FA3' } }),
            h('span', { className: 'sw-drawer-title' }, 'Files'),
            h('span', { className: 'sw-drawer-count' }, files.length)
          ),
          filesOpen &&
            h(
              'div',
              { className: 'sw-drawer-body' },
              h('div', { className: 'sw-drawer-hint' }, 'The project working tree.'),
              files.length
                ? files.map(rowFor)
                : h('div', { className: 'sw-group-empty' }, 'No files in this project yet.')
            )
        ),

        h('input', {
          ref: fileRef,
          type: 'file',
          multiple: true,
          style: { display: 'none' },
          onChange: async (e) => {
            const names = Array.from(e.target.files || []).map((f) => f.name);
            e.target.value = '';
            for (const name of names) await SW.store.uploadFile(name);
          },
        })
      )
    );
  };
})();
