window.SW = window.SW || {};

(function () {
  const { createElement: h } = React;
  const { Drawer, Select, Alert, Button, Space, Spin } = antd;

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
    },
    {
      slot: 'implement',
      label: 'Implement',
    },
    {
      slot: 'ask',
      label: 'Ask and Chat',
    },
  ];

  const DEFAULT_KEY = '__default__';

  SW.ModelAssignmentsDrawer = function ModelAssignmentsDrawer() {
    const {
      assignmentsOpen, assignments, assignmentsLoading, assignmentsError, buildRunning, catalog,
      sensitivity,
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

    // `row.default` is falsy only in the fallback rows above, and those are built only when
    // `assignments` is null — which is the same object `aliases` comes from, so the mapped list is
    // empty in exactly that case. The single option below therefore cannot collide with one of
    // them. Written down because the two facts sit four lines apart and read as independent: the
    // guard against a duplicate option value is that coupling, not a check.
    // The sensitivity lock (ADR-0043). A separate flag from `readOnly` and a separate Alert from
    // `notice`, for the reason those are separate from each other: this closes SOME rows and leaves
    // the rest working, so folding it into either would put one explanation over two situations.
    const locked = SW.util.isLocked(sensitivity);
    // Every model closed and nothing approved — the group is missing, empty, unreachable, or holds
    // nothing this caller may call. The server has already written the sentence that names which,
    // and it is the same sentence a turn would be refused with; a second wording here would be a
    // second account of one state.
    const lockDead = locked && !(sensitivity.approved || []).length;

    const options = (row) => [
      // The way BACK, so it carries no model id of its own: picking it clears the assignment rather
      // than setting one, which is the difference between "the default" and "this model, which
      // happens to be the default today". Absent when the panel read never landed, because then the
      // default is exactly what is not known.
      ...(row.default
        ? [{ value: DEFAULT_KEY, label: `Use the default (${row.default})` }]
        : [{ value: row.model, label: row.model }]),
      ...aliases.map((a) => {
        // The lock outranks "not serving" in BOTH the disable and the reason, mirroring the router,
        // where it outranks every other rule (`llm_router._lock_sensitivity`). A model that is both
        // unapproved and stopped is unapproved first: starting the endpoint would not make it
        // pickable, so an explanation that sent somebody to do that would waste their afternoon.
        const barred = locked && !SW.util.isApproved(sensitivity, a.name);
        return {
          value: a.name,
          // Offered but not selectable. `/v1/models` filters on permission alone, so a granted Alias
          // whose Hosted GenAI Endpoint is stopped is listed anyway (#21) — hiding it would answer
          // "where did that model go" with nothing, and allowing it would fail opaquely mid-build.
          // The lock keeps that shape on purpose: a model hidden from the list teaches nobody why
          // it went, and this one has a reason worth reading.
          disabled: barred || !a.serving,
          title: barred
            ? (sensitivity.refusal || SW.util.lockReason(sensitivity, a.name))
            : (a.problem || undefined),
          label: barred
            ? `${a.name} — not allowed`
            : a.serving
            ? (a.display_name && a.display_name !== a.name ? `${a.name} — ${a.display_name}` : a.name)
            : `${a.name} — not serving`,
        };
      }),
    ];

    const row = (spec) => {
      const current = rows.find((r) => r.slot === spec.slot);
      if (!current) return null;
      const assigned = Boolean(current.assigned);
      // What the lock moves this slot to, when the assignment it would move IS barred. Drawn as the
      // row's value rather than left showing the barred one, because a select whose closed state
      // reads "gpt-5.4 — not allowed" has answered the wrong question: the row is there to say what
      // this mode runs, and under a lock that is never the barred model (ADR-0043).
      //
      // The server's answer, per slot, not a rule re-derived here. `llm_router.nearest_approved`
      // reads the sovereign slots and the administrator's group ordering, and a second copy of that
      // in JavaScript would be a confident label that is wrong exactly where it matters — the same
      // reasoning that keeps `lockedRunsOn` reading `sensitivity.model` for the composer chip.
      const barredNow = locked && !SW.util.isApproved(sensitivity, current.model);
      const runs = (barredNow && ((sensitivity.slot_models || {})[spec.slot])) || '';
      return h(
        'div',
        { key: spec.slot, className: 'sw-assignment-row' },
        h('label', { className: 'sw-assignment-label', htmlFor: `assign-${spec.slot}` }, spec.label),
        h(Select, {
          id: `assign-${spec.slot}`,
          'aria-label': `${spec.label} model`,
          style: { width: '100%' },
          disabled: readOnly,
          value: runs || (assigned || !current.default ? current.model : DEFAULT_KEY),
          options: options(current),
          onChange: (value) =>
            SW.store.setAssignment(spec.slot, value === DEFAULT_KEY ? null : value),
        }),
        // Preflight's verdict on the slot as it stands — the half of the save-time re-check that a
        // greyed menu row cannot carry, because that one says the model is bad and this says the
        // slot is.
        //
        // Dropped on a row the lock has already moved, but only when it is the signing pin's
        // sentence: the lock outranks the pin (`llm_router._lock_sensitivity` wraps `_pin_signing`),
        // so under it the pin is not what decides this row, and "this model won't run" names both
        // the wrong cause and the wrong remedy. The other two verdicts stay — a model that will not
        // answer will not answer whatever moved the turn — and the lock's own line, one below, still
        // says what runs. Which verdict it is comes from the server and not from reading the
        // sentence, because a sentence is what a brand pack is allowed to change (#276).
        //
        // `barredNow` and NOT `runs`: `runs` is the narrower fact that the lock moved this row AND
        // the panel was told where to. `_locked_slot_models` returns nothing at all when the
        // approved set resolves to none — the lock that closes every model — and reading `runs`
        // there would let the pin's sentence back in at exactly the moment the reader can act on it
        // least.
        current.problem && !(current.shadowed && barredNow)
          ? h('div', { className: 'sw-assignment-problem' }, current.problem)
          : null,
        // What the row would say if the lock were not holding. Not optional once the value above is
        // a substitution: without it the panel simply shows a model nobody chose, and the person who
        // set this slot to `gpt-5.4` last week would read the row as having lost their assignment.
        // It replaces the default line rather than sitting beside it — while the lock holds, what
        // the slot reverts to is not the question anybody is asking of this row.
        //
        // Says neither "assigned" nor "the default", because `current.model` is whichever of the two
        // this slot holds and one sentence has to be true of both.
        runs
          ? h('div', { className: 'sw-assignment-detail' },
              `${current.model} isn't approved, so this runs ${runs}.`)
          // Only when it differs from the default: repeating "gpt-5.4 (default)" under a select that
          // already says exactly that is noise on every row nobody has touched.
          : assigned && current.default
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

        // The lock's own explanation, above the rows it closes (ADR-0043). It is drawn alongside
        // `notice` rather than as one of its cases because it does not close the panel — the
        // approved models are still assignable from here, which is the whole point of leaving the
        // rest visible and disabled instead of hiding them.
        //
        // One paragraph, and the way out where there is one. The scope sentence — what happens to a
        // Data Source bound alongside — used to ride here and no longer does: it is a lesson about
        // a different object, read by somebody who came to change a model, and the limit of the
        // promise is still said where the promise is made (`resources/pinned_model._egress_note`,
        // and the app's own AGENTS.md).
        locked
          ? h(Alert, {
              type: lockDead ? 'warning' : 'info',
              showIcon: true,
              message: 'Allowed models only',
              description: h(
                'div',
                null,
                h('p', { style: { margin: 0 } },
                  lockDead
                    // The server's own sentence, which names WHICH of the four ways the approved set
                    // came back empty and who to ask about it. The fallback exists only so a
                    // response that somehow carried none still says something true.
                    ? (sensitivity.refusal || SW.brand.text(
                        'No models are approved for sensitive data yet. Ask a {platformName} '
                        + 'administrator to approve one.'
                      ))
                    // Which of the two locks is holding changes the subject of the sentence, not
                    // just its wording: after an unbind it is the conversation that read the rows
                    // and the app that reads nothing (ADR-0043).
                    // Publish is named in the Bindings sentence and NOT in this one, deliberately.
                    // The publish guard's sensitive-model check reads the CURRENT Bindings, so with
                    // no declared Dataset attached there is no publish-time gate left to promise —
                    // and ADR-0043 records that a session lock does not reach past what Sage sends.
                    // Saying it anyway would be the one thing this whole amendment is against: a
                    // sentence about the lock that is false at the moment somebody relies on it.
                    : SW.util.lockedBySession(sensitivity)
                      ? SW.brand.text(
                          'This chat already used {datasets}, so only approved models are allowed.',
                          { datasets: SW.util.declaredPhrase(sensitivity) }
                        )
                      : SW.brand.text(
                          'Only approved models can be used with {datasets}.',
                          { datasets: SW.util.declaredPhrase(sensitivity) }
                        )),
                // NOT under `lockDead`: that branch renders `sensitivity.refusal`, the server's own
                // sentence, and `declared_turn_refusal` already appends this one to it whenever no
                // Dataset is declared — which is exactly the sticky case. Drawn again here it is
                // the same sentence twice in two paragraphs.
                !lockDead && SW.util.sessionWayOut(sensitivity)
                  ? h('p', { style: { margin: '8px 0 0' } }, SW.util.sessionWayOut(sensitivity))
                  : null
              ),
            })
          : null,

        assignmentsLoading && !rows.length
          ? h(Spin, null)
          : h('div', null, SLOTS.map(row))
      )
    );
  };
})();
