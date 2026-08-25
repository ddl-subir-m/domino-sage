window.SW = window.SW || {};

(function () {
  const { createElement: h, useState, useRef, Fragment } = React;
  const { Input, Tooltip, Button, Tag, Dropdown } = antd;
  const {
    SearchOutlined, DownOutlined, RightOutlined, PlusOutlined, MoreOutlined,
    FolderOutlined, FileTextOutlined, ArrowRightOutlined,
  } = icons;

  // The project's working set, by category. Plans are not here — the one that
  // matters is pinned above, and the rest are history. Files are not here
  // either; they get their own drawer at the bottom, because files only matter
  // once you are coding.
  const GROUPS = [
    {
      key: 'data',
      label: 'Data',
      subgroups: [
        { kind: 'dataset', label: 'Datasets' },
        { kind: 'table', label: 'Database tables' },
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
    { key: 'tools', label: 'Tools', subgroups: [{ kind: 'tool', label: null }] },
    { key: 'agents', label: 'Agents', subgroups: [{ kind: 'agent', label: null }] },
    { key: 'skills', label: 'Skills', subgroups: [{ kind: 'skill', label: null }] },
  ];

  const EMPTY_HINT = {
    data: 'No data here yet.',
    models: 'No models here yet.',
    tools: 'No tools here yet.',
    agents: 'No agents here yet.',
    skills: 'No skills here yet — add some so builds follow your conventions.',
  };

  // The plan is the project's north star, so it sits above the list rather than
  // as row one of an accordion. A project's other plans are history and live in
  // the plan list, not here.
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

  SW.ResourceRow = function ResourceRow({ resource, required, highlighted, app, onOpen }) {
    const [menuOpen, setMenuOpen] = useState(false);

    // What an app needs is a fact about the app, not about the project, so it
    // rides along as a badge on the project's row instead of duplicating the
    // row into a second list.
    const items = [
      { key: 'mention', label: 'Mention in this chat' },
      ...(app
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
      if (key === 'mention') return SW.store.addToContext(resource);
      if (key === 'promote') return SW.store.promoteResource(resource);
      if (key === 'demote') return SW.store.demoteResource(resource);
      if (key === 'remove') return SW.store.removeFromProject(resource);
      return undefined;
    };

    // A model, tool, or agent reads very differently once the row says what
    // breaks without it, so dependants win over the spec line there. For data
    // the spec line is the more useful fact, and its dependants show up in the
    // preview instead.
    //
    // Counted rather than named, because app names are long and the panel is
    // narrow — a name that truncates to "Used by Limit Alert Rou…" tells you
    // less than a number does. The names are in the tooltip.
    const used = resource.usedBy || [];
    const showsDependants = ['model_llm', 'model_predictive', 'tool', 'agent', 'skill'].includes(
      resource.kind
    );
    // Being a dependency of the app you are building outranks everything else
    // this line could say. It also goes here rather than beside the name, where
    // a badge would eat the width the name needs.
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
          ),
        // A model is not data, so its own sensitivity label says nothing useful.
        // What matters is how sensitive the data it may touch can be, and that
        // needs more room than a row has — the preview carries it.
        resource.kind !== 'model_llm' &&
          h(SW.SensitivityTag, { level: resource.sensitivity, short: true })
      ),
      h(
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
    const { resourceGroups, requires, activeApp, panelFilter, activePlanId, bindings } = SW.store.get();
    const [query, setQuery] = useState('');
    const [collapsed, setCollapsed] = useState({});
    const [filesOpen, setFilesOpen] = useState(false);
    const fileRef = useRef(null);

    const requiredIds = new Set(requires.map((r) => r.resourceId));
    const filterGroup = panelFilter && SW.util.RESOURCE_META[panelFilter]
      ? SW.util.RESOURCE_META[panelFilter].group
      : null;

    const matches = (r) => r.name.toLowerCase().includes(query.trim().toLowerCase());
    const visible = (kind) => (resourceGroups[kind] || []).filter(matches);
    const files = (resourceGroups.file || []).filter(matches);

    // The plan the current work is anchored to, falling back to the project's
    // most recently touched one.
    const plans = resourceGroups.plan || [];
    const pinned = plans.find((p) => p.id === activePlanId) || plans[0] || null;
    const inBuild = SW.router.get().mode === 'build';
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
        highlighted: Boolean(panelFilter) && SW.util.RESOURCE_META[resource.kind].group === filterGroup,
        onOpen: openResource,
      });

    return h(
      'div',
      { className: 'sw-panel' },

      h(PlanCard, { plan: pinned, blessed: Boolean(pinned && pinned.id === activePlanId) }),

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
                  onOpen: () => {},
                })
              )
        ),

      h(
        'div',
        { className: 'sw-panel-section-head' },
        // The scope chip above already names the project, so repeating it here
        // only makes the header wrap.
        h('span', { className: 'sw-panel-section-title' }, 'In this project'),
        h('span', { className: 'sw-panel-section-count' }, total),
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
              h('span', { className: 'sw-group-label' }, `${group.label} (${count})`)
            ),
            !isCollapsed &&
              (count === 0
                ? h(
                    'div',
                    { className: 'sw-group-empty' },
                    query.trim() ? 'Nothing matches here.' : EMPTY_HINT[group.key],
                    !query.trim() &&
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
