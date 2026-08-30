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
      // Subgroup labels are templates, resolved where they are drawn: this list is built when the
      // file is evaluated, which is before GET /api/brand has answered.
      subgroups: [
        { kind: 'dataset', label: '{datasetPlural}' },
        { kind: 'datasource', label: '{dataSourcePlural}' },
      ],
    },
    {
      key: 'model_llm',
      label: 'Language models',
      subgroups: [{ kind: 'model_llm' }],
    },
    {
      key: 'model_predictive',
      label: 'Predictive models',
      subgroups: [{ kind: 'model_predictive' }],
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
    model_llm: 'No language models here yet.',
    model_predictive: 'No predictive models here yet.',
    agents: 'No agents here yet.',
    skills: 'No skills here yet.',
    mcp: 'No MCPs here yet.',
  };

  const ERROR_KEYS = {
    data: ['datasets', 'data_sources'],
    model_llm: ['llm_aliases'],
    model_predictive: ['model_apis'],
  };

  // The plan the app is being built from: `.sage/plan.md` while it waits for approval, and the
  // plan the last build consumed once it has been. Opens the plan document behind it. A workspace
  // whose plan predates plan documents has no id to open, and there the raw markdown is still the
  // whole plan, so it opens in a modal instead of sending the user to a page that cannot load.
  function PlanCard({ plan }) {
    const [open, setOpen] = useState(false);

    if (!plan) {
      return h(
        'div',
        { className: 'sw-plan-pin is-empty' },
        h('span', { className: 'sw-plan-pin-label' }, 'Plan'),
        h(
          'span',
          { className: 'sw-plan-pin-empty' },
          `No plan yet. Ask ${SW.brand.assistant()} to draft one when the work is worth writing down.`
        )
      );
    }

    const built = plan.status === 'built';
    const steps = plan.steps === 1 ? '1 step' : `${plan.steps || 0} steps`;

    return h(
      Fragment,
      null,
      h(
        'button',
        {
          className: `sw-plan-pin${built ? ' is-blessed' : ''}`,
          onClick: () => (plan.planId ? SW.store.openPlanArtifact(plan.planId) : setOpen(true)),
        },
        h(
          'span',
          { className: 'sw-plan-pin-head' },
          h(FileTextOutlined, { style: { fontSize: 11 } }),
          h('span', { className: 'sw-plan-pin-label' }, built ? 'Working from' : 'Plan'),
          h('span', { className: 'sw-plan-pin-open' }, 'Open', h(ArrowRightOutlined, { style: { fontSize: 9 } }))
        ),
        // The name is one line and a plan title is a sentence, so it truncates. The tooltip is the
        // only way to read the rest without opening it.
        h('span', { className: 'sw-plan-pin-name', title: plan.title }, plan.title),
        h(
          'span',
          { className: 'sw-plan-pin-sub' },
          built ? `Built · ${steps}` : `Waiting for approval · ${steps}`
        )
      ),
      open &&
        h(
          antd.Modal,
          {
            open: true,
            // Not `plan.title` — that is the plan's own first line, which the body below already
            // renders. The transcript's plan card labels itself the same way for the same reason.
            title: built ? 'The plan this app was built from' : 'Plan, waiting for approval',
            footer: null,
            width: 640,
            onCancel: () => setOpen(false),
          },
          h('div', { className: 'sw-plan-md' }, SW.util.markdown(plan.markdown || ''))
        )
    );
  }

  function resourceFromAttachment(att) {
    return {
      id: att.resourceId || att.id,
      name: att.resourceName,
      kind: att.resourceKind || 'file',
      path: att.path,
      bindingKey: att.bindingKey,
      subtitle: att.addedBy === 'sage' ? `${SW.brand.assistant()} added this` : 'You added this',
    };
  }

  SW.ResourceRow = function ResourceRow({
    resource,
    required,
    highlighted,
    app,
    // The Built App this row is a record of, and the record itself: `{ app, binding }` or
    // `{ app, attachment }`. Only the "In this app" rows carry it, because only they are the list
    // that owns that scope — a Project row's removal is the Project's (ADR-0011).
    appScope,
    onOpen,
    contextItem,
    attached,
    expandable,
    expanded,
    onToggleExpand,
  }) {
    const [menuOpen, setMenuOpen] = useState(false);

    // Not the rail's Datasets: the server promotes a scratch file onto any Dataset this container
    // mounts writable (`_default_dataset`), so offering only the ones someone added to the project
    // greyed this out while the copy would have worked.
    const writableDatasets = SW.store.get().datasetTargets || [];
    const items = contextItem
      ? [{ key: 'remove-from-conversation', label: 'Stop using here' }]
      : [
          {
            key: attached ? 'remove-resource-from-conversation' : 'mention',
            label: attached ? 'Stop using here' : 'Use in this chat',
          },
          ...(resource.source === 'scratch'
            ? writableDatasets.length
              ? writableDatasets.map((d) => ({
                  key: `to-dataset:${d.id}`,
                  label: `Add to ${d.name}`,
                }))
              // A disabled item never fires onClick, so the reason has to be the label itself.
              : [{
                  key: 'to-dataset',
                  label: SW.brand.text('No writable {dataset} is mounted here'),
                  disabled: true,
                }]
            : []),
          // The third of the three scopes, beside the two this menu already named. Every label says
          // which list it acts on, because that is the only thing telling the three apart.
          ...(appScope
            ? [
                { type: 'divider' },
                { key: 'remove-from-app', label: `Remove from ${appScope.app.name}`, danger: true },
              ]
            : []),
          ...(resource.membershipParent
            ? [
                { type: 'divider' },
                { key: 'remove', label: `Remove from ${SW.store.get().scope.name}`, danger: true },
              ]
            : []),
        ];

    const onMenu = ({ key }) => {
      setMenuOpen(false);
      if (key === 'mention') return SW.store.addToContext(resource, { quiet: true });
      if (key === 'remove-from-conversation') return SW.store.removeFromConversation(contextItem);
      if (key === 'remove-resource-from-conversation') {
        return SW.store.removeResourceFromConversation(resource.id);
      }
      if (key === 'remove-from-app') {
        return appScope.binding
          ? SW.store.removeBindingFromApp(appScope.binding)
          : SW.store.removeAttachmentFromApp(appScope.attachment);
      }
      if (key === 'remove') return SW.store.removeFromProject(resource);
      if (key.startsWith('to-dataset:')) {
        return SW.store.addScratchToDataset(resource, key.slice('to-dataset:'.length).replace(/^dataset:/, ''));
      }
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
        expandable
          ? 'button'
          : 'span',
        expandable
          ? {
              className: 'sw-res-expand',
              'aria-expanded': !!expanded,
              'aria-label': expanded ? `Collapse ${resource.name}` : `Browse ${resource.name}`,
              onClick: (e) => {
                e.preventDefault();
                e.stopPropagation();
                if (onToggleExpand) onToggleExpand();
              },
            }
          : { className: 'sw-res-expand is-spacer' },
        expandable
          ? h(expanded ? DownOutlined : RightOutlined, { style: { fontSize: 9 } })
          : null
      ),
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
                SW.store.removeFromConversation(contextItem);
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
      resourceGroups, resourceErrors, activeApp, panelFilter, projectPlan, bindings, attachments,
      appAttachments, appRemoval, resourcesLoading,
    } = SW.store.get();
    const [query, setQuery] = useState('');
    const [collapsed, setCollapsed] = useState({});
    const [filesOpen, setFilesOpen] = useState(false);
    const fileRef = useRef(null);

    const [expandedId, setExpandedId] = useState(null);

    // What the selected app is bound to, keyed the way a Project row is (#99) — see
    // `SW.util.bindingId`. The "In this app" rows below take the same key, and they have to: the
    // two sections describe one list from two sides, so a Resource marked required up here and a
    // row down there are the same record or the panel is contradicting itself.
    const requiredIds = new Set((bindings || []).map((b) => SW.util.bindingId(b)));
    const attachedIds = new Set((attachments || []).map((a) => a.resourceId));
    const filterGroup = panelFilter && SW.util.RESOURCE_META[panelFilter]
      ? SW.util.RESOURCE_META[panelFilter].group
      : null;

    const matches = (r) => r.name.toLowerCase().includes(query.trim().toLowerCase());
    const visible = (kind) => (resourceGroups[kind] || []).filter(matches);
    const files = (resourceGroups.file || []).filter(matches);

    const inBuild = SW.router.get().mode === 'build';
    const inChat = SW.router.get().mode === 'chat';
    const needle = query.trim().toLowerCase();
    const kindForBinding = (kind) =>
      kind === 'data_source' ? 'datasource'
        : kind === 'llm_alias' ? 'model_llm'
        : kind === 'model_api' ? 'model_predictive'
        : kind;
    // Both of the app's lists, read out of the store rather than fetched: `refreshAppScope` assigns
    // them together with the app, so anything drawn here is already the selected app's (#95).
    const inApp = (bindings || []).filter((b) =>
      !needle || (b.display_name || b.name || '').toLowerCase().includes(needle)
    );
    const fileName = (a) => String(a.file || a.path || '').split('/').pop();
    const inAppFiles = (appAttachments || []).filter((a) =>
      !needle || fileName(a).toLowerCase().includes(needle)
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
        { key: 'browse', label: SW.brand.text('Browse {platformName}…') },
        { key: 'upload', label: 'Upload a file' },
        // "a new {dataSource}", not "a {dataSource}": there is no article engine, and the article
        // has to sit against a word the pack cannot change. The toast below already said "new".
        { key: 'connect', label: SW.brand.text('Connect a new {dataSource}') },
      ],
      onClick: ({ key }) => {
        if (key === 'browse') return SW.store.openCatalog();
        if (key === 'upload') return fileRef.current && fileRef.current.click();
        return antd.message.info(
          SW.brand.text('Connecting a new {dataSource} is not wired up in this prototype.')
        );
      },
    };

    const total = GROUPS.reduce(
      (acc, g) => acc + g.subgroups.reduce((n, s) => n + visible(s.kind).length, 0),
      0
    );

    // Both group labels, always, even over an empty kind. The Build header omits one for the
    // opposite reason: it is a glance, and naming a kind with nothing in it says the app ships
    // something it does not. This is where the two words are learned and where someone arrived
    // intending to act, so "Attachments — none" answers the question they came with (ADR-0011).
    //
    // `held` is what the APP records, not what the filter left: a search matching nothing does not
    // make the app's list empty, and a label saying it did would be the wrong answer to a question
    // about the app.
    const appGroup = (label, held, rows) =>
      h(
        Fragment,
        { key: label },
        h(
          'div',
          { className: 'sw-res-subgroup' },
          h('span', { className: 'sw-group-label sw-app-group' }, held ? label : `${label} — none`)
        ),
        rows
      );

    const bindingRow = (b) =>
      h(SW.ResourceRow, {
        key: SW.util.bindingId(b),
        resource: {
          id: SW.util.bindingId(b),
          name: b.display_name || b.name,
          kind: kindForBinding(b.kind),
          subtitle: (b.kind || '').replace(/_/g, ' '),
        },
        // These rows ARE the app's list — the literal is them describing themselves, and it keeps
        // the marker the Project rows get. No `app`, though: with one, the subtitle would read
        // "Required by this app" under a head already naming the app, so the row would repeat its
        // own section instead of naming its kind (#99).
        required: true,
        appScope: activeApp ? { app: activeApp, binding: b } : null,
        onOpen: () => {},
      });

    const fileRow = (a) =>
      h(SW.ResourceRow, {
        key: a.path,
        resource: {
          id: `file:${a.path}`,
          name: fileName(a),
          kind: 'file',
          // The Dataset the bytes stay in, which is also the half the removal can promise. Keyed on
          // `dataset_id` for the reason `removeAttachmentFromApp` gives: a rehydrated entry still
          // carries a `dataset`, filled from the symlink's parent directory, and printing that as a
          // Dataset name would name a source the entry does not have.
          subtitle: a.dataset_id ? a.dataset : a.path,
        },
        required: true,
        appScope: activeApp ? { app: activeApp, attachment: a } : null,
        onOpen: () => {},
      });

    const rowFor = (resource) => {
      const expandable = resource.membershipParent
        && (resource.kind === 'dataset' || resource.kind === 'datasource');
      const expanded = expandable && expandedId === resource.id;
      return h(
        Fragment,
        { key: resource.id },
        h(SW.ResourceRow, {
          resource,
          required: requiredIds.has(resource.id),
          app: activeApp,
          attached: attachedIds.has(resource.id),
          highlighted: Boolean(panelFilter) && SW.util.RESOURCE_META[resource.kind]
            && SW.util.RESOURCE_META[resource.kind].group === filterGroup,
          onOpen: inChat ? addFromPanel : openResource,
          expandable,
          expanded,
          onToggleExpand: () => setExpandedId(expanded ? null : resource.id),
        }),
        expanded &&
          h(SW.ResourceTree, { resource, query: needle, variant: 'rail' })
      );
    };

    return h(
      'div',
      { className: 'sw-panel' },

      inBuild &&
        h(PlanCard, { plan: projectPlan }),

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

      // Headed by the app rather than by "this app": a Project holds many, and ADR-0008 makes which
      // one a question every surface has to answer. This one has to twice over — it is where #92's
      // header pointers land, and a pointer is a promise that the destination can act.
      inBuild &&
        h(
          'div',
          { className: 'sw-panel-section-head' },
          h(
            'span',
            { className: 'sw-panel-section-title' },
            `In ${activeApp ? activeApp.name : 'this app'}`
          ),
          h('span', { className: 'sw-panel-section-count' }, inApp.length + inAppFiles.length)
        ),
      inBuild &&
        h(
          'div',
          { className: 'sw-in-app' },
          // What the last removal reported, after the act. Here rather than in a toast because it
          // is only worth having if it can be acted on, and five seconds is not long enough to read
          // a file list and decide (ADR-0011).
          appRemoval &&
            h(
              'div',
              { className: 'sw-panel-hint sw-app-notice' },
              h('span', { className: 'sw-app-notice-text' }, appRemoval.text),
              // Writes the prompt into the composer and stops. Firing the turn from here could be
              // refused by the turn lock, and would put work past a plan gate nobody read.
              appRemoval.prompt &&
                h(
                  Button,
                  {
                    type: 'link',
                    size: 'small',
                    style: { padding: 0, height: 'auto' },
                    onClick: () => SW.store.seedComposer(appRemoval.prompt),
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
          (bindings || []).length === 0 && (appAttachments || []).length === 0
            ? h('div', { className: 'sw-caption' }, SW.util.appScopeEmpty('Nothing yet.'))
            : [
                appGroup('Bindings', (bindings || []).length, inApp.map(bindingRow)),
                appGroup('Attachments', (appAttachments || []).length, inAppFiles.map(fileRow)),
              ]
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
                        SW.brand.text('Add from {platformName}')
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
                              h('span', { className: 'sw-group-label' }, SW.brand.text(sub.label))
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
              h('div', { className: 'sw-drawer-hint' },
                SW.brand.text('Files in this workspace. {dataset} contents live under the {dataset}.')),
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
            const files = Array.from(e.target.files || []);
            e.target.value = '';
            for (const file of files) await SW.store.uploadFile(file);
          },
        })
      )
    );
  };
})();
