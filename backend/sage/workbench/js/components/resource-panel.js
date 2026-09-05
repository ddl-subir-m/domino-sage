window.SW = window.SW || {};

(function () {
  const { createElement: h, useState, useRef, Fragment } = React;
  const { Tooltip, Button, Tag, Dropdown } = antd;
  const {
    DownOutlined, RightOutlined, PlusOutlined, MoreOutlined, DoubleRightOutlined,
    ArrowRightOutlined, CloseOutlined, CheckCircleFilled, InboxOutlined,
  } = icons;

  // What the caller can pick now. Agents / Skills / MCPs are still listed because OpenCode config
  // will wire them; they draw nothing until it does, which is the point of the rule below.
  //
  // A group is drawn only when it HOLDS something, or when its listing failed. Empty headings were
  // the panel's loudest noise: six subheadings over nothing, in a 320px rail whose whole job is to
  // show what the Project has. The error half of that rule is not a nicety — `GET /api/resources`
  // carries its reason per kind on purpose ("each group in the rail renders its own list or its own
  // reason"), and a group hidden for being empty when it is really unknown would turn "the gateway
  // is not answering" into "you have no models".
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
    // `placeholder` is "no catalog behind this yet", and it is what the group's add door is gated
    // on (#164). These three draw nothing until OpenCode config wires them, so under the
    // draw-only-when-held rule below they are invisible today — the flag is what keeps the door
    // from appearing on the day they are not.
    { key: 'agents', label: 'Agents', placeholder: true, subgroups: [{ kind: 'agent' }] },
    { key: 'skills', label: 'Skills', placeholder: true, subgroups: [{ kind: 'skill' }] },
    { key: 'mcp', label: 'MCPs', placeholder: true, subgroups: [{ kind: 'mcp' }] },
    // The Project's own Uploads. It was a collapsible drawer pinned to the bottom of the panel —
    // its own pattern, its own chevron, its own empty sentence — for a list that behaves like every
    // other group. Folded in here: one pattern, and it disappears when there are no files, which
    // the drawer never did.
    //
    // `placeholder` for the opposite reason to the three above: a file does not come from the
    // catalog at all, it comes from Upload, so `openCatalog('file')` has nothing to open. The head
    // draws no `+`; the panel's own Add menu carries Upload a file.
    { key: 'file', label: 'Files', placeholder: true, subgroups: [{ kind: 'file' }] },
  ];

  // Which filter a group's add door pre-selects in the catalog. One helper for both doors, so the
  // two cannot disagree. A group holding two kinds asks for its GROUP — the catalog's sidebar now
  // carries an entry per group as well as per kind, so `Data` is askable. It used to open on
  // Everything, because the only alternative then was `subgroups[0].kind`, which silently meant
  // Datasets and hid Data Sources behind a filter the caller never chose (#164).
  const addKind = (group) => (group.subgroups.length === 1 ? group.subgroups[0].kind : group.key);

  // `EMPTY_HINT` went with the branch that held it. A group with nothing in it is not drawn at all
  // now, so there is no row left to say "No language models here yet." over — and the sentence a
  // wholly empty project needs is the panel's own empty state, said once.

  const ERROR_KEYS = {
    data: ['datasets', 'data_sources'],
    model_llm: ['llm_aliases'],
    model_predictive: ['model_apis'],
  };

  SW.ResourceRow = function ResourceRow({
    resource,
    required,
    highlighted,
    app,
    // Whether this row's subtitle answers "does the selected app use this". The SIGN, and all that
    // is left of the pair it was half of: `canBind` put the ADD on this row's menu until #144, and
    // that act is on the Built App's own surface now (ADR-0021). Set by the Project list only; see
    // the call site.
    saysAppUse,
    onOpen,
    contextItem,
    // Whether this Resource is in the Conversation's context. Drawn as a mark, never as the act:
    // the chips over the composer are where context is shown and taken back (#137), and this row
    // only says whether the thing you are looking at is one of them.
    inContext,
    // Chat only. The cheap half of the pair the mark leaves open — one click to put a row into the
    // Conversation, which is a Session-context door and so one the panel is allowed to own
    // (ADR-0021's table). Build has no verb for it (#147), so Build passes none.
    onAddToContext,
    // A single always-visible act, for a row whose whole point is to be opened into something else.
    // `{ icon, title, onClick }`. Plans carry one; nothing else does yet.
    action,
    // Suppress the overflow entirely, rather than drawing the disabled one below. For a row that
    // was never going to have a menu — a plan is not a Resource and holds none of the three scopes
    // this menu acts on — where "no actions here, check the other modes" would be a wrong answer
    // rather than a dead end.
    noMenu,
    expandable,
    expanded,
    onToggleExpand,
  }) {
    const [menuOpen, setMenuOpen] = useState(false);

    // Not the rail's Datasets: the server promotes a scratch file onto any Dataset this container
    // mounts writable (`_default_dataset`), so offering only the ones someone added to the project
    // greyed this out while the copy would have worked.
    const writableDatasets = SW.store.get().datasetTargets || [];
    // "Use in this chat" / "Stop using here" is a Conversation-scope act, so it only belongs on a
    // Chat surface — a Build reader has no conversation for the verb to name (#147).
    const inChat = SW.router.get().mode === 'chat';
    const isScratch = resource.source === 'scratch';
    const noWritableDataset = {
      key: 'to-dataset',
      // A disabled item never fires onClick, so the reason has to be the label itself.
      label: SW.brand.text('No writable {dataset} is mounted here'),
      disabled: true,
    };
    const noAppSelected = {
      key: 'to-app-disabled',
      label: 'No app is selected',
      disabled: true,
    };
    // Whether Domino still holds what this row names, and the Built Apps that bind it — read
    // together because between them they decide which removal door the row offers. Liveness is
    // computed in `applyListing` and only there, so this reads it rather than working it out
    // again; a second place subtracting these two sets is the disagreement that function exists to
    // prevent (ADR-0034).
    const missing = SW.util.isMissing(resource);
    const used = resource.usedBy || [];
    // The conversations holding a chip on it, the other half of what the removal refuses on (#169).
    const held = resource.heldBy || [];
    // A missing Resource that an app still binds cannot leave the Project: `remove_project_resource`
    // answers 409, naming that app. The refusal stays — the Binding belongs to the app and removal
    // lives with the list that owns the scope (ADR-0011) — so the row points at the act that would
    // work instead of the one that is certain to be refused. One item per app, because each one has
    // to be visited. A live conversation's chip refuses it just the same (#168), and a Resource
    // only a conversation holds used to compute `stuck === false` — so the row handed over the one
    // door certain to close. Both holders count, and each one keeps its own door.
    const stuck = missing && (used.length > 0 || held.length > 0);
    // The chip on the conversation on screen already has its door above — "Stop using here" — so
    // listing it again would point the reader at the page they are already reading.
    const openable = inChat && inContext
      ? held.filter((c) => c.threadId !== ((SW.store.get().thread || {}).id || ''))
      : held;
    const holders = used.map((u) => u.name).concat(held.map((c) => c.title));
    const oneHolder = used.length ? 'the app' : 'the conversation';
    const missingTitle = missing
      ? `${SW.util.missingTitle()}${stuck
        ? ` ${holders.join(', ')} still ${holders.length > 1 ? 'use' : 'uses'} it,`
          + ` so it cannot leave this project until it leaves`
          + ` ${holders.length > 1 ? 'them' : oneHolder}.`
        : ''}`
      : null;
    // The doors the Project scope offers this row. A stuck row's holders each keep their own, and
    // there may be none left to draw: a chip on the conversation on screen is answered by the item
    // above, and a divider over nothing is the one shape this list must not take.
    const projectDoors = stuck
      ? [
          ...used.map((u) => ({
            key: `unbind-app:${u.appId}`,
            label: `Remove from ${u.name}`,
            danger: true,
          })),
          // Not styled as a removal, because it is not one: the chip comes off in the conversation,
          // where the reader can see the turns that put it there.
          ...openable.map((c) => ({ key: `open-chat:${c.threadId}`, label: `Open ${c.title}` })),
        ]
      : [{ key: 'remove', label: `Remove from ${SW.store.get().scope.name}`, danger: true }];
    const items = contextItem
      ? [{ key: 'remove-from-conversation', label: 'Stop using here' }]
      : [
          ...(inChat
            ? [{
                key: inContext ? 'remove-resource-from-conversation' : 'mention',
                label: inContext ? 'Stop using here' : 'Use in this chat',
              }]
            : []),
          ...(isScratch
            ? inChat
              ? writableDatasets.length
                ? writableDatasets.map((d) => ({
                    key: `to-dataset:${d.id}`,
                    label: `Add to ${d.name}`,
                  }))
                : [noWritableDataset]
              // In Build the per-Dataset list collapses to the one app on screen: the file lands on
              // whichever Dataset `_default_dataset` picks, same as an unpicked upload (#147).
              : !app
              ? [noAppSelected]
              : writableDatasets.length
              ? [{ key: 'to-app', label: `Add to ${app.name}` }]
              : [noWritableDataset]
            : []),
          ...(resource.membershipParent && projectDoors.length
            ? [{ type: 'divider' }, ...projectDoors]
            : []),
          // The Project-scope door onto the scratch bytes themselves (ADR-0023) — shown regardless
          // of mode, like the rest of this list, since it is a Project row rather than a Chat one.
          ...(isScratch
            ? [{ type: 'divider' }, { key: 'delete-scratch', label: 'Delete file', danger: true }]
            : []),
        ];

    const onMenu = ({ key }) => {
      setMenuOpen(false);
      if (key === 'mention') return SW.store.addToContext(resource, { quiet: true });
      if (key === 'remove-from-conversation') return SW.store.removeFromConversation(contextItem);
      if (key === 'remove-resource-from-conversation') {
        return SW.store.removeResourceFromConversation(resource.id);
      }
      if (key === 'remove') return SW.store.removeFromProject(resource);
      if (key.startsWith('unbind-app:')) {
        return SW.store.openAppBindings(key.slice('unbind-app:'.length));
      }
      // The route only, as the Plan page's own "From conversation" link does: the chip's door is
      // "Stop using here" on the row once the conversation is open, and it draws itself there.
      if (key.startsWith('open-chat:')) {
        return SW.router.go(
          SW.conversationRoute({ id: key.slice('open-chat:'.length) }, SW.router.get().mode)
        );
      }
      if (key === 'to-app') return SW.store.addScratchToDataset(resource, '');
      if (key === 'delete-scratch') return SW.store.deleteScratchFile(resource);
      if (key.startsWith('to-dataset:')) {
        return SW.store.addScratchToDataset(resource, key.slice('to-dataset:'.length).replace(/^dataset:/, ''));
      }
      return undefined;
    };

    // `used` is the Built Apps that bind this Resource, each with its Scope — the server's answer,
    // carried on the Project row (#133). It was a kind list before, because nothing filled the
    // field and a count of nothing had to be kept off the rows most likely to show it. Now the
    // field is only ever non-empty for a Resource an app really binds, so the data is its own gate:
    // no kind can be left out of the count by being forgotten in a list nobody revisits. Read above
    // the menu since #161, because it decides which removal the menu offers.
    const secondary = required && app
      ? `Required by ${app.name}`
      // The negative of the line above, and it outranks the Project-wide count below it because
      // `saysAppUse` is only ever true in Build — where the question on screen is what THIS app
      // uses, not how popular the Resource is. In Chat the count keeps the slot (#127).
      : saysAppUse && app
      ? `Not used by ${app.name}`
      : used.length
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
          (resource.live ? ' is-live' : '') +
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
            h('span', { className: 'sw-res-name' }, resource.name),
            // Domino no longer holds it. Marked rather than removed, and marked rather than
            // greyed: the creator picked this row deliberately and an app may still bind it, so a
            // row that vanished overnight would leave nobody anything to act on (ADR-0034). The
            // reason is the tooltip and the act is in the menu two inches right, which is where
            // every other act on this row already lives.
            SW.util.isMissing(resource) &&
              h(
                Tooltip,
                { title: missingTitle },
                h(
                  Tag,
                  { bordered: false, className: 'sw-sens sw-sens-restricted' },
                  SW.util.missingMark()
                )
              )
          ),
          secondary &&
            h(
              Tooltip,
              {
                // The apps behind the count, each with its Scope, because the count alone says how
                // many and never which. Otherwise whatever the row asked to be
                // readable in full: `.sw-res-sub` ellipsises, and a Data Source's Scope is the one
                // subtitle here whose TAIL is the part that identifies it — `DWH.MARTS` and
                // `DWH.MARTS_ARCHIVE` truncate to the same pixels in a narrow rail.
                title: used.length
                  ? used.map((u) => (u.scope ? `${u.name} — ${u.scope}` : u.name)).join(', ')
                  : (resource.subtitleFull || null),
                mouseEnterDelay: 0.4,
              },
              h('span', { className: 'sw-res-sub' }, secondary)
            )
        ),
        resource.sovereign &&
          h(
            Tooltip,
            { title: SW.util.SOVEREIGN_TITLE },
            h(Tag, { bordered: false, className: 'sw-sens sw-sens-internal' }, 'sovereign')
          )
      ),

      // One slot, two states, so nothing on the row moves as context changes. The mark is a mark
      // and not a button: taking something back out is what the chip's own × does, and what the
      // drawer behind this row offers in words. Only the ADD is a click, and only where the verb
      // has a Conversation on screen to name.
      inContext
        ? h(
            Tooltip,
            { title: SW.util.IN_CONTEXT_TITLE },
            h(
              'span',
              { className: 'sw-res-ctx', 'aria-label': `${resource.name} is in this conversation` },
              h(CheckCircleFilled, { style: { fontSize: 12 } })
            )
          )
        : onAddToContext
        ? h(
            Tooltip,
            { title: 'Use in this chat' },
            h(
              'button',
              {
                className: 'sw-res-ctx sw-res-ctx-add',
                'aria-label': `Use ${resource.name} in this chat`,
                onClick: (e) => {
                  e.preventDefault();
                  e.stopPropagation();
                  onAddToContext(resource);
                },
              },
              h(PlusOutlined, { style: { fontSize: 11 } })
            )
          )
        : h('span', { className: 'sw-res-ctx is-spacer' }),

      action &&
        h(
          Tooltip,
          { title: action.title },
          h(
            'button',
            {
              className: 'sw-res-action',
              'aria-label': action.title,
              onClick: (e) => {
                e.preventDefault();
                e.stopPropagation();
                action.onClick();
              },
            },
            h(action.icon, { style: { fontSize: 11 } })
          )
        ),

      noMenu
        ? null
        : contextItem
        ? h(
            'button',
            {
              className: 'sw-res-more',
              // Says "in this chat" where the visible menu says "Stop using here": that control
              // takes its scope from the panel around it, and a screen reader has no "here" to
              // look at. Same act, same verb, the scope spoken rather than shown (ADR-0015).
              'aria-label': `Stop using ${resource.name} in this chat`,
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
            // A disabled button never opens, so a row with nothing to offer reads as a dead end
            // rather than an empty menu a click reveals (#147's mode gate is what made items=[]
            // reachable here). menu.items stays [] rather than the node disappearing, which is
            // what a caller reading the menu off this tree needs to still find.
            items.length === 0
              ? h(
                  Tooltip,
                  { title: 'No actions here — check the other modes and sections' },
                  h(
                    'button',
                    {
                      className: 'sw-res-more',
                      'aria-label': `No actions for ${resource.name}`,
                      disabled: true,
                    },
                    h(MoreOutlined, null)
                  )
                )
              : h(
                  'button',
                  { className: 'sw-res-more', 'aria-label': `Actions for ${resource.name}` },
                  h(MoreOutlined, null)
                )
          )
    );
  };

  SW.ResourcePanel = function ResourcePanel() {
    const {
      resourceGroups, resourceErrors, activeApp, panelFilter, projectPlan, activePlanId, plans,
      apps, bindings, attachments, resourcesLoading,
    } = SW.store.get();
    const [collapsed, setCollapsed] = useState({});
    // Whether the Plans group is showing what has been put away (#167). Panel state rather than
    // stored state, like `collapsed` beside it: an archive is a lasting judgement about a document,
    // and "let me see the ones I hid" is a glance, not a preference to carry between sessions.
    const [showArchived, setShowArchived] = useState(false);
    const fileRef = useRef(null);

    // Opened by clicking a row and by nothing else. The refusal card used to ask for one from
    // outside, because a Data Source Binding carried a Scope and a Scope was a position in here
    // (#129, #135); since #142 the bind carries none and the card records it in one click, so the
    // only reader of that pointer is gone and the cascade is what it was before — a way to look
    // (#143, ADR-0021).
    const [expandedId, setExpandedId] = useState(null);

    // What the selected app is bound to, keyed the way a Project row is (#99) — see
    // `SW.util.bindingId`. This is now the ONLY place the panel says what an app needs: the second
    // list that said it from the other side is the App dependencies modal, which is the app's own
    // surface and therefore where its removals belong (ADR-0021, extending ADR-0011).
    const requiredIds = new Set((bindings || []).map((b) => SW.util.bindingId(b)));
    const attachedIds = new Set((attachments || []).map((a) => a.resourceId));
    const filterGroup = panelFilter && SW.util.RESOURCE_META[panelFilter]
      ? SW.util.RESOURCE_META[panelFilter].group
      : null;

    const rows = (kind) => resourceGroups[kind] || [];

    const inChat = SW.router.get().mode === 'chat';
    const inBuild = SW.router.get().mode === 'build';

    const openResource = (resource) => SW.store.previewResource(resource.id);

    const addToContext = (resource) => SW.store.addToContext(resource, { quiet: true });

    const addMenu = {
      items: [
        { key: 'browse', label: SW.brand.text('Browse {platformName}…') },
        { key: 'upload', label: 'Upload a file' },
      ],
      onClick: ({ key }) => {
        if (key === 'browse') return SW.store.openCatalog();
        if (key === 'upload') return fileRef.current && fileRef.current.click();
      },
    };

    // Which plan the surface in front of you is working from. One rule, two answers, because the
    // two modes stand in different things: Build stands in an app, so it is the document plan.md
    // was copied from (`projectPlan.planId`); Chat stands in a Conversation, so it is that
    // Conversation's plan — the one its own plan bar offers to open. Neither is "the project's
    // plan", because there is no such thing: a plan belongs to an app (ADR-0008), which is why the
    // rows below name theirs.
    const livePlanId = String(
      (inBuild ? (projectPlan && projectPlan.planId) : activePlanId) || ''
    );
    const appName = (id) => {
      const found = (apps || []).find((a) => a.id === id);
      return found ? found.name : '';
    };

    // Newest first, which is the order the server lists them in.
    //
    // An archived plan is held back rather than dropped (#167). Hiding it with nothing saying so
    // fails the empty-state rule: somebody who put a plan away and wants it back has no answer to
    // "where did it go". So the head counts them and offers the way in, and the count is read off
    // the whole list rather than the drawn one.
    const planDocs = plans || [];
    const archivedCount = planDocs.filter((plan) => plan.archived).length;
    const shownPlans = showArchived ? planDocs : planDocs.filter((plan) => !plan.archived);
    const planRows = shownPlans.map((plan) => {
      const status = SW.util.PLAN_STATUS[plan.status];
      const owner = appName(plan.appId);
      // Archived wins. The filter that drops an archived document from an app's plan pin
      // deliberately does not reach `_thread_plan_id`, so an archived plan can still be a
      // Conversation's `planId` and therefore `activePlanId` — archived and live at once. A
      // hidden-but-highlighted row is the worst of both, and the Conversation's own plan card is
      // the surface that goes on showing it.
      const live = !plan.archived && livePlanId && String(plan.id) === livePlanId;
      return {
        id: plan.id,
        name: plan.title || 'Untitled plan',
        kind: 'plan',
        live,
        // Which app, because a plan is app-specific and this list is the Project's (ADR-0008). A
        // plan drafted in Chat has no app yet — the reference is stamped on at the handoff — and
        // saying so is the answer somebody looking for their draft came for.
        subtitle: [
          // First, because it outranks the review state while the row is only on screen at all
          // because somebody asked to see what was put away.
          plan.archived ? 'Archived' : '',
          live && inBuild && projectPlan && projectPlan.status === 'built'
            ? 'Built'
            : (status ? status.label : ''),
          owner || (plan.appId ? '' : 'Not built yet'),
        ].filter(Boolean).join(' · '),
      };
    });

    const groupRows = (group) =>
      group.subgroups.map((sub) => ({ sub, rows: rows(sub.kind) }));

    const total = planDocs.length
      + GROUPS.reduce((acc, g) => acc + g.subgroups.reduce((n, s) => n + rows(s.kind).length, 0), 0);

    const rowFor = (resource) => {
      const expandable = resource.membershipParent
        && (resource.kind === 'dataset' || resource.kind === 'datasource');
      const expanded = expandable && expandedId === resource.id;
      // Whether the SUBTITLE answers "does the selected app use this" (#127, #129). Build only: a
      // Binding names exactly one app and Chat shows none, so the sentence would have no subject.
      // Both kinds this panel can name a Binding for, because absence reads the same to a person
      // either way — the sign is what tells a Resource the app can reach from one merely sitting in
      // the Project, and a Data Source was as silent about that as an Alias ever was.
      const saysAppUse = inBuild && Boolean(activeApp) && Boolean(resource.bindingKey)
        && (resource.kind === 'model_llm' || resource.kind === 'datasource');
      // An Upload has crossed into no app yet — said here, at render time, because which app that
      // is can change without a reload (#147). `resource.subtitle` never sets this for a scratch
      // row, so filling it in only when absent cannot clobber anything real.
      const row = resource.source === 'scratch' && !resource.subtitle
        ? {
            ...resource,
            subtitle: activeApp ? `Chat-only — not in ${activeApp.name}` : 'Chat-only — not in any app yet',
          }
        : resource;
      const inContext = attachedIds.has(resource.id);
      return h(
        Fragment,
        { key: resource.id },
        h(SW.ResourceRow, {
          resource: row,
          required: requiredIds.has(resource.id),
          app: activeApp,
          saysAppUse,
          inContext,
          onAddToContext: inChat && !inContext ? addToContext : null,
          highlighted: Boolean(panelFilter) && SW.util.RESOURCE_META[resource.kind]
            && SW.util.RESOURCE_META[resource.kind].group === filterGroup,
          onOpen: openResource,
          expandable,
          expanded,
          onToggleExpand: () => setExpandedId(expanded ? null : resource.id),
        }),
        expanded &&
          // No app is passed down any more (#142). The tree used to be handed one so it could hang
          // `Use in {app}` beside the crumb; that act is on the Built App's own surface now
          // (ADR-0021), and what is left here is looking at what the Resource contains.
          h(SW.ResourceTree, { resource, query: '', variant: 'rail' })
      );
    };

    const planRowFor = (row) =>
      h(SW.ResourceRow, {
        key: row.id,
        resource: row,
        // Opening the plan is the whole of what this row does, so it is the row's own click AND an
        // icon that says so. A plan has an editor behind it rather than a details drawer, and
        // nothing else in this list opens into one — the icon is what makes that legible before
        // the click rather than after it.
        onOpen: () => SW.store.openPlanArtifact(row.id),
        action: {
          icon: ArrowRightOutlined,
          title: 'Open the plan',
          onClick: () => SW.store.openPlanArtifact(row.id),
        },
        noMenu: true,
      });

    // A group head: the caret that folds it, and the way in beside it. `group` is null for Plans,
    // which is a list of documents rather than of things the catalog holds — there is nothing for
    // an add door to open.
    //
    // The head holds two controls, so the head itself cannot be one: a `+` nested in a
    // `role="button"` div is invalid markup, and its click would bubble and collapse the group it
    // had just opened a catalog for (#164). The caret is a real button, the `+` is another, and the
    // row keeps neither cursor nor hover of its own.
    // `extra` is the slot the `+` would have taken, for a group that has no add door but does have
    // something else to offer there — the Plans group's archived toggle is the only one so far.
    const groupLabel = (key, label, count, group, extra) => {
      const isCollapsed = collapsed[key];
      return h(
        'div',
        { className: 'sw-res-group-label' },
        h(
          'button',
          {
            type: 'button',
            className: 'sw-res-group-toggle',
            'aria-expanded': !isCollapsed,
            onClick: () => setCollapsed({ ...collapsed, [key]: !isCollapsed }),
          },
          h(isCollapsed ? RightOutlined : DownOutlined, { style: { fontSize: 9, color: '#8F8FA3' } }),
          h('span', { className: 'sw-group-label' }, `${label} (${count})`)
        ),
        // The way in does not depend on the group being empty. This door used to live only in the
        // empty branch, so adding the first thing to a group took the door away with it, and a
        // caller wanting a second one had to know about the head's dropdown (#164). It is drawn
        // always, not on hover like `.sw-res-more`: a way in that appears only under the pointer is
        // the same missing affordance in a quieter form.
        group && !group.placeholder &&
          h(
            Tooltip,
            { title: SW.brand.text(`Add ${label.toLowerCase()} from {platformName}`), placement: 'left' },
            h(
              'button',
              {
                type: 'button',
                className: 'sw-res-group-add',
                'aria-label': SW.brand.text(`Add ${label.toLowerCase()} from {platformName}`),
                onClick: () => SW.store.openCatalog(addKind(group)),
              },
              h(PlusOutlined, { style: { fontSize: 11 } })
            )
          ),
        extra || null
      );
    };

    return h(
      'div',
      { className: 'sw-panel' },

      // The panel names itself, once, in the shell's own heading voice rather than the all-caps
      // group label the section heads used to wear — a rail with three shouting subheadings and no
      // title reads as three lists rather than one panel. The dock drew a tab bar here; there is
      // one panel now, so a tab that can only be the tab you are on is a control with nothing to
      // choose.
      //
      // Title and act on two rows. One row held four things — a name, a count, a door and the
      // chevron that hides the panel — which reads as a toolbar rather than a heading, and it was
      // the reason the Add label had to disappear on a narrow rail. A heading is a heading; the act
      // goes under it, with room for its own words at every width.
      h(
        'div',
        { className: 'sw-panel-head' },
        h('h2', { className: 'sw-panel-title' }, 'Project resources'),
        h('span', { className: 'sw-topnav-spacer' }),
        // The one control that hides the panel, and it stays on the title row: it is the dock's
        // chrome rather than one of the panel's own acts, and the sub bar's near-identical twin
        // two rows up is gone.
        h(
          Tooltip,
          { title: `Hide the side panel · ${SW.util.shortcut('⌘/')}` },
          h(
            'button',
            {
              className: 'sw-icon-btn is-dark-text',
              'aria-label': 'Hide the side panel',
              // `toggleDockOpen` records the close and nulls `panelFilter` for us. A raw `set` here
              // meant the dock persisted when you closed it with ⌘/ and forgot when you closed it
              // with its own button (#150).
              onClick: () => SW.store.toggleDockOpen(),
            },
            h(DoubleRightOutlined, null)
          )
        )
      ),

      h(
        'div',
        { className: 'sw-panel-actions' },
        h(
          Dropdown,
          { menu: addMenu, trigger: ['click'], placement: 'bottomLeft' },
          h(
            Button,
            {
              size: 'small',
              type: 'primary',
              className: 'sw-panel-add',
              icon: h(PlusOutlined, { style: { fontSize: 10 } }),
            },
            'Add resources'
          )
        )
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

        resourcesLoading &&
          h('div', { className: 'sw-panel-note' }, 'Loading this project…'),

        // Drawn off the whole list, not the drawn rows: a Project whose only plans are archived
        // still needs the head, because the head is where the way back to them is (#167).
        planDocs.length > 0 &&
          h(
            Fragment,
            null,
            groupLabel(
              'plans',
              'Plans',
              planRows.length,
              null,
              archivedCount > 0 &&
                h(
                  'button',
                  {
                    type: 'button',
                    className: 'sw-res-group-archived',
                    'aria-pressed': showArchived,
                    // The count is on the label rather than in a tooltip: it is the answer to
                    // "where did my plan go", and a number you have to hover to read is no answer.
                    onClick: () => {
                      setShowArchived(!showArchived);
                      // The rows sit under the group's own caret, so revealing them into a folded
                      // group would report a press with nothing on screen to show for it. Only on
                      // the way in: folding on the way out would take the live plans with it.
                      if (!showArchived) setCollapsed({ ...collapsed, plans: false });
                    },
                  },
                  `${showArchived ? 'Hide' : 'Show'} archived (${archivedCount})`
                )
            ),
            !collapsed.plans && planRows.map(planRowFor)
          ),

        GROUPS.map((group) => {
          const items = groupRows(group);
          const count = items.reduce((acc, i) => acc + i.rows.length, 0);
          // Every failed kind in this group, not the first. `data` holds two of them, so a `.find`
          // here reported a Data Sources outage and stayed silent about a simultaneous Datasets one
          // — and the group would look half-checked with nothing saying which half. Each sentence
          // names its own kind, which is what keeps a group-level line legible over rows from a kind
          // that answered (#161).
          const listingError = (ERROR_KEYS[group.key] || [])
            .map((k) => (resourceErrors || {})[k])
            .filter(Boolean)
            .join(' ') || null;
          // Empty and known is nothing to draw. Empty and UNKNOWN still is — see the note on
          // GROUPS above. This is also what puts #161's group-level sentence on screen in the case
          // it was written for: a kind that errored and has no rows left to hang it over.
          if (count === 0 && !listingError) return null;
          const isCollapsed = collapsed[group.key];
          const named = items.filter((i) => i.rows.length).length > 1;

          return h(
            Fragment,
            { key: group.key },
            groupLabel(group.key, group.label, count, group),
            // A kind that would not list says so here, above its rows, because the fact is the
            // KIND's rather than any row's — stamping it on twelve rows says it twelve times. It
            // used to render only in the empty state, which is the one place it could never appear
            // for the case that needs it: a kind that errored AND still has rows, carried forward
            // by `keepUnreadKinds`. Those rows are the last good answer and none of them is marked
            // missing, so without this line nothing on screen says the fresh read failed (ADR-0034).
            //
            // Only a kind that ERRORED gets a sentence. A kind that is merely uncheckable — Model
            // APIs, whose fan-out silently drops projects — stays quiet, because Preflight's rule
            // is that "we could not check" stays a state unless the dependency itself is the fault.
            !isCollapsed && listingError &&
              h('div', { className: 'sw-group-note' }, listingError),
            !isCollapsed &&
              items.map(({ sub, rows: subRows }) =>
                subRows.length
                  ? h(
                      Fragment,
                      { key: sub.kind },
                      sub.label && named &&
                        h(
                          'div',
                          { className: 'sw-res-subgroup' },
                          h('span', { className: 'sw-group-label' }, SW.brand.text(sub.label))
                        ),
                      subRows.map(rowFor)
                    )
                  : null
              )
          );
        }),

        // Nothing at all, and nothing on its way. Not shown over a listing failure: a Project whose
        // Data Sources could not be listed has not been shown to be empty, and offering Add as the
        // way out of a gateway outage sends somebody to a catalogue that will fail the same way.
        total === 0 && !resourcesLoading && !Object.keys(resourceErrors || {}).length &&
          h(
            'div',
            { className: 'sw-panel-empty' },
            h('span', { className: 'sw-panel-empty-icon' }, h(InboxOutlined, null)),
            h('p', { className: 'sw-panel-empty-title' }, 'Nothing here yet'),
            h(
              'p',
              { className: 'sw-panel-empty-text' },
              SW.brand.text(
                'Bring in the {dataSourcePlural}, models and files this project builds with.'
              )
            ),
            h(
              Dropdown,
              { menu: addMenu, trigger: ['click'], placement: 'bottom' },
              h(
                Button,
                {
                  size: 'small',
                  className: 'sw-btn-secondary',
                  icon: h(PlusOutlined, { style: { fontSize: 10 } }),
                },
                'Add resources'
              )
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
