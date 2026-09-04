window.SW = window.SW || {};

(function () {
  const { createElement: h, useState, useRef, useEffect } = React;
  const { Input, Button, Dropdown, Tag, Tooltip, Space } = antd;
  const { PlusOutlined, ArrowUpOutlined, DownOutlined, CloseOutlined } = icons;

  function BUILD_MODES() {
    return [
      { id: 'auto', label: 'Auto', key: '1' },
      { id: 'ask', label: 'Ask', key: '2' },
      { id: 'plan', label: 'Plan', key: '3' },
      { id: 'implement', label: 'Implement', key: '4' },
    ];
  }
  const BUILD_MODE_LABEL = { auto: 'Auto', ask: 'Ask · read-only', plan: 'Plan', implement: 'Implement' };
  const PROJECT_MENTION_KINDS = SW.util.MEMBERSHIP_PARENT_KINDS;

  function chatAliases(resourceGroups) {
    return SW.util.chatCapable(resourceGroups.model_llm);
  }

  function effortLabel(value) {
    if (!value) return 'Default';
    return value.charAt(0).toUpperCase() + value.slice(1);
  }

  // Session context, then this Thread's artifacts, then project Resources, then the Project's
  // Uploads, then the selected app's Attachments, then the catalogue this project has not joined
  // yet. The working-set-before-catalogue half of that order is `SW.util.workingSetFirst`, shared
  // with the Build header's picker so the two menus cannot drift (ADR-0021); the groups above it
  // are this menu's own, because only a Conversation has Session context and artifacts.
  //
  // The app's Attachments are a group of their own because the Project stopped listing them
  // (#148). Without it the menu would go on offering every file it ever offered EXCEPT the app's
  // own data — the one kind a Build prompt names most.
  function mentionCandidates(attachments, resourceGroups, query, artifacts, catalogueParents,
                             appAttachments, collapse) {
    const context = (attachments || []).map((att) => ({
      id: att.resourceId || att.id,
      name: att.resourceName,
      kind: att.resourceKind || 'file',
      path: att.path,
      bindingKey: att.bindingKey,
    }));

    const produced = (artifacts || []).map((a) => {
      const path = a.path || '';
      return {
        id: a.id || (path ? `artifact:${path}` : ''),
        name: a.name || path.split('/').pop(),
        kind: 'artifact',
        path,
      };
    });

    const project = PROJECT_MENTION_KINDS.flatMap((kind) => resourceGroups[kind] || []);
    const files = (resourceGroups.file || []).filter(
      (r) => !SW.util.isHiddenFromExplorer(r.path || r.name)
    );
    // Off the app's own record, through the one derivation the turn reads them back with — filter
    // included — so a row this menu offers is a row `collectTurnRefs` can resolve to a path, and
    // nothing hidden from `files` two lines up is let in by the other door.
    const attached = SW.util.attachmentRows(appAttachments);

    // Parents only — the store filters to `MEMBERSHIP_PARENT_KINDS`, off a listing that has no
    // leaf in it to begin with. That is what keeps a warehouse table out of this menu, which must
    // never fetch a warehouse catalog (docs/workbench/chat.md).
    // `collapse` is Build's. Above the threshold the app's Attachments come back as folder rows
    // (ADR-0030), and a folder mention is honoured by `_resolve_mentions` against the app's own
    // manifest — a Build turn's channel. Chat resolves its tokens against the Conversation's chips,
    // where a folder is not a chip, so it is offered a folder nowhere it could not carry one.
    // Chat gains no folder act (ADR-0029), and this is the same line drawn in the menu.
    return SW.util.workingSetFirst({
      groups: [context, produced, resourceGroups.pin || [], project, files, attached],
      catalogue: catalogueParents,
      query,
      // The same number as `FOLDER_COLLAPSE_THRESHOLD` in `sage/orchestrator/service.py`, and the
      // same number for the same reason: at or below it nothing collapses, so the menu has to be
      // able to show the whole list or it goes back to reading as complete when it is not. Above
      // it the collapse holds the app's Attachments to at most that many folder rows, so the
      // collapsed list fits too. Held together by
      // `test_the_menu_shows_as_many_rows_as_the_collapse_lets_through`.
      limit: 10,
      collapse,
    });
  }

  // Where the caret sits inside an unfinished @mention, if it does.
  function mentionAt(value, caret) {
    const upto = value.slice(0, caret);
    const at = upto.lastIndexOf('@');
    if (at === -1) return null;
    if (at > 0 && /[\w@]/.test(upto[at - 1])) return null;
    const token = upto.slice(at + 1);
    if (/\s/.test(token)) return null;
    return { start: at, query: token };
  }

  // The gap between what the prompt names and what the selected Built App holds, said BEFORE the
  // send (#136). The dead end it removes is a whole turn long: the mention is dropped, the answer
  // is prose directions to a panel, and the prompt has to be retyped. The same rows and the same
  // acts as the refusal that would follow (#135), so a person who reads this and a person who
  // sends anyway are told the same thing by the same words.
  //
  // It never blocks. Send is live behind it, because a mention is often incidental and the rest of
  // the prompt still runs — and nothing on it binds on its own, because a Binding is a human pick
  // (ADR-0010). The warning offers the act; the person takes it.
  //
  // The sentence is built from the rows that HAVE an act, not from every row, so it can never name
  // something no button below it can close — the invariant the refusal keeps by building both
  // halves in one pass.
  function MentionGuard({ entries, activeAppId }) {
    const [busy, run] = SW.util.useBusyAct();
    const fixes = SW.store.mentionFixes(entries, activeAppId);
    if (!fixes.length) return null;
    const offered = new Set(fixes.map((fix) => fix.key));
    // The token the picker INSERTED, not the row's name. `mentionToken` collapses whitespace, so a
    // Resource called "Sales Warehouse" stands in the box as `@Sales_Warehouse` — and a warning that
    // quoted the name would send the reader looking for a word their prompt does not contain.
    const named = entries
      .filter((e) => offered.has(`${e.kind}:${e.id}`))
      .map((e) => SW.util.mentionToken({ name: e.name, path: e.kind === 'file' ? e.id : '' }));
    return h(
      'div',
      { className: 'sw-mention-guard' },
      // Named, because a Project holds many Built Apps (ADR-0008). Future tense and the
      // consequence rather than a rule: what is worth knowing here is that sending now costs the
      // mention, and every row carries the same app.
      h('div', { className: 'sw-mention-guard-text' },
        `Send now and ${named.join(', ')} won't reach ${entries[0].app}.`),
      h(Space, { size: 8, wrap: true }, fixes.map((fix, i) =>
        h(Button, {
          key: fix.key,
          // One filled button, whichever gap came first. Three side by side would be three
          // primary actions in one place, which is no hierarchy at all.
          type: i === 0 ? 'primary' : 'default',
          size: 'small',
          loading: busy === fix.key,
          disabled: !!busy,
          onClick: run(fix.key, fix.act),
        }, fix.label)))
    );
  }

  SW.Composer = function Composer({
    placeholder,
    onSend,
    showMode,
    autoFocus,
    disabled,
    compact,
  }) {
    const {
      model, reasoningEffort, attachments, scope, resourceIndex, resourceGroups,
      buildMode, buildTurnMode, buildRunning, catalogAsk, gatewayAliases, thread,
      catalog, buildModel, buildPhase, openWeightModels, signingSlot,
      apps, activeApp, composerSeed, queuedTurns, catalogueParents, appAttachments,
    } = SW.store.get();
    const [text, setText] = useState('');
    const [dragOver, setDragOver] = useState(false);
    const [mention, setMention] = useState(null);
    const [cursor, setCursor] = useState(0);
    const [sendHint, setSendHint] = useState(false);
    const [attachHint, setAttachHint] = useState(false);
    const [attachOpen, setAttachOpen] = useState(false);
    const [modeOpen, setModeOpen] = useState(false);
    // The first chip's note (#137), read once per mount. prefs.js treats storage that cannot be
    // read as "not dismissed", so a browser that blocks storage still renders the page.
    const [chipHintDismissed, setChipHintDismissed] = useState(
      () => SW.prefs.get('chipScopeHintDismissed')
    );
    const fileRef = useRef(null);

    const aliases = chatAliases({
      model_llm: (gatewayAliases && gatewayAliases.length) ? gatewayAliases : resourceGroups.model_llm,
    });
    const askAlias = catalogAsk || '';
    const effectiveModel = (model && model !== 'auto') ? model : askAlias;
    const activeAlias = aliases.find((a) => a.alias === effectiveModel);
    const modelLabel = activeAlias ? (activeAlias.name || activeAlias.alias) : (effectiveModel || 'Ask');
    const efforts = (activeAlias && activeAlias.reasoning_efforts) || [];

    const attachedIds = new Set(attachments.map((a) => a.resourceId));
    // The list uniqueness is computed against, for the token this menu INSERTS and for the folder its
    // rows show (ADR-0030). One list for both, so the folder a person reads on a row and the token
    // that lands in the box cannot disagree about which of two `data.csv` the click meant. The
    // folder rows are in it as well as the files: a folder row is given a token too, and two
    // partitions both called `2026` would otherwise both be offered as `@2026`.
    const mentionPeers = SW.util.attachmentPeers(appAttachments);
    const suggestions = mention
      ? mentionCandidates(attachments, resourceGroups, mention.query, thread && thread.artifacts,
                          catalogueParents, appAttachments, showMode)
      : [];
    const catalogueIds = new Set((catalogueParents || []).map((r) => r.id));
    const buildModes = BUILD_MODES();
    const activeBuildMode = buildModes.find((m) => m.id === buildMode) || buildModes[0];
    const modeQueued = showMode && buildRunning && buildTurnMode && buildTurnMode !== buildMode;

    // A prompt written somewhere else and left here to read, edit or drop — the panel's cleanup
    // offer after an app-scoped removal is the one that writes it (ADR-0011). Taken as a DRAFT and
    // never sent: `onSend` is reached from the send control and from nowhere else.
    useEffect(() => {
      if (!composerSeed) return;
      setText(composerSeed);
      SW.store.clearComposerSeed();
    }, [composerSeed]);

    useEffect(() => {
      if (!modeOpen || !showMode) return undefined;
      const onKey = (e) => {
        if (e.key < '1' || e.key > '4') return;
        e.preventDefault();
        SW.store.setBuildMode(BUILD_MODES()[+e.key - 1].id);
        setModeOpen(false);
      };
      document.addEventListener('keydown', onKey);
      return () => document.removeEventListener('keydown', onKey);
    }, [modeOpen, showMode]);

    // Build only. Chat has no Binding requirement — a Session context chip is all it needs — so
    // the guard is hung off the same flag that tells the two composers apart everywhere else.
    //
    // Read on every render rather than held in state: the rows are a function of the text and of
    // the app's two lists, and both lists are written by the very acts the buttons call. So the
    // warning clears the moment the Binding or the Attachment lands, with nothing to invalidate.
    const unusable = showMode ? SW.store.unusableMentions(text) : [];

    const send = () => {
      const value = text.trim();
      if (!value || disabled) return;
      setText('');
      setMention(null);
      // Sending disables the button under the pointer, so nothing ever fires
      // the mouseleave that would dismiss its hint.
      setSendHint(false);
      onSend(value);
    };

    const changeText = (value, caret, inputType) => {
      setText(value);
      // Backspacing over a finished mention re-opens the picker on every keystroke: the caret lands
      // just after "@BigQuery_Dem", which still matches the token. The user is deleting and gets a
      // menu — and the open menu then takes the next Enter for row selection instead of send. A
      // deletion may still NARROW a menu that is already open, so the guard is conditional on state.
      if (String(inputType || '').startsWith('delete') && !mention) return;
      const found = mentionAt(value, caret === undefined ? value.length : caret);
      setMention(found);
      setCursor(0);
    };

    // Keep @name in the box (and the sent prompt) and add the chip. Context
    // without the token meant OpenCode saw "what's in this" with no file name.
    // Every one of these updates the UI first — the token typed, the chip drawn, the picker
    // closed — so a rejected call leaves the screen claiming something that never happened.
    const sayFailed = (err) => antd.message.error(String((err && err.message) || err));

    // State first, pref second: hiding must not wait on a write the browser may refuse. The
    // state takes the note off screen now; the pref keeps it away on every later visit.
    const dismissChipHint = () => {
      setChipHintDismissed(true);
      SW.prefs.set('chipScopeHintDismissed', true);
    };

    const pickMention = async (resource) => {
      if (!mention) return;
      // Prefer the file's basename so "@data.csv" matches the path OpenCode reads, and fall back to
      // the shortest distinguishing suffix when the app holds two files of that name (ADR-0030).
      // Derived by the util the TURN reads these tokens back with, so the two cannot drift apart.
      const token = SW.util.mentionToken(resource, mentionPeers);
      const after = text.slice(mention.start).replace(/^@\S*/, '');
      const pad = after === '' || /^\s/.test(after) ? '' : ' ';
      setText(text.slice(0, mention.start) + token + pad + after);
      setMention(null);
      // A folder row is not a Resource and has no chip to become: it is offered only because every
      // file under it is already attached to this app, which is the very thing a chip would say
      // (ADR-0030). Adding one would post a `folder:` id no Resource answers to.
      if (resource.kind === 'folder') return;
      // The @name is already in the box. Unreported, this sends a prompt mentioning a file that
      // was never attached.
      await SW.store.addToContext(resource, { quiet: true }).catch(sayFailed);
    };

    const pickFile = () => fileRef.current && fileRef.current.click();

    const handleFiles = async (files) => {
      for (const file of Array.from(files || [])) {
        // Per file, so one rejection does not abandon the rest of the drop. Both callers invoke
        // this as a floating promise, so without the catch a failed upload was an unhandled
        // rejection and the composer simply looked like it had ignored the file.
        try {
          await SW.store.uploadFile(file);
        } catch (err) {
          antd.message.error(`${file.name}: ${String((err && err.message) || err)}`);
        }
      }
    };

    // Reset app is last and set apart on purpose: it is the one item here that throws work away, and
    // it should not sit where a hand reaching for Upload can find it (#36). Builder only — Chat has
    // no app code to put back. Disabled mid-build says why, because a reset would pull files out
    // from under the running turn (the server refuses it with the same sentence).
    // It confirms, and says in the same breath what it does NOT take — the fear when you click this
    // is losing the attachments and the conversation, and both survive.
    // It NAMES the app (#75). A Project holds many Built Apps and this takes one of them, so "the
    // app" and "the code you have built" both read as all of it — the copy would describe the reset
    // this stopped being. The other apps are only mentioned when there are some: a Project with one
    // app gains nothing from being told the apps it does not have are safe.
    // The name is QUOTED because it is usually a sentence: a display name starts as the title of the
    // plan the app was built from, and those end in a full stop, which unquoted lands one in the
    // middle of this question.
    const confirmReset = () => {
      antd.Modal.confirm({
        title: activeApp ? `Reset “${activeApp.name}” to the starter template?`
          : 'Reset this app to the starter template?',
        content: 'The code built in this app is removed and can’t be recovered. Your attached files, '
          + `Resources, and this conversation stay${apps.length > 1 ? ', as do your other apps' : ''}.`,
        okText: 'Reset app',
        okButtonProps: { danger: true },
        cancelText: 'Cancel',
        onOk: () =>
          SW.store.resetApp().catch((err) => {
            antd.message.error(String(err.message || err));
            throw err;
          }),
      });
    };

    const attachMenu = {
      items: [
        { key: 'upload', label: 'Upload a file' },
        { key: 'browse', label: SW.brand.text('Browse {platformName}…') },
        ...(showMode
          ? [
              { type: 'divider' },
              {
                key: 'reset',
                label: buildRunning ? 'Reset app — wait for this build to finish' : 'Reset app',
                danger: true,
                disabled: buildRunning,
              },
            ]
          : []),
      ],
      onClick: ({ key }) => {
        if (key === 'upload') pickFile();
        if (key === 'browse') SW.store.openCatalog();
        if (key === 'reset') confirmReset();
      },
    };

    // Build's model picker. Which slot the current mode is pinned to is the whole question the
    // menu answers, and the router (llm_router) is the only authority on it: Ask is pinned to
    // `ask`, Auto follows the phase, and Plan and Implement take their own slot — and are the only
    // two that will honour an override at all.
    //
    // `signingSlot` outranks all of that and is NOT recomputed here. A model that signs its tool
    // calls cannot share a session with one that does not, so one assignment takes every mode and
    // every phase (ADR-0032). This copy of the precedence could not see that rule, and every line
    // below reads `pinnedModel` — so the label, the `(default)` marker and the override comparison
    // were all naming a model the turn would not run on.
    const pinnedSlot = signingSlot || (activeBuildMode.id === 'ask'
      ? 'ask'
      : activeBuildMode.id === 'auto'
        ? (buildPhase === 'implement' ? 'implement' : 'plan')
        : activeBuildMode.id);
    const pinnedModel = (catalog && catalog[pinnedSlot]) || '';
    // Why the mode is not running its own slot's model. Without this the person who assigned
    // gpt-5.4 to Plan sees Gemini and has nothing to read — the guarantee they cannot see is the
    // one they file as a bug.
    const SLOT_NAME = { plan: 'Plan', implement: 'Implement', ask: 'Ask' };
    const pinWhy = signingSlot
      ? `${pinnedModel} signs its tool calls, so one session cannot mix it with another model. `
        + `Every Build turn runs on it while ${SLOT_NAME[signingSlot] || signingSlot} is assigned `
        + 'to it.'
      : '';
    const overridable = activeBuildMode.id === 'plan' || activeBuildMode.id === 'implement';
    // The four configured slots reduced to the models behind them: two slots pointing at one model
    // are one row, not two the person has to tell apart.
    const slotModels = catalog ? [...new Set([catalog.plan, catalog.implement, catalog.ask])] : [];
    const extraModels = (openWeightModels || []).filter((o) => !slotModels.includes(o.id));
    // The pinned row is the way BACK, so it carries no model id: picking it clears the override
    // rather than setting one, which is the difference between "Plan's model" and "this model,
    // which happens to be Plan's today".
    const PINNED_KEY = '__pinned__';
    // An override can name the model the mode is already pinned to — pick Plan's model while in
    // Implement, then switch to Plan. It is the pinned row at that point, and reading it as an
    // override would mark nothing selected and drop the "(default)" off a control that is running
    // exactly the default.
    const override = buildModel && buildModel !== pinnedModel ? buildModel : '';
    // Auto has no model of its own — it runs the Plan assignment while it plans and the Implement
    // assignment while it builds — so a bare id here changes under the person with nothing to say
    // why. The phase is the missing half of that sentence.
    const chipLabel = (name) => (activeBuildMode.id === 'auto'
      ? `${name} · ${buildPhase === 'implement' ? 'building' : 'planning'}`
      : name);
    // The way through to the assignments, from the menu that can only make an override. The two do
    // different things and say so: an override is this Builder's, until it restarts; an assignment
    // is the Project's (ADR-0017).
    const ASSIGNMENTS_KEY = '__assignments__';
    const buildModelMenu = {
      selectedKeys: [override || PINNED_KEY],
      items: [
        ...slotModels.map((id) => ({
          key: id === pinnedModel ? PINNED_KEY : id,
          label: id === pinnedModel ? `${id} (default)` : id,
        })),
        ...(extraModels.length
          ? [{
              type: 'group',
              label: 'Open-weight',
              children: extraModels.map((o) => ({ key: o.id, label: `${o.id} (${o.provider})` })),
            }]
          : []),
        { type: 'divider' },
        { key: ASSIGNMENTS_KEY, label: 'Model assignments…' },
      ],
      onClick: ({ key }) => {
        if (key === ASSIGNMENTS_KEY) return SW.store.openAssignments(true);
        return SW.store.setBuildModel(key === PINNED_KEY ? null : key);
      },
    };

    const modelMenu = {
      selectedKeys: effectiveModel ? [effectiveModel] : [],
      items: aliases.map((option) => ({
        key: option.alias,
        label: h(
          'div',
          { style: { minWidth: 200 } },
          h('div', { className: 'sw-model-option-name' }, option.name || option.alias),
          h('div', { className: 'sw-model-option-detail' }, option.alias)
        ),
      })),
      onClick: ({ key }) => {
        const next = aliases.find((a) => a.alias === key);
        const keep = next && (next.reasoning_efforts || []).includes(reasoningEffort)
          ? reasoningEffort
          : null;
        SW.store.setChatModel(key, keep);
      },
    };

    const effortMenu = {
      items: [
        { key: 'default', label: 'Default' },
        ...efforts.map((value) => ({ key: value, label: effortLabel(value) })),
      ],
      onClick: ({ key }) => {
        SW.store.setChatModel(model, key === 'default' ? null : key);
      },
    };

    const modeMenu = {
      selectedKeys: [activeBuildMode.id],
      items: BUILD_MODES().map((option) => ({
        key: option.id,
        label: h(
          'div',
          { style: { minWidth: 220, display: 'flex', alignItems: 'flex-start', gap: 12 } },
          h(
            'div',
            { style: { flex: 1 } },
            h('div', { className: 'sw-model-option-name' }, option.label)
          ),
          h('span', { className: 'sw-caption' }, option.key)
        ),
      })),
      onClick: ({ key }) => {
        SW.store.setBuildMode(key);
        setModeOpen(false);
      },
    };

    return h(
      'div',
      { className: 'sw-composer-inner' },

      // Questions asked and not started yet (#79). Above the box rather than in the transcript, and
      // deliberately: the transcript is the receipt, and a pending turn is an intention rather than
      // a commitment — nothing of it has run, and Cancel drops it without touching what is running.
      // Its own sentence comes from the server, so the queue explains itself in one voice wherever
      // it is drawn.
      queuedTurns.length > 0 &&
        h(
          'div',
          { className: 'sw-composer-queued' },
          queuedTurns.map((queued) =>
            h(
              'div',
              { key: queued.ticket, className: 'sw-composer-queued-row' },
              h(
                'div',
                { className: 'sw-composer-queued-text' },
                h('div', { className: 'sw-composer-queued-prompt' }, queued.text),
                // One Composer draws these in both modes, so the row has to say which one it
                // belongs to (#126). Unlabelled, a Chat question waiting behind a build appears
                // over the Build box reading like a queued build.
                h('div', { className: 'sw-caption' },
                  [queued.kind === 'chat' ? 'Chat' : 'Build', queued.message]
                    .filter(Boolean).join(' · '))
              ),
              h(
                Button,
                {
                  size: 'small',
                  type: 'text',
                  onClick: () => SW.store.cancelQueuedTurn(queued.ticket).catch(sayFailed),
                },
                'Cancel'
              )
            )
          )
        ),

      h(
        'div',
        {
          className: `sw-composer${dragOver ? ' is-dragover' : ''}`,
          onDragOver: (e) => {
            e.preventDefault();
            setDragOver(true);
          },
          onDragLeave: () => setDragOver(false),
          onDrop: (e) => {
            e.preventDefault();
            setDragOver(false);
            const resourceId = e.dataTransfer.getData('text/sw-resource');
            if (resourceId && resourceIndex[resourceId]) {
              SW.store.addToContext(resourceIndex[resourceId], { quiet: true }).catch(sayFailed);
            } else if (e.dataTransfer.files && e.dataTransfer.files.length) {
              handleFiles(e.dataTransfer.files);
            }
          },
        },

        // Chips are the conversation's context, so they stay put between turns.
        // Closing one takes it out of context for everything that follows.
        attachments.length > 0 &&
          h(
            'div',
            { className: 'sw-composer-chips' },
            attachments.map((att) =>
              h(
                Tooltip,
                {
                  key: att.id,
                  // The chips are the only place conversation context is shown
                  // now, so the reason Sage reached for something has to live
                  // here rather than in a panel zone.
                  title: att.addedBy === 'sage'
                    ? `${SW.brand.assistant()} added this — ${att.rationale || 'picked for you.'}`
                    : 'You added this to the conversation.',
                },
                h(
                  Tag,
                  {
                    bordered: true,
                    closable: true,
                    closeIcon: h(CloseOutlined, { style: { fontSize: 10 } }),
                    onClose: (e) => {
                      e.preventDefault();
                      // A failed removal leaves the chip on screen, so silence reads as a dead
                      // close button.
                      SW.store.removeFromConversation(att).catch(sayFailed);
                    },
                    className: 'sw-chip',
                  },
                  h('span', null, SW.util.iconFor(att.resourceKind)),
                  att.resourceName
                )
              )
            )
          ),

        // The first chip teaches its own scope (#137): a chip is Session context — this
        // Conversation's only — and the app someone builds declares its own Resources. The same
        // guard that draws the chip row draws the note, so it appears exactly when the first
        // chip does and never over an empty composer. Dismissal is for good.
        attachments.length > 0 && !chipHintDismissed &&
          h(
            'div',
            { className: 'sw-chip-hint' },
            h(
              'span',
              { className: 'sw-chip-hint-text' },
              'Added to this Conversation only — an app you build declares its own Resources.'
            ),
            h(
              Button,
              {
                type: 'link',
                size: 'small',
                style: { padding: 0, height: 'auto' },
                onClick: dismissChipHint,
              },
              "Don't show this again"
            )
          ),

        h(
          'div',
          { className: 'sw-composer-input' },
          mention &&
            suggestions.length > 0 &&
            h(
              'div',
              { className: 'sw-mention-pop' },
              h(
                'div',
                { className: 'sw-mention-head sw-group-label' },
                suggestions[0] && attachedIds.has(suggestions[0].id)
                  ? 'In context'
                  : suggestions[0] && suggestions[0].kind === 'artifact'
                    ? 'In this thread'
                    : suggestions[0] && catalogueIds.has(suggestions[0].id)
                      ? `Not in ${scope.name} yet`
                      : `In ${scope.name}`
              ),
              suggestions.map((resource, index) => {
                // The folder that tells this row from the other one wearing its name (ADR-0030).
                // Two colliding files drew two identical rows — same icon, same label, same caption
                // — that inserted the same text, which is the half of the defect a unique token
                // cannot reach: the right file could not be SEEN, let alone picked.
                //
                // Off the same peer list the token is built from, so the caption reads `2026`
                // exactly when the box will read `@2026/data.csv`. A row showing a folder its own
                // click does not carry would point at a file the click cannot reach.
                const folder = SW.util.mentionSuffix(resource.path, mentionPeers)
                  .split('/').slice(0, -1).join('/');
                // A folder row stands for files nobody can see, so it says how many (ADR-0030).
                // That is the one thing worth knowing before picking it, and it is the difference
                // between a row that reads as one file and a row that reads as the partition.
                //
                // It keeps the distinguishing folder beside the count, and for the reason this
                // caption exists at all: two Datasets partitioned by year both offer a row called
                // `2024`, which is the collision the row replaced arriving at the row itself. Same
                // suffix the token is built from, so the words on the row and the word in the box
                // still name one thing.
                const caption = resource.kind !== 'folder' ? folder
                  : `${resource.count} files${folder ? ` in ${folder}` : ''}`;
                return h(
                  'button',
                  {
                    key: resource.id,
                    className: `sw-mention-item${index === cursor ? ' is-active' : ''}`,
                    onMouseEnter: () => setCursor(index),
                    onMouseDown: (e) => e.preventDefault(),
                    onClick: () => pickMention(resource),
                  },
                  h('span', { className: 'sw-res-icon' }, SW.util.iconFor(resource.kind)),
                  // The whole path in `title`, the way `LeafRow` already does it in the Dataset
                  // tree: the folder beside it says WHICH of the two this is, and the title says
                  // where it lives without spending a row's width on it.
                  h('span', { className: 'sw-mention-name', title: resource.path || resource.name },
                    resource.name),
                  caption ? h('span', { className: 'sw-caption' }, caption) : null,
                  attachedIds.has(resource.id)
                    ? h('span', { className: 'sw-incontext-tag' }, 'in context')
                    // The menu has ONE heading and it describes the first row only. A catalogue
                    // row sits last, so whenever anything is above it that heading reads
                    // `In {project}` — the exact opposite of true for this row. It says so
                    // itself, the way `in context` already does for the same reason.
                    : catalogueIds.has(resource.id)
                      ? h('span', { className: 'sw-caption' }, `not in ${scope.name}`)
                      // The kind is what a row says when it has nothing more useful to say. A row
                      // that has just named its folder does, and two captions are one more than the
                      // slot holds — so the kind gives way, being the half that tells nothing apart.
                      : caption
                        ? null
                        : h('span', { className: 'sw-caption' }, SW.util.labelFor(resource.kind))
                );
              })
            ),
          h(Input.TextArea, {
            value: text,
            autoFocus,
            disabled,
            placeholder: placeholder || SW.util.composerPlaceholder('Describe your app, or a change to make'),
            autoSize: { minRows: compact ? 1 : 2, maxRows: 8 },
            onChange: (e) =>
              changeText(e.target.value, e.target.selectionStart, e.nativeEvent && e.nativeEvent.inputType),
            onKeyDown: (e) => {
              // One Backspace takes the whole @mention rather than a letter of it. The token is
              // plain text in the box and not a chip, so the browser's own key would spend
              // fourteen strokes on "@BigQuery_Demo" — and every stroke in between leaves a
              // fragment that names nothing and matches nothing. Only while the picker is CLOSED:
              // mid-typing, the same key is how a person narrows the query.
              if (e.key === 'Backspace' && !mention) {
                const el = e.target;
                const caret = el.selectionStart;
                // A selection already says what to delete. A caret inside the token means the
                // letter behind it, because eating the rest would be a forward delete.
                const atEnd = caret === el.value.length || /\s/.test(el.value[caret]);
                if (caret === el.selectionEnd && atEnd) {
                  // The same reading the picker uses, so the two cannot disagree about where a
                  // mention starts — a finished mention is textually the unfinished one.
                  const found = mentionAt(el.value, caret);
                  // Widen the selection rather than rewrite the value: the browser's own
                  // Backspace then does the delete, which keeps undo and the caret intact.
                  if (found && found.query) el.setSelectionRange(found.start, caret);
                }
                return;
              }
              if (!mention || suggestions.length === 0) return;
              if (e.key === 'ArrowDown') {
                e.preventDefault();
                setCursor((c) => (c + 1) % suggestions.length);
              } else if (e.key === 'ArrowUp') {
                e.preventDefault();
                setCursor((c) => (c - 1 + suggestions.length) % suggestions.length);
              } else if (e.key === 'Tab') {
                e.preventDefault();
                pickMention(suggestions[cursor]);
              } else if (e.key === 'Escape') {
                e.preventDefault();
                setMention(null);
              }
            },
            onPressEnter: (e) => {
              if (mention && suggestions.length > 0 && !e.shiftKey) {
                e.preventDefault();
                pickMention(suggestions[cursor]);
                return;
              }
              if (e.metaKey || e.ctrlKey || !e.shiftKey) {
                e.preventDefault();
                send();
              }
            },
          })
        ),

        h(
          'div',
          { className: 'sw-composer-bar' },
          !showMode &&
            h(
              Dropdown,
              { menu: modelMenu, trigger: ['click'], placement: 'topLeft' },
              h(
                Button,
                { size: 'small' },
                h(Space, { size: 4 }, modelLabel, h(DownOutlined, { style: { fontSize: 9 } }))
              )
            ),
          !showMode &&
            efforts.length > 0 &&
            h(
              Dropdown,
              { menu: effortMenu, trigger: ['click'], placement: 'topLeft' },
              h(
                Button,
                { size: 'small' },
                h(Space, { size: 4 }, effortLabel(reasoningEffort), h(DownOutlined, { style: { fontSize: 9 } }))
              )
            ),
          h(
            Dropdown,
            {
              menu: attachMenu,
              trigger: ['click'],
              placement: 'topLeft',
              open: attachOpen,
              onOpenChange: (open) => {
                setAttachOpen(open);
                // The pointer is still on the button when the menu closes, so
                // nothing will deliver the mouseleave that clears the hint —
                // left alone it pops back up over the spot the menu just left.
                // Hovering again brings it back, which is the whole hint.
                if (!open) setAttachHint(false);
              },
            },
            h(
              Tooltip,
              {
                title: 'Attach a file or resource',
                // The menu and the hint come out of the same corner of the same
                // button, so a hint left open is drawn over the list. It gets
                // left open because the click that opens the menu happens under
                // the pointer, and the mouseleave that would close it never
                // comes.
                open: attachHint && !attachOpen,
                onOpenChange: setAttachHint,
              },
              h(Button, { size: 'small', icon: h(PlusOutlined, null), 'aria-label': 'Attach' })
            )
          ),
          h('span', { className: 'sw-composer-bar-spacer' }),

          // Beside the mode pill, because the mode is what decides what this falls back to. Every
          // mode can be changed now (ADR-0017) — Plan and Implement through an override, Auto and
          // Ask through the assignment behind them — so the only closed state left is a running
          // turn.
          showMode && pinnedModel &&
            (buildRunning
              ? h(
                  Tooltip,
                  {
                    // Unlike the mode, neither a pick nor an assignment is pinned for the turn:
                    // both are read live, so a change here would move the rest of this build onto
                    // another model with the first half's tool calls in context. There is no queue
                    // to put it in either, so the control closes instead.
                    title: `This turn is running on ${override || pinnedModel}. Wait for it to finish to change the model.`,
                  },
                  // The span is load-bearing: a browser dispatches no mouse events on a disabled
                  // button, so a Tooltip put straight on one never opens and the sentence above
                  // becomes the silence it was written to prevent.
                  h('span', { style: { display: 'inline-block' } },
                    h(Button, { size: 'small', disabled: true, 'aria-label': 'Build model' },
                      chipLabel(override || pinnedModel)))
                )
              : overridable
                ? (() => {
                    const control = h(
                      Dropdown,
                      { menu: buildModelMenu, trigger: ['click'], placement: 'topRight' },
                      h(
                        Button,
                        { size: 'small', 'aria-label': 'Build model' },
                        h(Space, { size: 4 }, override || `${pinnedModel} (default)`,
                          h(DownOutlined, { style: { fontSize: 9 } }))
                      )
                    );
                    // Wrapped only when there IS something to say. Plan and Implement have never
                    // carried a tooltip, and the menu still works — an in-session pick outranks the
                    // pin, which is the router's own rule (ADR-0032). So this explains the label
                    // without taking the control away.
                    return pinWhy ? h(Tooltip, { title: pinWhy }, control) : control;
                  })()
                // Ask and Auto honour no override — Ask is pinned to its slot and Auto follows the
                // phase — so there is no menu to offer. They open the panel instead: a disabled
                // control with a working door behind it answers "why can't I change this" with
                // nothing, which is what this button used to be.
                : h(
                    Tooltip,
                    {
                      // `pinWhy` first: the Auto sentence below names two models, and under the
                      // pin there is only one. A confident, specific, false sentence is the worst
                      // thing this control can say.
                      title: pinWhy || (activeBuildMode.id === 'ask'
                        ? `Ask runs on ${pinnedModel}, and so does Chat.`
                        : `Auto runs ${(catalog || {}).plan} to plan and ${(catalog || {}).implement} to build.`),
                    },
                    h(
                      Button,
                      {
                        size: 'small',
                        'aria-label': 'Build model',
                        onClick: () => SW.store.openAssignments(true),
                      },
                      h(Space, { size: 4 }, chipLabel(pinnedModel),
                        h(DownOutlined, { style: { fontSize: 9 } }))
                    )
                  )),

          showMode &&
            h(
              Dropdown,
              {
                menu: modeMenu,
                trigger: ['click'],
                placement: 'topRight',
                open: modeOpen,
                onOpenChange: setModeOpen,
              },
              h(
                'button',
                {
                  className: `sw-phase-pill${activeBuildMode.id === 'ask' ? ' is-ask' : ''}`,
                  type: 'button',
                  'aria-label': 'Build mode',
                  title: modeQueued
                    ? `This turn is still ${BUILD_MODE_LABEL[buildTurnMode] || buildTurnMode}. Your pick applies to the next message.`
                    : (activeBuildMode.id === 'ask'
                      ? 'Ask mode answers questions and never changes files'
                      : 'Mode'),
                },
                  h('span', {
                    className:
                      'sw-dot ' +
                      (activeBuildMode.id === 'ask'
                        ? 'sw-dot-ask'
                        : activeBuildMode.id === 'implement'
                        ? 'sw-dot-building'
                        : 'sw-dot-draft'),
                  }),
                  BUILD_MODE_LABEL[activeBuildMode.id],
                  h(DownOutlined, { style: { fontSize: 9 } })
                )
            ),

          h(
            Tooltip,
            {
              title: `Send · ${SW.util.shortcut('⌘⏎')}`,
              // A hint for a button that cannot be pressed is noise, and it is
              // also how the hint got stuck: sending empties the box under the
              // pointer, so the mouseleave that would close it never comes.
              open: sendHint && Boolean(text.trim()) && !disabled,
              onOpenChange: setSendHint,
            },
            h(Button, {
              type: 'primary',
              shape: 'circle',
              size: 'small',
              // An empty box and a wedged workspace, and nothing else. The mention warning below
              // is deliberately absent from this line (#136): a mention is often incidental, the
              // rest of the prompt still runs, and a guard that closed the send would have taken
              // the turn away to save the mention.
              disabled: !text.trim() || disabled,
              icon: h(ArrowUpOutlined, null),
              onClick: send,
              'aria-label': 'Send message',
            })
          )
        ),

        h('input', {
          ref: fileRef,
          type: 'file',
          multiple: true,
          style: { display: 'none' },
          onChange: (e) => {
            handleFiles(e.target.files);
            e.target.value = '';
          },
        })
      ),

      // Under the box, not inside it: the box is what you are writing and this is what will happen
      // to it, and a warning drawn within the border reads as a field that has failed validation —
      // which would say the send is blocked, the one thing this must never say.
      unusable.length > 0 &&
        h(MentionGuard, { entries: unusable, activeAppId: activeApp && activeApp.id })
    );
  };
})();
