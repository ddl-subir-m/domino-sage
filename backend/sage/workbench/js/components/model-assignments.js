window.SW = window.SW || {};

(function () {
  const { createElement: h } = React;
  const { Drawer, Select, Alert, Button, Space, Spin, Typography } = antd;

  // The three slots a person can assign, in the order the panel lists them, with what each one
  // actually drives. Auto is absent on purpose: it has no assignment of its own, it runs Plan's
  // while it plans and Implement's while it builds, so a fourth row would be a control that changes
  // nothing (ADR-0017).
  //
  // `ask` is one row for two consumers, and the label says so. `_resolve_chat` returns `catalog.ask`
  // as CHAT_DEFAULT, so setting this has always repointed Chat as well — silently, until now.
  const SLOTS = [
    {
      slot: 'plan',
      label: 'Plan',
      detail: 'Auto plans on this, and so does Plan mode.',
    },
    {
      slot: 'implement',
      label: 'Implement',
      detail: 'Auto builds on this, and so does Implement mode.',
    },
    {
      slot: 'ask',
      label: 'Ask and Chat',
      detail: "Ask mode, and Chat whenever you haven't picked your own model.",
    },
  ];

  const DEFAULT_KEY = '__default__';

  SW.ModelAssignmentsDrawer = function ModelAssignmentsDrawer() {
    const {
      assignmentsOpen, assignments, assignmentsLoading, assignmentsError, buildRunning, catalog,
    } = SW.store.get();

    const close = () => SW.store.openAssignments(false);
    // The panel read, falling back to the catalog the status poll already keeps current when that
    // read never landed at all. Showing what each mode runs is the whole difference between a panel
    // that is closed and one that is empty, and a fetch that threw has no slots in it to show.
    const rows = (assignments && assignments.slots)
      || (catalog
        ? SLOTS.map((s) => ({ slot: s.slot, model: catalog[s.slot], default: null, problem: null }))
        : []);
    const aliases = SW.util.chatCapable((assignments && assignments.aliases) || []);
    const listable = aliases.length > 0;
    const readOnly = buildRunning || !listable;

    // Which sentence, if any — kept apart from `readOnly` on purpose. Three different things close
    // these rows and a fourth leaves them open, and collapsing them into the one flag is how a
    // person gets a disabled control with the wrong explanation over it.
    const notice = buildRunning ? 'running'
      : !listable && assignmentsError ? 'unlistable'
      : !listable && !assignmentsLoading && (assignments || catalog) ? 'empty'
      // The Alias list arrived; something else did not. `_endpoint_listing` failing means only that
      // reachability went unchecked, so the rows stay editable and the sentence says which half is
      // missing rather than claiming the models are gone.
      : assignmentsError ? 'unchecked'
      : null;

    const NOTICE = {
      running: {
        type: 'info',
        message: 'A build is running',
        description: 'Changing a model now would move the rest of that build onto it. Wait for the '
          + 'turn to finish.',
        retry: false,
      },
      unlistable: {
        type: 'warning',
        message: "Can't list the models right now",
        description: assignmentsError,
        retry: true,
      },
      empty: {
        type: 'warning',
        message: 'No models available to you',
        description: 'The LLM Gateway offers no chat model this account can use, so these cannot be '
          + 'changed. Ask whoever administers the LLM Gateway for access to one.',
        retry: true,
      },
      unchecked: {
        type: 'warning',
        message: "Couldn't check every model",
        description: `${assignmentsError} The models below can still be chosen, but Sage could not `
          + 'confirm they are all serving.',
        retry: true,
      },
    };

    const options = (row) => [
      // The way BACK, so it carries no model id of its own: picking it clears the assignment rather
      // than setting one, which is the difference between "the default" and "this model, which
      // happens to be the default today". Absent when the panel read never landed, because then the
      // default is exactly what is not known.
      ...(row.default
        ? [{ value: DEFAULT_KEY, label: `Use the default (${row.default})` }]
        : [{ value: row.model, label: row.model }]),
      ...aliases.map((a) => ({
        value: a.name,
        // Offered but not selectable. `/v1/models` filters on permission alone, so a granted Alias
        // whose Hosted GenAI Endpoint is stopped is listed anyway (#21) — hiding it would answer
        // "where did that model go" with nothing, and allowing it would fail opaquely mid-build.
        disabled: !a.serving,
        title: a.problem || undefined,
        label: a.serving
          ? (a.display_name && a.display_name !== a.name ? `${a.name} — ${a.display_name}` : a.name)
          : `${a.name} — not serving`,
      })),
    ];

    const row = (spec) => {
      const current = rows.find((r) => r.slot === spec.slot);
      if (!current) return null;
      const assigned = Boolean(current.assigned);
      return h(
        'div',
        { key: spec.slot, className: 'sw-assignment-row' },
        h('label', { className: 'sw-assignment-label', htmlFor: `assign-${spec.slot}` }, spec.label),
        h('div', { className: 'sw-assignment-detail' }, spec.detail),
        h(Select, {
          id: `assign-${spec.slot}`,
          'aria-label': `${spec.label} model`,
          style: { width: '100%' },
          disabled: readOnly,
          value: assigned || !current.default ? current.model : DEFAULT_KEY,
          options: options(current),
          onChange: (value) =>
            SW.store.setAssignment(spec.slot, value === DEFAULT_KEY ? null : value),
        }),
        // Preflight's verdict on the slot as it stands — the half of the save-time re-check that a
        // greyed menu row cannot carry, because that one says the model is bad and this says the
        // slot is.
        current.problem
          ? h('div', { className: 'sw-assignment-problem' }, current.problem)
          : null,
        // Only when it differs from the default: repeating "gpt-5.4 (default)" under a select that
        // already says exactly that is noise on every row nobody has touched.
        assigned && current.default
          ? h('div', { className: 'sw-assignment-detail' }, `Default is ${current.default}.`)
          : null
      );
    };

    return h(
      Drawer,
      {
        title: 'Model assignments',
        placement: 'right',
        width: 420,
        open: assignmentsOpen,
        onClose: close,
      },
      h(
        Space,
        { direction: 'vertical', size: 16, style: { width: '100%' } },

        h(
          Typography.Paragraph,
          { className: 'sw-secondary', style: { marginBottom: 0 } },
          'These apply to this Project and everyone in it. Teammates get them the next time the ',
          'Project syncs.'
        ),

        notice
          ? h(Alert, {
              type: NOTICE[notice].type,
              showIcon: true,
              message: NOTICE[notice].message,
              description: NOTICE[notice].description,
              action: NOTICE[notice].retry
                ? h(Button, { size: 'small', onClick: () => SW.store.loadAssignments() }, 'Retry')
                : undefined,
            })
          : null,

        assignmentsLoading && !rows.length
          ? h(Spin, null)
          : h('div', null, SLOTS.map(row))
      )
    );
  };
})();
