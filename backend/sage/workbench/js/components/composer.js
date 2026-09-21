window.SW = window.SW || {};

(function () {
  const { createElement: h, useState, useRef, useEffect } = React;
  const { Input, Button, Dropdown, Tag, Tooltip, Space } = antd;
  const { PlusOutlined, ArrowUpOutlined, DownOutlined, CloseOutlined,
          InfoCircleOutlined, LockOutlined } = icons;

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

  // 'Model default' rather than 'Default', because `none` is now a level some aliases really
  // offer (gpt-5.4 measured 2026-09-12, #280) and it means the opposite thing: 'Model default'
  // sends no field and lets the alias reason as it likes, 'None' sends the field and turns
  // reasoning off. Two menu entries a row apart cannot both be called Default.
  function effortLabel(value) {
    if (!value) return 'Model default';
    if (value === 'xhigh') return 'Extra high';
    return value.charAt(0).toUpperCase() + value.slice(1);
  }

  // Build's menu offers a level only underneath the model it belongs to, so one click has to
  // deliver both halves of the pick (ADR-0049). The key carries them; there is no second click and
  // no window in between where a level chosen for one alias stands against another.
  //
  // `::` is the join. What keeps it safe is NOT the separator being impossible in an alias id — the
  // menu is also clicked with a BARE model id, from a row that advertises no levels, and no amount
  // of `lastIndexOf` can tell `foo::bar` the model from `foo` at level `bar`. So the caller says
  // which it has rather than the string being asked to confess: `onClick` looks the key up among
  // the rows it just built, and splits only one that is not a row of its own.
  const EFFORT_SEP = '::';
  // The no-level row keys as an EMPTY suffix rather than the word `default`. `default` read well and
  // rested on an invariant nobody enforces: the route deliberately does not validate `pick_effort`
  // (`ModelControl.pick` stores an unrecognised level and lets the send path drop it), so a stored
  // level of literally `"default"` would emit two children with one key — the enabled no-level row
  // and the disabled stranded one — and the menu would mark the wrong one.
  //
  // An empty level cannot collide because it is not a level anywhere: `_EFFORT_VALUES` holds none,
  // and `ModelControl.pick` already normalises `""` to no effort. That is a property of the
  // encoding rather than a promise about what somebody might store.
  const effortKey = (model, effort) => `${model}${EFFORT_SEP}${effort || ''}`;
  function splitEffortKey(key) {
    const at = key.lastIndexOf(EFFORT_SEP);
    if (at < 0) return [key, null];
    const level = key.slice(at + EFFORT_SEP.length);
    return [key.slice(0, at), level || null];
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
  // The session moved onto an approved model, said once (ADR-0043). Decided deliberately over the
  // two alternatives: switching in silence fails the confidence the whole promise is meant to build
  // — a picked model changes under somebody with nothing to read — and refusing the turn stops
  // somebody mid-task to teach them what a sentence can teach.
  //
  // It names the model it moved to, from the same `lockedRunsOn` the chip above the box reads — the
  // router resolved it server-side and neither surface re-derives it. Both or neither: a notice
  // naming one model while the chip names another is a correction that contradicts the control it
  // is correcting. The vague sentence survives for the state that carries no answer.
  //
  // Under the box beside the mention guard, and for the same reason: the box is what you are
  // writing, and this is what will happen to it.
  function LockNotice({ sensitivity, picked, chat, app, onDismiss }) {
    const runsOn = SW.util.lockedRunsOn(sensitivity, chat);
    const using = runsOn
      ? SW.brand.text('Using {name}', { name: runsOn })
      : SW.brand.text('Using an approved model');
    // `declaredPhrase`, which is what the picker row's own sentence is built from (#264). This said
    // the KIND — "this Dataset" — while the greyed row beside it named the Dataset, so a creator
    // who read both learned that a declared Dataset was the reason twice and which one once. The
    // shared helper is the only thing that keeps the two from drifting again, and it is also where
    // the sticky lock's anonymity lives: under `session` there is no row left to name, and naming
    // one that has been unbound sends somebody to remove something already gone (ADR-0043).
    const datasets = SW.util.declaredPhrase(sensitivity);
    const why = SW.util.lockedBySession(sensitivity)
      ? SW.brand.text("{picked} isn't allowed — this chat already used {datasets}.",
        { datasets, picked })
      : SW.brand.text("{picked} isn't approved for {datasets}.", { datasets, picked });
    const wayOut = SW.util.lockWayOut(sensitivity, app, chat);
    return h(
      'div',
      // Same place as the mention guard — under the box — and deliberately not the same shape.
      // That one is a warning about a prompt still being written. This is a confirmation of a
      // switch that has already happened, so it is a line, not a filled incident.
      { className: 'sw-lock-notice' },
      h(InfoCircleOutlined, { className: 'sw-lock-notice-icon' }),
      h(
        'div',
        { className: 'sw-lock-notice-body' },
        h('div', { className: 'sw-lock-notice-text' }, `${using} — ${why}`),
        // The way out, and there is one under either reason (#264). It used to be drawn for the
        // sticky lock alone, on the grounds that the notice is about the model and not about the
        // data — but the sentence above now names a Dataset, and naming something without saying
        // where it can be acted on is the dead end ADR-0011 exists to close.
        wayOut ? h('div', { className: 'sw-lock-notice-way' }, wayOut) : null
      ),
      h(Button, {
        size: 'small',
        type: 'link',
        className: 'sw-lock-notice-link',
        onClick: () => SW.store.openAssignments(true),
      }, 'Allowed models'),
      h(Button, {
        size: 'small',
        type: 'text',
        className: 'sw-lock-notice-dismiss',
        icon: h(CloseOutlined, { style: { fontSize: 10 } }),
        onClick: onDismiss,
        'aria-label': 'Got it',
      })
    );
  }

  function MentionGuard({ entries, activeAppId, onSend }) {
    const [busy, run] = SW.util.useBusyAct();
    // The third argument is what the click does after it has written the record (#213): send what
    // is in the box. Decided rather than assumed — binding and leaving the person to press Send
    // costs the second click this warning was reported for — and it is the composer's own send, so
    // the box clears and the turn starts exactly as it would have without the warning.
    const fixes = SW.store.mentionFixes(entries, activeAppId, onSend);
    if (!fixes.length) return null;
    const offered = new Set(fixes.map((fix) => fix.key));
    // The token the picker INSERTED, not the row's name. `mentionToken` collapses whitespace, so a
    // Resource called "Sales Warehouse" stands in the box as `@Sales_Warehouse` — and a warning that
    // quoted the name would send the reader looking for a word their prompt does not contain.
    const shown = entries
      .filter((e) => offered.has(`${e.kind}:${e.id}`))
      .map((e) => ({ kind: e.kind, table: e.table, source: e.source, scope: e.scope,
        token: SW.util.mentionToken({ name: e.name, path: e.kind === 'file' ? e.id : '' }) }));
    // A table the Scope falls short of is split off first, because it is the one row here that is
    // NOT about a record the app is missing — it has the Resource, and the gap is which part of it
    // the app reads. Left in the line below, "doesn't use @DIM_ACCOUNT yet" would say the opposite
    // of what the panel shows, and the button beside it says Choose rather than Use.
    const offScope = shown.filter((e) => e.table);
    const rest = shown.filter((e) => !e.table);
    const aliases = rest.filter((e) => e.kind === 'llm_alias').map((e) => e.token);
    const named = rest.filter((e) => e.kind !== 'llm_alias').map((e) => e.token);
    // Named in both halves, because a Project holds many Built Apps (ADR-0008), and every row
    // carries the same app.
    const app = entries[0].app;
    // An Alias is the one kind whose mention is not a failed delivery. A bound Alias is chosen per
    // call in the prompt (`resources/pinned_model.bound_aliases`), so what one click buys here is a
    // capability the app keeps — in this build and in the published app — and a sentence about a
    // message not arriving would describe the smaller half of what is on offer.
    //
    // Both lines say the state now, not what a send would cost (#213). They used to spell out what
    // the click was for, and the button below IS that — it binds and sends in one — so directions
    // beside it describe the road it goes round. The Alias half is the refusal's own sentence word
    // for word, which is the point of the pair (#136); the other half says the same thing about the
    // same two lists, in the shape a warning takes rather than a report of a turn that ran.
    //
    // The third names the Scope, which is the whole of what the reader needs: "reads X, not @Y"
    // says what will happen to the mention and why in one clause, and the store is named because
    // two bound warehouses can each hold a table of that name. One line however many tables, since
    // they share the Binding whose Scope the button opens.
    const guardLines = [
      aliases.length && `${app} can't call ${aliases.join(', ')} yet.`,
      named.length && `${app} doesn't use ${named.join(', ')} yet.`,
      // `scope` is "" for a store bound with no Scope yet, which is the ordinary state of one bound
      // from the header (#142) — and "reads  inside Warehouse" is the sentence that would make.
      offScope.length && (offScope[0].scope
        ? `${app} reads ${offScope[0].scope} inside ${offScope[0].source}, `
          + `not ${offScope.map((e) => e.token).join(', ')}.`
        : `${app} hasn't chosen what it reads inside ${offScope[0].source}.`),
    ].filter(Boolean);
    return h(
      'div',
      { className: 'sw-mention-guard' },
      h('div', { className: 'sw-mention-guard-text' }, guardLines.join(' ')),
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

  // The offer, drawn in Build beside the chips it is about (#275). The Build tab is the other way
  // into a Built App and it crossed nothing, so chips added in Chat reached it as decoration: named
  // over the composer, held by nothing, refused by the first turn that mentioned one.
  //
  // It offers and never crosses on its own, because a Binding is a person's pick (ADR-0010) and
  // arriving in Build is not one. The sentence is built in the store beside the rows it counts, so
  // the number and the names cannot drift apart.
  function CrossingOffer({ offer, onCross }) {
    const [busy, run] = SW.util.useBusyAct();
    return h(
      'div',
      { className: 'sw-crossing-offer' },
      h('div', { className: 'sw-crossing-offer-text' }, offer.text),
      h(Button, {
        size: 'small',
        // The suggested next act on arriving in Build, and the only filled button here — the
        // mention guard below draws its own only when the prompt already names a gap this closes.
        type: 'primary',
        loading: busy === 'cross',
        disabled: !!busy,
        onClick: run('cross', onCross),
      }, offer.label)
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
      catalog, buildModel, buildEffort, buildPhase, openWeightModels, signingSlot,
      apps, activeApp, composerSeed, queuedTurns, catalogueParents, appAttachments,
      sensitivity, sensitivityNoticeFor, crossingRefused,
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
    const pickedLabel = activeAlias ? (activeAlias.name || activeAlias.alias) : (effectiveModel || 'Ask');
    // The chip names what will RUN, not what was picked (ADR-0043). The pick is kept underneath and
    // comes back when the declaration goes, the way the per-turn mode pin leaves the standing mode
    // alone — but while the lock holds, the label a person reads has to be true.
    // Judged on `effectiveModel` — the gateway alias, which is the space `approved` is in — and
    // DISPLAYED as `pickedLabel`, which is the row's `display_name or name` and can differ. The menu
    // below has always keyed on `option.alias`; this used to key on the label and so could mark an
    // approved Alias barred. `true`: this chip is drawn under `!showMode`, so the turn behind it is
    // a Chat turn, which the router pins to the sovereign Ask slot rather than the build mode.
    const modelLabel = SW.util.lockedLabel(sensitivity, effectiveModel, pickedLabel, true);
    // One rule for "which levels does this alias offer", shared by the Chat picker below and the
    // Build menu further down. Validity is per alias and measured (ADR-0049), not anything a name
    // predicts, so the answer has one source; two copies of it in one file could only disagree,
    // and a menu offering a level its alias refuses is a 400 on the turn rather than a wrong label.
    const aliasRow = (id) => aliases.find((a) => a.alias === id);
    const effortsFor = (id) => ((aliasRow(id) || {}).reasoning_efforts_with_tools) || [];
    const effortNoteFor = (id) => SW.util.effortNote(aliasRow(id));
    // The server's sentence about what an alias never claimed it could do, read off the same row
    // `effortsFor` reads and never re-derived from `capabilities` here (#463). Empty for a model
    // with no alias row at all — the open-weight options are ids from `/healthz` and carry no
    // capability list, and no evidence must not be drawn as a fault.
    const capabilityFor = (id) => ((aliasRow(id) || {}).capability_note) || '';
    // No field means no evidence, not a measured refusal. Offer no levels without evidence;
    // the separate stranded-level checks keep stored values visible until evidence arrives.
    const efforts = effortsFor(effectiveModel);

    // What a chip's click actually did. In Build a mentioned Dataset file is an Attachment — the
    // bytes are copied into the selected app and a committed manifest entry rehydrates them on
    // publish (ADR-0048) — and the `@` menu has no label to say so, so the chip carries the
    // receipt. Read off `attachedApp`, which the server wrote when the fork actually ran, rather
    // than off the mode and the current selection: the chip is the Conversation's, so a
    // re-derived mark relabels itself on the next app switch and marks a Chat mention the moment
    // somebody opens Build. Neither is a receipt. Resolved through `apps` so a renamed app reads
    // as its new name, and an app since deleted says nothing rather than an id. Not a toast: a
    // toast is gone in eight seconds and the Attachment outlives the Conversation.
    const attachMark = (att) => {
      const inApp = att.attachedApp && (apps || []).find((a) => a.id === att.attachedApp);
      return inApp ? h('span', { className: 'sw-chip-scope' }, `In ${inApp.name}`) : null;
    };

    const attachedIds = new Set(attachments.map((a) => a.resourceId));
    // The list uniqueness is computed against, for the token this menu INSERTS and for the folder its
    // rows show (ADR-0030). One list for both, so the folder a person reads on a row and the token
    // that lands in the box cannot disagree about which of two `data.csv` the click meant. The
    // folder rows are in it as well as the files: a folder row is given a token too, and two
    // partitions both called `2026` would otherwise both be offered as `@2026`.
    const mentionPeers = SW.util.attachmentPeers(appAttachments);
    // Artifacts this clone actually has. A chart the Project never committed survives in the
    // manifest and not on disk (#255), and mentioning one hands the turn a path that resolves to
    // nothing — the same dead reference the card was changed to stop drawing, on the one surface
    // where it degrades the answer silently instead of showing it.
    const mentionArts = ((thread && thread.artifacts) || []).filter((a) => !a.missing);
    const suggestions = mention
      ? mentionCandidates(attachments, resourceGroups, mention.query, mentionArts,
                          catalogueParents, appAttachments, showMode)
      : [];
    const catalogueIds = new Set((catalogueParents || []).map((r) => r.id));
    const buildModes = BUILD_MODES();
    const activeBuildMode = buildModes.find((m) => m.id === buildMode) || buildModes[0];
    const modeQueued = showMode && buildRunning && buildTurnMode && buildTurnMode !== buildMode;
    // Which mode the MODEL chip describes. While a turn runs that is the turn's, not the picker's:
    // the mode picker stays live mid-turn — `modeQueued` one line up exists to say so — and every
    // value derived below is about which model and which level are ON THE WIRE. Switch the selector
    // to Auto during a Plan build and a chip reading the selector names the Auto slot and drops the
    // pick's level, while the turn runs on the pick at that level; switch it to Plan during an Auto
    // build and the chip names a pick Auto honours no part of.
    //
    // Falls back to the selection whenever no turn holds a mode, so the menu path is unchanged by
    // construction — `buildTurnMode` is set only while a turn is pinned, and while one is pinned no
    // menu is drawn at all.
    const chipModeId = (buildRunning && buildTurnMode) || activeBuildMode.id;

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

    // The same question, asked of the chips rather than of the text (#275). Build only, and read on
    // every render for the reason above — which is also what makes it re-ask on an app switch, since
    // both lists arrive with the app.
    const notInApp = showMode ? SW.store.chipsNotInApp() : [];
    const crossing = showMode ? SW.store.crossingOffer() : null;
    const missingChip = (att) => notInApp.find((r) => r.id === att.id);

    // What a chip says when the selected app does not hold it. `attachMark` above records where
    // bytes WENT and is deliberately not re-derived; this reports what the app holds NOW, which is
    // the question somebody switching apps is asking. Both can be true of one chip: bytes crossed
    // into the app on the left, and this one on the right holds nothing.
    const chipNote = (att, who) => {
      const missing = missingChip(att);
      if (!missing) return who;
      // A refused chip carries its own reason where there is one. `Not in <app>` alone would read as
      // the same plain gap its neighbours have, when in fact this one has been tried and refused.
      const refused = crossingRefused && crossingRefused.appId === missing.appId
        ? (crossingRefused.byName || {})[att.resourceName] : '';
      return `${refused || `Not in ${missing.app}`} · ${who}`;
    };

    const send = () => {
      const value = text.trim();
      if (!value || disabled) return;
      setText('');
      setMention(null);
      // Sending disables the button under the pointer, so nothing ever fires
      // the mouseleave that would dismiss its hint.
      setSendHint(false);
      // Returned, not dropped: the mention warning's button sends through this one, and the spinner
      // it puts on itself lasts exactly as long as what it is waiting for.
      return onSend(value);
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

    // The bar's button (#275). A crossing is per chip and can be half refused, so what comes back is
    // reported rather than assumed: the first refusal is said out loud here, and every refusal keeps
    // its own words on its own chip. A success names the app, since the whole question was which app
    // holds this.
    const cross = () => SW.store.crossChipsToApp().then((res) => {
      const refused = (res && res.refused) || [];
      const unresolved = (res && res.unresolved) || [];
      // The app the SERVER wrote into, which is the one the person needs named: it resolves the
      // target from the live selection, so a switch landing first crosses somewhere else.
      const where = (res && res.appName) || (activeApp ? activeApp.name : 'the app');
      if (refused.length) {
        antd.message.warning(refused[0].reason || `${refused[0].name} stayed in Chat.`);
      } else if (unresolved.length) {
        // Added, and still unable to open. Saying only "added" would leave somebody to find out
        // from the first turn that queries it.
        antd.message.warning(
          `Added to ${where}, but Domino could not open ${unresolved.join(', ')}. `
          + 'Re-bind it in the Data panel.');
      } else {
        antd.message.success(`Added to ${where}.`);
      }
    }).catch(sayFailed);

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
    // this stopped being.
    // The name is QUOTED because it is usually a sentence: a display name starts as the title of the
    // plan the app was built from, and those end in a full stop, which unquoted lands one in the
    // middle of this question.
    const confirmReset = () => {
      antd.Modal.confirm({
        title: activeApp ? `Reset "${activeApp.name}" to the starter template?`
          : 'Reset this app to the starter template?',
        content: "This app's code is removed and can't be recovered. Files, resources, and this "
          + 'conversation stay.',
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
    // One derivation, called with whichever mode the READER is asking about. The chip asks about
    // the turn; the lock notice asks about the next turn, which is the selector's. Scoping this
    // once and letting every reader inherit it is what leaked a turn-scoped answer into a notice
    // that renders mid-turn — the justification "while a turn is pinned no menu is drawn" was true
    // of the MENU and applied to consumers that are not the menu.
    const slotOf = (modeId) => signingSlot || (modeId === 'ask'
      ? 'ask'
      : modeId === 'auto'
        ? (buildPhase === 'implement' ? 'implement' : 'plan')
        : modeId);
    const runsFor = (modeId) => {
      const slot = slotOf(modeId);
      const model = (catalog && catalog[slot]) || '';
      const canOverride = modeId === 'plan' || modeId === 'implement';
      return {
        slot,
        model,
        override: canOverride && buildModel && buildModel !== model ? buildModel : '',
      };
    };
    const pinnedSlot = slotOf(chipModeId);
    const pinnedModel = (catalog && catalog[pinnedSlot]) || '';
    // The sensitivity lock, read once for both pickers below (ADR-0043). Chat is gated exactly as
    // Build is — `llm_router` applies the lock OUTSIDE their fork — so both menus have to say so,
    // and saying it from one place is what keeps them saying the same thing.
    //
    // A row is disabled and NOT hidden, the way a stopped endpoint's is: a model that vanishes
    // teaches nobody why it went, and here the reason is the whole point. Leaving it selectable
    // would be worse still — the turn refuses it, so the explanation would arrive after the work
    // rather than before it.
    const lockedHere = SW.util.isLocked(sensitivity);
    const lockNote = (name) => (sensitivity && sensitivity.refusal)
      || SW.util.lockReason(sensitivity, name);
    const barredModel = (name) => lockedHere && !SW.util.isApproved(sensitivity, name);

    const overridable = chipModeId === 'plan' || chipModeId === 'implement';
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
    //
    // Gated on `overridable`: the router (llm_router._resolve_build) reads `picked_model` only in
    // Plan and Implement and ignores it in Auto and Ask, but `buildModel` itself is never cleared
    // on a mode switch (ModelControl.set_mode does not touch it). Without this gate, a pick made
    // while in Implement kept showing up as the Auto/Ask chip's label after switching away — a
    // model the assignments drawer never named and the router was no longer honouring.
    const override = overridable && buildModel && buildModel !== pinnedModel ? buildModel : '';
    // Build's chip under the lock (ADR-0043), the same rule as the Chat chip one bar over: the
    // label has to name what will RUN. It reads `sensitivity.model`, which is Build's half of the
    // server's answer — `chat_model` is Chat's, and they differ wherever the sovereign Ask slot and
    // the sovereign plan/implement slot are different approved models.
    //
    // No second name to reconcile here, unlike Chat's chip: the slots come off the catalog and the
    // menu keys on `option.alias`, so everything Build routes by is already in alias space.
    // Why the mode is not running its own slot's model. Without this the person who assigned
    // gpt-5.4 to Plan sees Gemini and has nothing to read — the guarantee they cannot see is the
    // one they file as a bug.
    //
    // Empty under an override, which is why it is read here and not up beside `pinnedModel`:
    // precedence is in-session act > pin, and `_pin_signing` returns a PLAN_OVERRIDE or
    // IMPLEMENT_OVERRIDE decision untouched. So a pick really does beat the pin, the chip beside
    // this sentence names the pick, and saying the session requires another model would have the
    // control contradict itself in two consecutive sentences (#276).
    const pinWhy = signingSlot && !override
      ? `${pinnedModel} is required for this session, so every Build turn uses it.`
      : '';
    // The chip is the only thing left on screen once the menu closes, so a level picked in there
    // has to be readable here too — otherwise the person who chose one has no evidence it took, and
    // a setting with no receipt reads as a setting that was dropped. Only when they chose one: a
    // pick left on the alias's own default says nothing, which is what it did before this existed.
    // The level standing against `id` that its alias will not take — the one the menu has to show
    // marked rather than drop, and the one the chip must not name. Only ever the OVERRIDE's:
    // `buildEffort` belongs to the picked model, so no other row can strand it.
    //
    // Gated on the alias ROW EXISTING, which is the distinction `effortsFor` on its own cannot make.
    // An empty `reasoning_efforts` means this alias advertises no levels; a MISSING row means the
    // listing has not landed or the gateway leg refused it (`gatewayAliases` starts empty, and a leg
    // that 40x's at boot leaves it that way until something else re-reads). Those are opposite
    // facts, and reading the second as the first strands a level the model takes perfectly well —
    // making this control say the specific, false thing the chip comment below forbids. `store.js`'s
    // `kept()` already draws the same line for the drawer's half: an alias the listing did not carry
    // is a prediction with no evidence, not evidence of a drop.
    // Keyed on `buildModel` and not on `override`: the level belongs to the model the person
    // actually picked, and `override` collapses to '' in the one case below where that model is the
    // mode's own pinned one. Same answer for every row the menu draws — `withEfforts` skips the
    // pinned row anyway — and the right answer for the chip, which has to cover the collapse.
    const strandedLevel = (id) => {
      const alias = aliasRow(id);
      // `!alias.reasoning_efforts_with_tools` rides with `!alias` for one reason: both are the
      // absence of an answer rather than an answer of "no". A row from a producer that does not
      // publish this field would otherwise strand every level it holds.
      if (!alias || !alias.reasoning_efforts_with_tools) return '';
      if (id !== buildModel || !buildEffort) return '';
      // Judged against the TOOL-carrying list, the same one the submenu offers. Against the enum, a
      // level this alias drops beside tools reads as perfectly fine — `gpt-5.4` at `high` is in the
      // enum and dropped on every Build turn — and the chip would name it over a turn running at the
      // alias default (#295).
      //
      // The server resolves this list with the same local rule as save and send (#284, #298).
      return (alias.reasoning_efforts_with_tools || []).includes(buildEffort) ? '' : buildEffort;
    };
    // The level the live pick is running at, where there is one the alias will take. Empty for no
    // pick, no level, a stranded level, or a mode that honours no pick at all.
    const pickedLevel = overridable && buildModel && buildEffort && !strandedLevel(buildModel)
      ? buildEffort
      : '';
    //
    // A STRANDED level is left off, and that is the chip doing its own job rather than an omission:
    // this label names what will RUN, and a level the alias will not take is one the send path
    // drops, so the turn runs at the alias's default. Naming it here would be the one thing this
    // control must never do — a confident, specific, false sentence. The submenu is where the
    // stranded level is read, marked for what it is, and where the way off it is.
    // Three cases and not two, because of the collapse `override` performs just above: a pick that
    // names the mode's OWN pinned model reads as no override, which is right about the model — it
    // really is the slot's — and wrong about the level, because a level the person chose is not the
    // assignment's. `(default)` over one of those would have the control claim the slot's setting
    // while the turn runs theirs, which is this ticket's own defect arriving through the one door
    // the menu cannot mark. The menu still marks `__pinned__` there; that row does clear the pick,
    // so the selection is incomplete rather than false, and the chip is the half that must be true.
    // What the way-back row should mean once it can also carry a level is #310.
    //
    // `(default)` is a claim about the SLOT'S ASSIGNMENT — its model and its effort together — so it
    // may only be made where that assignment is what routes. ANY live pick makes `_resolve_build`
    // return an OVERRIDE, and an override carries the PICK's effort rather than
    // `catalog.<slot>_effort`: `None` where the person chose no level, the alias's own default where
    // the level they chose is stranded. Both differ from the assignment wherever the slot carries a
    // level, so `(default)` over either is the control naming a setting the turn is not running.
    //
    // Measured, not reasoned: with `plan_effort="low"` and a pick naming plan's own model, the
    // router answers `plan-override … effort=None` where the unpicked slot answers
    // `plan-pinned … effort=low`. True since #282 put an effort on the decision; #295 is what made
    // it visible, because until then the pick had no level of its own to show either.
    //
    // One rule rather than a case per shape. The collapse that empties `override` is about the
    // MODEL being the slot's, and says nothing whatever about the level.
    const livePick = overridable && buildModel ? buildModel : '';
    // The level standing on the live pick that its alias will not take, WHATEVER shape the pick is.
    // `collapsedStranded` below narrows it to the collapsed case; this one does not, because the two
    // readers want different scopes and gating once for both is how the override half went unheard.
    const strandedNow = livePick ? strandedLevel(livePick) : '';
    const collapsedStranded = !override ? strandedNow : '';
    // The FACT, with no exit in it. `buildPick` rather than `pinnedModel`, so it names the model on
    // a real override too — that is the half the collapse gate silently excluded.
    // "Turns run", not "this turn runs": the same sentence reaches the RUNNING chip and the idle
    // dropdown, and idle there is no turn for a present-tense claim to be about. On the running chip
    // it would be wrong the other way anyway — a table that narrowed mid-flight leaves the in-flight
    // turn having already sent the level.
    const strandedFact = strandedNow
      ? `${override || pinnedModel} doesn't accept ${effortLabel(strandedNow)}. `
        + 'Choose Model default or a supported setting before the next turn.'
      : '';
    // The fact PLUS the way out, for the open menu only. Clearing the pick is a real instruction
    // there and a false one on the running chip, which draws a disabled Button with no menu behind
    // it — offering an action nobody can take and then saying to wait before changing anything, in
    // consecutive sentences. Only the collapsed case needs it: on a real override the submenu marks
    // the level itself, which is a better account than a sentence.
    //
    // And the instruction carries no DIRECTION. It used to say "the row below", which is wrong
    // twice: the dropdown opens `topRight` so the menu renders ABOVE the chip the tooltip hangs off,
    // and the way-back row is `slotModels[0]` in Plan but `slotModels[1]` in Implement. A reader
    // following it found the mode pill.
    const strandedWhy = strandedNow
      ? (collapsedStranded
        ? `${strandedFact} Clear the pick to put the mode back on its assignment.`
        // A real override keeps the FACT and loses only the row pointer, which belongs to the
        // collapsed case. Withholding it entirely left the idle chip strictly less informative than
        // the running one for the same state: the label drops the level because it will not run,
        // and the only account was a disabled row two hovers into a submenu.
        : strandedFact)
      : '';
    const buildChipLabel = override
      ? (pickedLevel ? `${override} · ${effortLabel(pickedLevel)}` : override)
      : livePick
      ? (pickedLevel ? `${pinnedModel} · ${effortLabel(pickedLevel)}` : pinnedModel)
      : `${pinnedModel} (default)`;
    const buildPick = override || pinnedModel;
    const buildBarred = barredModel(buildPick);
    const buildLabel = SW.util.lockedLabel(sensitivity, buildPick, '', false);
    // Auto has no model of its own — it runs the Plan assignment while it plans and the Implement
    // assignment while it builds — so a bare id here changes under the person with nothing to say
    // why. The phase is the missing half of that sentence.
    const chipLabel = (name) => (chipModeId === 'auto'
      ? `${name} · ${buildPhase === 'implement' ? 'building' : 'planning'}`
      : name);
    // The way through to the assignments, from the menu that can only make an override. The two do
    // different things and say so: an override is this Builder's, until it restarts; an assignment
    // is the Project's (ADR-0017).
    const ASSIGNMENTS_KEY = '__assignments__';
    // A model row grows a submenu of the levels that alias advertises, so the level is chosen under
    // the model it belongs to and both arrive as one act (ADR-0049). Same `length > 0` rule the
    // Chat chip one bar over uses to decide whether to draw anything at all: a model that
    // advertises no levels stays a plain row, which is every row on a deployment nobody has probed.
    //
    // Never on the pinned row. Picking that one CLEARS the override, and what the slot then runs at
    // is its ASSIGNMENT's effort — the Project's standing choice, which belongs to the drawer and
    // not to a control that forgets itself on restart (ADR-0017). Never on a barred row either: it
    // cannot be picked at all, so a submenu under it would be a door into a wall.
    const withEfforts = (row, id) => {
      const levels = effortsFor(id);
      if (!row.disabled) {
        row = { ...row, title: [row.title, effortNoteFor(id)].filter(Boolean).join(' ') || undefined };
      }
      // A level standing against this row's model that the model will not take. Reachable without
      // anyone having done anything wrong: a deployment default can move under a live pick, and the
      // measured table can narrow when an alias is probed (#280). Dropped from the menu, the level
      // would be invisible, still standing, and clearable only by giving up the model too.
      //
      // The answer is `model-assignments.js`'s, taken verbatim down to the label — that is where it
      // was decided, for the drawer's half of this same setting, and this is the second control on
      // it. Two controls answering one stranded level differently is the drift that costs; whoever
      // changes one of these changes both.
      const stranded = strandedLevel(id);
      if ((!levels.length && !stranded) || row.disabled || row.key === PINNED_KEY) return row;
      return {
        ...row,
        children: [
          // First, and not buried under the levels: running the alias at its own default is what
          // every Build pick did before this submenu existed, so it stays the easiest thing to ask
          // for rather than becoming the thing you have to know to look for. It is also the way out
          // of a stranded level, which is why it is here even where `levels` is empty.
          { key: effortKey(id, null), label: effortLabel(null) },
          ...levels.map((value) => ({ key: effortKey(id, value), label: effortLabel(value) })),
          // Disabled for the reason a barred model is disabled one level up: it is not a thing that
          // can be chosen, and the way out is the row above it. Drawn at all so the level the
          // session is standing on has somewhere to be read.
          ...(stranded
            ? [{
                key: effortKey(id, stranded),
                disabled: true,
                label: `${effortLabel(stranded)} — not accepted`,
                title: `${id} doesn't accept this level. Pick another, or go back to the model `
                  + 'default.',
              }]
            : []),
        ],
      };
    };
    const buildItems = [
        // The pinned row is never barred, even when the slot behind it holds an unapproved model.
        // It carries no model id — picking it CLEARS the override — so disabling it would strand
        // somebody on the override they are trying to leave, and it is the one row here that cannot
        // make things worse. What the slot actually resolves to under the lock is the router's
        // answer and not this menu's, and the notice under the box is where that is said.
        ...slotModels.map((id) => withEfforts({
          key: id === pinnedModel ? PINNED_KEY : id,
          disabled: id !== pinnedModel && barredModel(id),
          // `title` and not a wrapped element, so the label stays the plain string every reader of
          // this menu already expects — the menu is drawn headless in a test that reads it as JSON.
          //
          // The capability note is LAST of the two reasons a row can carry a tooltip, because the
          // lock is a reason the row cannot be used and this is a note about a row that can (#463).
          // It never reaches `disabled` for the same reason: this metadata has been measured wrong
          // in both directions, and a Build menu that refused a model on it would refuse one that
          // works. Build earns the mark more than Chat does — every Build turn carries tools.
          title: id !== pinnedModel && barredModel(id) ? lockNote(id)
            : (capabilityFor(id) || undefined),
          label: id === pinnedModel
            ? `${id} (default)`
            : barredModel(id)
            ? `${id} — not allowed`
            : capabilityFor(id)
            ? `${id} — no tool support advertised`
            : id,
        }, id)),
        ...(extraModels.length
          ? [{
              type: 'group',
              label: 'Open-weight',
              children: extraModels.map((o) => withEfforts({
                key: o.id,
                disabled: barredModel(o.id),
                // Silent in practice rather than by a rule of its own: an open-weight option is an
                // id out of `/healthz` with no Alias row behind it, so `capabilityFor` finds no
                // capability list and says nothing. That is the right answer — no evidence is not
                // a fault — and it is the same expression as the rows above so the day one of
                // these does gain a row, it is marked without anybody remembering this line.
                title: barredModel(o.id) ? lockNote(o.id) : (capabilityFor(o.id) || undefined),
                label: barredModel(o.id)
                  ? `${o.id} — not allowed`
                  : capabilityFor(o.id)
                  ? `${o.id} (${o.provider}) — no tool support advertised`
                  : `${o.id} (${o.provider})`,
              }, o.id)),
            }]
          : []),
        { type: 'divider' },
        { key: ASSIGNMENTS_KEY, label: 'Model assignments…' },
    ];
    // What the menu marks, read off the rows that were actually built rather than by restating the
    // rule that decided whether each one has a submenu. A row with a submenu is selected by its
    // CHILD, and a second copy of the "does this row have one" condition could only disagree with
    // the first — about the one row the session is running.
    const overrideRow = override
      ? buildItems.flatMap((i) => (i.type === 'group' ? i.children : [i]))
          .find((i) => i.key === override)
      : null;
    const selectedPick = !override
      ? PINNED_KEY
      : (overrideRow && overrideRow.children ? effortKey(override, buildEffort) : override);
    const buildModelMenu = {
      selectedKeys: [selectedPick],
      items: buildItems,
      onClick: ({ key }) => {
        if (key === ASSIGNMENTS_KEY) return SW.store.openAssignments(true);
        // The way back carries no level of its own: it clears the override, and the slot's
        // ASSIGNMENT effort is what applies from the next turn on (ADR-0049's last row).
        if (key === PINNED_KEY) return SW.store.setBuildModel(null, null);
        // A row with a submenu never fires this with its own key — Ant opens the submenu instead —
        // so the model here arrives either bare, from a row that advertises no levels, or paired
        // with the level the person clicked under it.
        //
        // Told apart by LOOKING, not by parsing. A bare key is a row of its own, and an alias id
        // containing the separator would otherwise be split inside its own name and sent as a
        // truncated model at an invented level. The rows were built three lines up; asking them
        // costs nothing.
        //
        // The residual, stated rather than implied: this makes the BARE direction safe, not the key
        // space itself. An alias literally named `a::low`, drawn beside an alias `a` that offers
        // `low`, produces one key for two rows — and no reader of a flat key space can separate
        // those, this one or Ant's own `selectedKeys`. That is a property of menu keys, not of this
        // parse, and the honest thing is to say so rather than to claim a guarantee one lookup
        // cannot give.
        const bare = buildItems.some((i) => (i.type === 'group'
          ? i.children.some((c) => c.key === key)
          : i.key === key));
        const [id, level] = bare ? [key, null] : splitEffortKey(key);
        return SW.store.setBuildModel(id, level);
      },
    };

    const modelMenu = {
      selectedKeys: effectiveModel ? [effectiveModel] : [],
      items: aliases.map((option) => {
        const barred = barredModel(option.alias);
        // The server's sentence about what this model never claimed it could do, carried on the row
        // rather than re-derived from `option.capabilities` here (#463). One rule, one place: the
        // same argument `SW.util.chatCapable` makes for the other question asked of that list, and
        // the drawer renders the same field, so the two controls cannot come to disagree.
        //
        // It marks and never disables. The capability list has been measured wrong in both
        // directions on this gateway, and hiding or closing a row would take away the only way
        // anybody could find that out (#296).
        const capability = !barred && option.capability_note;
        return {
          key: option.alias,
          disabled: barred,
          title: barred ? lockNote(option.alias)
            : [capability, effortNoteFor(option.alias)].filter(Boolean).join(' ') || undefined,
          label: h(
            'div',
            { style: { minWidth: 200 } },
            h('div', { className: 'sw-model-option-name' }, option.name || option.alias),
            h(
              'div',
              { className: 'sw-model-option-detail' },
              barred ? `${option.alias} — not allowed`
                : capability ? `${option.alias} — no tool support advertised`
                : option.alias
            )
          ),
        };
      }),
      onClick: ({ key }) => {
        const next = aliases.find((a) => a.alias === key);
        const keep = next && (next.reasoning_efforts_with_tools || []).includes(reasoningEffort)
          ? reasoningEffort
          : null;
        SW.store.setChatModel(key, keep);
      },
    };

    const strandedChatEffort = reasoningEffort && aliasRow(effectiveModel)
      && aliasRow(effectiveModel).reasoning_efforts_with_tools
      && !efforts.includes(reasoningEffort) ? reasoningEffort : null;
    const effortMenu = {
      selectedKeys: [strandedChatEffort ? '__stranded__' : (reasoningEffort || 'default')],
      items: [
        { key: 'default', label: effortLabel(null) },
        ...efforts.map((value) => ({ key: value, label: effortLabel(value) })),
        ...(strandedChatEffort ? [{ key: '__stranded__',
          label: `${effortLabel(strandedChatEffort)} — not accepted`, disabled: true,
          title: `${effectiveModel} doesn't accept this level beside tools. Choose Model default or another level.`,
        }] : []),
      ],
      onClick: ({ key }) => {
        // A level rides beside the alias it was chosen under (ADR-0049), which is the alias the
        // rows above were built from — `effectiveModel`, not `model`. With no explicit Chat pick
        // `model` is `''`, and the server reads an empty model as "clear the pick" and drops the
        // level with it, answering 200 (#487). So the pick pins the alias the chip already shows,
        // the way a Build pick always carries its model. Model default with nothing pinned stays
        // unpinned: that is the one click that means "follow the slot".
        if (key === 'default') return SW.store.setChatModel(model, null);
        SW.store.setChatModel(effectiveModel, key);
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

    // What this composer WOULD run if the lock were not on: Build's override or pinned slot (the
    // pin has already been through the signing rule, so it is the model the router starts from),
    // Chat's picked Alias. Empty unless the lock really moved it — an approved pick is not a switch
    // and has nothing to announce.
    // The SELECTOR's mode, not the running turn's: this is what the composer would run NEXT, which
    // is the question the notice answers. Mid-turn the two differ, and reading the turn's here
    // announced a lock switch for the slot the running turn used — or, with a barred pick standing
    // and the selector on Auto, named a pick Auto will never honour.
    const selectedRuns = runsFor(activeBuildMode.id);
    const pickedModel = showMode
      ? (selectedRuns.override || selectedRuns.model)
      : effectiveModel;
    const movedFrom = pickedModel && barredModel(pickedModel) ? pickedModel : '';
    // What "once" is counted against: what the notice SAYS, which is the approved set, the model
    // it moved to, and which of the two reasons is holding the lock. None of them alone. The set
    // catches an administrator adding or removing a member; the model catches them REORDERING the
    // group, where the set is unchanged and the session moves anyway (`nearest_approved` prefers by
    // that order, ADR-0043); the reason catches the creator unbinding the Dataset, after which the
    // notice says something it has never said — that unbinding did not work, and what does. Either
    // way the notice already read was never an answer to the new fact, so it comes back.
    //
    // The Datasets and the app are in it since the notice began naming them (#264): the sentence
    // now changes when a second Dataset is declared ("the Dataset claims" becomes "the Datasets
    // claims and members", and "Remove it" becomes "Remove them") and when the selection moves to
    // another app whose lock happens to match on every other field. A dismissal that outlived
    // either would hide a sentence naming a different row in a different list.
    const noticeKey = String([(sensitivity && sensitivity.approved) || [],
                             SW.util.lockedRunsOn(sensitivity, !showMode),
                             (sensitivity && sensitivity.reason) || '',
                             (sensitivity && sensitivity.datasets) || [],
                             // Both halves of the app: the id because two Built Apps can be renamed
                             // to one name and their dependency lists are still different lists,
                             // and the name because it is the word the sentence prints, so a rename
                             // alone changes what the reader is being told.
                             (activeApp && activeApp.id) || '',
                             (activeApp && activeApp.name) || '']);

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
                  // The press and the row coming down are a round trip apart, so without this the
                  // button says nothing happened and a second click looks like the thing to try
                  // (#385). The store refuses that click either way; this is the half that says why.
                  loading: !!queued.cancelling,
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
                  title: att.pending
                    ? 'Adding to the conversation…'
                    : chipNote(att, att.addedBy === 'sage'
                      ? `${SW.brand.assistant()} added this — ${att.rationale || 'picked for you.'}`
                      : 'You added this to the conversation.'),
                },
                h(
                  Tag,
                  {
                    bordered: true,
                    // No server id to delete yet; the close arrives with the row.
                    closable: !att.pending,
                    closeIcon: h(CloseOutlined, { style: { fontSize: 10 } }),
                    onClose: (e) => {
                      e.preventDefault();
                      // A failed removal leaves the chip on screen, so silence reads as a dead
                      // close button.
                      SW.store.removeFromConversation(att).catch(sayFailed);
                    },
                    // Muted where the selected app does not hold it: the chip stays legible and
                    // stays closable, because it is still this Conversation's context and still arms
                    // the sensitivity lock — it is the app that is missing something, not the chip.
                    className: att.pending
                      ? 'sw-chip is-pending'
                      : missingChip(att) ? 'sw-chip is-not-in-app' : 'sw-chip',
                  },
                  h('span', null, SW.util.iconFor(att.resourceKind)),
                  att.resourceName,
                  attachMark(att)
                )
              )
            )
          ),

        // What the selected app is missing out of what this Conversation carries, with the one click
        // that moves it (#275). Under the chips it counts and above the box, so it is read in the
        // order it happens: these are the chips, this is the app that holds none of them, here is
        // the button.
        crossing && h(CrossingOffer, { offer: crossing, onCross: cross }),

        // The first chip teaches its own scope (#137): a chip is Session context — this
        // Conversation's only — and the app someone builds declares its own Resources. The same
        // guard that draws the chip row draws the note, so it appears exactly when the first
        // chip does and never over an empty composer. Dismissal is for good.
        //
        // Not beside the offer above, which says the same thing with an act on it: two notes over
        // one chip row, one of them teaching what the other is already fixing.
        attachments.length > 0 && !chipHintDismissed && !crossing &&
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
                  // Domino no longer holds it (ADR-0034). Marked, not withheld: the row stays
                  // selectable and carries its reason at the point of picking, because a refusal
                  // here would be Sage's third and would only pre-empt one the creator gets a step
                  // later, from code that knows more about the failure than this menu does
                  // (ADR-0027). Its own slot rather than the caption ladder below, which is
                  // first-match — a missing file with a folder caption would otherwise be marked in
                  // the rail and nowhere here.
                  SW.util.isMissing(resource)
                    ? h('span', { className: 'sw-mention-missing' }, SW.util.missingMark())
                    : null,
                  caption ? h('span', { className: 'sw-caption' }, caption) : null,
                  attachedIds.has(resource.id)
                    ? h('span', { className: 'sw-incontext-tag' }, 'in this conversation')
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
                // The chip is now the only place the switch is stated once the notice is dismissed,
                // so it carries the reason on hover as well as the name (ADR-0043).
                {
                  size: 'small',
                  // Same alias the menu rows are judged and explained by, so the chip's hover and
                  // the row's hover cannot say different things about one Alias.
                  title: barredModel(effectiveModel) ? lockNote(effectiveModel)
                    : effortNoteFor(effectiveModel) || undefined,
                },
                h(Space, { size: 4 },
                  barredModel(effectiveModel)
                    && h(LockOutlined, { style: { fontSize: 11 } }),
                  modelLabel, h(DownOutlined, { style: { fontSize: 9 } }))
              )
            ),
          !showMode &&
            (efforts.length > 0 || strandedChatEffort) &&
            h(
              Dropdown,
              { menu: effortMenu, trigger: ['click'], placement: 'topLeft' },
              h(
                Button,
                { size: 'small', title: effortNoteFor(effectiveModel) || undefined },
                h(Space, { size: 4 }, strandedChatEffort
                  ? `${effortLabel(strandedChatEffort)} — not accepted` : effortLabel(reasoningEffort),
                  h(DownOutlined, { style: { fontSize: 9 } }))
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
                    // Two facts when the pick is barred, in the order they surprise: why the
                    // chip does not name what was picked, then why the control will not open.
                    //
                    // The same join for the signing pin, and for the same reason (#276): both
                    // sentences are true at once, and the running one used to replace `pinWhy`
                    // outright — so hovering the chip mid-build, which is the moment somebody
                    // looks, gave no account of the pin at all. The lock still outranks the pin
                    // here, exactly as it does below: under the lock the pin did not move this
                    // model, and a sentence naming the wrong cause is worse than no sentence.
                    // And a STRANDED level joins too, because the running chip is the one place it
                    // can have no other surface at all: this branch draws a disabled Button rather
                    // than the Dropdown, so the submenu the comments keep calling "where a stranded
                    // level is read" is unreachable, and the label drops the level because it will
                    // not run. Reachable mid-turn exactly as `withEfforts` describes — the measured
                    // table narrows under a live pick (#280) — and without this the chip quietly
                    // loses `· High` and says only which model is running.
                    title: [
                      buildBarred ? lockNote(buildPick) : pinWhy,
                      buildBarred ? '' : strandedFact,
                      `This turn is running on ${buildLabel}. Wait for it to finish to change the model.`,
                    ].filter(Boolean).join(' '),
                  },
                  // The span is load-bearing: a browser dispatches no mouse events on a disabled
                  // button, so a Tooltip put straight on one never opens and the sentence above
                  // becomes the silence it was written to prevent.
                  h('span', { style: { display: 'inline-block' } },
                    h(Button, { size: 'small', disabled: true, 'aria-label': 'Build model' },
                      // The level rides on the RUNNING chip too. This is the one screen state where
                      // the turn is demonstrably running AT that level, so dropping the receipt here
                      // is the "a setting with no receipt reads as a setting that was dropped"
                      // failure the open chip cites as its own reason to exist, reintroduced exactly
                      // where it is least true. Not under the lock: there `buildLabel` is the model
                      // the lock moved the turn ONTO, and a level picked for the model it moved off
                      // is not what that turn runs at.
                      chipLabel(!buildBarred && pickedLevel
                        ? `${buildLabel} · ${effortLabel(pickedLevel)}`
                        : buildLabel)))
                )
              : overridable
                ? (() => {
                    const control = h(
                      Dropdown,
                      { menu: buildModelMenu, trigger: ['click'], placement: 'topRight' },
                      h(
                        Button,
                        { size: 'small', 'aria-label': 'Build model' },
                        // "(default)" is a claim about what the slot falls back to, and under the
                        // lock it falls back nowhere near there. So the lock's answer replaces the
                        // whole construction rather than being appended to it.
                        h(Space, { size: 4 },
                          buildBarred ? buildLabel : buildChipLabel,
                          h(DownOutlined, { style: { fontSize: 9 } }))
                      )
                    );
                    // Wrapped only when there IS something to say. Plan and Implement have never
                    // carried a tooltip, and the menu still works — an in-session pick outranks the
                    // pin, which is the router's own rule (ADR-0032). So this explains the label
                    // without taking the control away.
                    //
                    // The lock first: under it the signing pin is not what moved this model, and a
                    // sentence naming the wrong cause is worse than no sentence.
                    //
                    // The stranded sentence comes last of the three, behind the lock and the pin,
                    // for the same reason they are ordered: each outranks it as a cause, and a
                    // confident sentence naming the wrong one is worse than one fewer sentence. It
                    // is the only account of a level the collapsed row cannot draw — the menu shows
                    // no submenu on the way-back row (#310), so without this the person is told
                    // nothing at all about a level they set and the turn is not running.
                    // The pin and a stranded level are about DIFFERENT things — which model runs,
                    // and which level it runs at — so they join rather than one winning. Ordered
                    // pin-first because it is the bigger fact, and joined for the reason #276 joined
                    // the lock and the running turn: both are true at once, and letting one replace
                    // the other left the moment somebody looks with no account of the second at all.
                    // The LOCK still wins outright: under it the pin did not move this model and the
                    // level was never sent, so both sentences would name wrong causes.
                    // The accepted-level twin of the stranded sentence. Both are the collapse — a
                    // pick naming the mode's own model — and in both the menu marks the way-back row
                    // and offers no submenu (#310), so the tooltip is the only place either can be
                    // accounted for. Mutually exclusive with `strandedWhy`: `pickedLevel` is empty
                    // whenever a level is stranded.
                    //
                    // Gated on the assignment ACTUALLY differing, which the composer can check and
                    // did not: `catalog` carries `<slot>_effort` beside `<slot>`, from the same
                    // payload `pinnedModel` is read out of. Pick the assignment's own level and the
                    // ungated sentence asserts the turn is not running at it while it runs at
                    // exactly that — a confident, specific falsehood reached through the one door
                    // the paragraphs above do not cover, because it is a claim about the ASSIGNMENT
                    // rather than about the pick.
                    // The slot whose EFFORT the turn would run at with no pick, which is not
                    // always `pinnedSlot`. `_pin_signing` early-returns the mode slot's decision
                    // UNMODIFIED when the signing slot names the same model — so the pin supplies
                    // the effort only where it actually MOVES the model. Reading the signing slot's
                    // either way compares against a level the router would not have used, and then
                    // the sentence below names a difference that does not exist (or hides one that
                    // does). Restated here because the picker restates the router's precedence, and
                    // this is the half of the pin rule the server's `signing_slot` cannot express.
                    const modeSlot = chipModeId === 'ask' ? 'ask'
                      : chipModeId === 'auto' ? (buildPhase === 'implement' ? 'implement' : 'plan')
                      : chipModeId;
                    const pinMoves = Boolean(signingSlot && catalog
                      && catalog[signingSlot] !== catalog[modeSlot]);
                    const effortSlot = pinMoves ? signingSlot : modeSlot;
                    // Narrowed the SAME way the pick's level is, because the two are about to be
                    // compared. The pick goes through `effortsFor` before it may appear; the
                    // assignment was read raw off the catalog. Save validation now uses the same
                    // tool-compatible list, but legacy stored levels can still be stranded (#298).
                    //
                    // `plan_effort="high"` on `gpt-5.4` is exactly that: the send path drops it, so
                    // the unpicked slot and a pick carrying no level BOTH run at the model default —
                    // and comparing a narrowed value against an un-narrowed one reported a
                    // difference that does not exist. Same falsehood cf75c76 closed, reached through
                    // the assignment half rather than the pick half.
                    //
                    // No evidence still PERMITS: with no alias row, or a row not carrying the narrow
                    // list, the level stands rather than being narrowed away.
                    const pinnedEffortSaved = (catalog && catalog[`${effortSlot}_effort`]) || null;
                    const pinnedEffortRow = aliasRow((catalog && catalog[effortSlot]) || '');
                    const pinnedEffort = pinnedEffortSaved && pinnedEffortRow
                      && pinnedEffortRow.reasoning_efforts_with_tools
                      && !pinnedEffortRow.reasoning_efforts_with_tools.includes(pinnedEffortSaved)
                      ? null
                      : pinnedEffortSaved;
                    // Compared as EFFECTIVE levels, not on whether a level was chosen. A collapsed
                    // pick that names NO level still overrides — the router answers
                    // `plan-override … effort=None` where the slot would have answered its assigned
                    // level — and that is the commoner shape of all: pick a model, leave levels
                    // alone. Gating on `pickedLevel` covered only the half where somebody had
                    // touched a level, which is the rarer one.
                    //
                    // Not while stranded: that has its own sentence, and both would name the same
                    // gap twice with different causes.
                    const levelWhy = !override && livePick && !collapsedStranded
                      && (pickedLevel || null) !== pinnedEffort
                      ? `This pick runs ${pinnedModel} at ${effortLabel(pickedLevel || null)}, not `
                        + `at the assignment's ${effortLabel(pinnedEffort)}. Clear the pick to `
                        + "use the assignment's."
                      : '';
                    // All three joined rather than any of them winning. Behind a `||` the pin
                    // suppressed whichever level sentence applied — and it was the COMMONER one,
                    // the accepted level, that lost its only surface, which is the asymmetry the
                    // stranded join was added to end rather than to move one case along.
                    const why = buildBarred ? lockNote(buildPick)
                      : [pinWhy, strandedWhy, levelWhy].filter(Boolean).join(' ');
                    return why ? h(Tooltip, { title: why }, control) : control;
                  })()
                // Ask and Auto honour no override — Ask is pinned to its slot and Auto follows the
                // phase — so there is no menu to offer. They open the panel instead: a disabled
                // control with a working door behind it answers "why can't I change this" with
                // nothing, which is what this button used to be.
                : h(
                    Tooltip,
                    {
                      // The lock first, then `pinWhy`: the Auto sentence below names two models,
                      // and under either of those there is only one. A confident, specific, false
                      // sentence is the worst thing this control can say.
                      title: buildBarred
                        ? lockNote(buildPick)
                        : (pinWhy || (activeBuildMode.id === 'ask'
                          ? `Ask runs on ${pinnedModel}, and so does Chat.`
                          : `Auto runs ${(catalog || {}).plan} to plan and ${(catalog || {}).implement} to build.`)),
                    },
                    h(
                      Button,
                      {
                        size: 'small',
                        'aria-label': 'Build model',
                        onClick: () => SW.store.openAssignments(true),
                      },
                      h(Space, { size: 4 }, chipLabel(buildLabel),
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
        h(MentionGuard, {
          entries: unusable, activeAppId: activeApp && activeApp.id, onSend: send,
        }),

      // The switch, said once. `movedFrom` is the model this composer WOULD have run — Build's
      // pinned slot, Chat's picked Alias — and its being barred is the whole condition: an approved
      // pick is not a switch and has nothing to announce. Nothing is drawn while the approved set
      // resolves to nothing either, because that is a refusal rather than a move, and the turn
      // carries its own sentence for it.
      lockedHere && movedFrom && (sensitivity.approved || []).length > 0 &&
        !(sensitivityNoticeFor || []).includes(noticeKey) &&
        h(LockNotice, {
          sensitivity,
          picked: movedFrom,
          chat: !showMode,
          // The app whose dependency list can lift this lock — the same `activeApp` the mention
          // guard beside it acts on, because the lock's scope is the selected app's (ADR-0043).
          //
          // Passed in BOTH modes. Whether the app's list can release the lock is a question about
          // the records and `appHoldsEveryDeclaredDataset` asks it of them; the mode cannot answer
          // it, since a chip arms the lock from the Conversation and the Conversation travels into
          // Build through the handoff. Chat gets the pointer with the mode named in it, because a
          // pointer exists to send a reader somewhere they are not standing — see `lockWayOut`.
          app: activeApp && activeApp.name,
          onDismiss: () => SW.store.dismissSensitivityNotice(noticeKey),
        })
    );
  };
})();
