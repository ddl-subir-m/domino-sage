window.SW = window.SW || {};

(function () {
  // Until /project answers, the chip has nothing real to name. Not a sandbox: scratch is not
  // offered as a Project any more (ADR-0004) — every Thread lives in a git-backed sage-* Project.
  const NO_SCOPE = {
    id: '',
    name: 'Default',
    color: '#543FDE',
    appCount: 0,
    planCount: 0,
    memberCount: 1,
  };

  const state = {
    ready: false,
    me: null,
    brand: {
      productName: 'AI Workbench',
      assistantName: 'Sage',
      pageTitle: 'Sage Workspace',
      logoUrl: './img/domino-logo.svg',
      logoAlt: 'Domino',
      colors: {
        primary: '#543FDE',
        primaryDark: '#311EAE',
        primaryLight: '#EEEBFC',
      },
    },
    projects: [],
    // Whether this container can create or attach Projects at all (Domino + a git host).
    // False on a laptop run, where New project has nothing to create against.
    canProvision: false,
    scope: NO_SCOPE,
    scopeFlash: false,

    // Dock
    dockTab: null,        // null = collapsed
    panelFilter: null,    // resource kind the assistant asked the user to pick
    railHidden: false,
    railAppFilter: null,  // show only conversations that changed this app
    previewResourceId: null,

    // The catalogue picker. Browsing the platform is a deliberate, occasional
    // act, so it gets a surface you open and close rather than a column you
    // live beside.
    catalogOpen: false,
    catalogKind: null,

    // Overlays
    handoffOpen: false,
    handoffDraft: null,
    graduationOpen: false,
    inviteOpen: false,
    paletteOpen: false,
    scopePickerOpen: false,
    helpOpen: false,

    // Project-scoped data
    resourceGroups: {},
    resourceErrors: {},
    resourceIndex: {},
    // Every Dataset this container has mounted writable, whether or not it is in the project rail.
    // Membership is a curated list; the promote target is a fact about the disk.
    datasetTargets: [],
    gatewayAliases: [],
    resourcesLoading: true,
    members: [],
    directory: [],
    userIndex: {},
    activity: [],
    charts: {},
    starters: null,
    notifications: [],

    // Chat
    threads: [],
    thread: null,
    messages: [],
    typing: null,
    // A turn is running somewhere in this project. Not "in this conversation": one project runs
    // one turn at a time (the server's turn lock), so a Chat turn, a Build turn and a turn another
    // tab started are all the same fact here — the fact that decides whether Chat can send, and
    // whether it offers Stop. Reading it per-conversation is what let the composer take a question
    // it already knew the server would refuse.
    chatRunning: false,
    // The conversation whose Chat turn THIS tab is streaming, or null. Not the same question as
    // `chatRunning`: a turn started in another tab, or before a reload, is running with nobody here
    // reading it. Knowing which lets the composer say whether the turn holding it up is the one on
    // screen — "wait" and "wait, over there" send someone to different places.
    chatTurnThread: null,
    pendingTurn: null,
    scriptMeta: { planTemplate: 'tpl_generic' },
    assistantTurns: 0,
    nudgeDismissed: false,

    // Context has an owner. `attachments` is this conversation's — disposable,
    // and nobody else's, rendered as chips in the composer and nowhere else.
    // `requires` is the previewed app's — durable, part of the artifact, and
    // shown as a badge on the project rows it points at.
    attachments: [],
    requires: [],
    // The Built Apps in this Project, oldest first, and the one Build is pointed at. The Build
    // rail's list, where Chat's is `threads` — a Project holds many of each and neither is the
    // other (ADR-0008).
    apps: [],
    activeApp: null,
    activePlanId: null,
    activePlan: null,
    // plan.md, for the rail's pin. Not the plan document above.
    projectPlan: null,

    // Apps this conversation changed, newest first. Drives the change dots on
    // the app selector and the tags in the rail.
    touched: [],

    // Artifacts — plans and outputs the conversation produced. The plan viewer
    // opens beside the work rather than taking over the main area.
    planViewerId: null,

    // Composer
    model: '',
    catalogAsk: '',
    reasoningEffort: null,
    phase: 'planning',
    // Build agent mode (Auto / Ask / Plan / Implement). Distinct from the
    // prototype model picker above. `buildMode` is the picker's standing
    // choice; `buildTurnMode` is what the in-flight turn is pinned to.
    buildMode: 'auto',
    buildTurnMode: 'auto',

    // Build is the project's history.jsonl, not the Chat Thread. Chat ↔ Build
    // is turning your head: the Thread stays selected, this transcript is the app's.
    buildHistory: [],
    buildMessages: [],
    buildTyping: null,
    buildRunning: false,
    bindings: [],
    previewSrc: './preview/',
    previewStatus: 'idle',
  };

  const listeners = new Set();
  function notify() {
    listeners.forEach((fn) => fn(state));
  }

  // A conversation owns its context, always — before a plan exists, before an
  // app exists. There is no fallback to guess an owner from, which is what let
  // one conversation report different context in Chat and Build.
  function conversationId() {
    return (state.thread && state.thread.id) || null;
  }

  function applyModelStatus(status) {
    const m = (status && status.model) || status;
    if (!m) return;
    state.buildMode = m.selected_mode || m.mode || state.buildMode;
    state.buildTurnMode = m.mode || state.buildTurnMode;
    if (m.catalog && m.catalog.ask) state.catalogAsk = m.catalog.ask;
    if ('chat_model' in m || m.chat_model === null) {
      state.model = m.chat_model || '';
    }
    if ('reasoning_effort' in m) state.reasoningEffort = m.reasoning_effort || null;
  }

  function indexResources(groups) {
    const index = {};
    Object.values(groups || {}).forEach((list) => list.forEach((r) => { index[r.id] = r; }));
    return index;
  }

  let scopeLoad = 0;

  function applyResourceGroups(groups, extras = {}) {
    state.resourceGroups = groups;
    state.resourceIndex = indexResources(groups);
    if ('aliases' in extras) state.gatewayAliases = extras.aliases || [];
    if ('errors' in extras) state.resourceErrors = extras.errors || {};
  }

  function applyBrandChrome(brand) {
    if (!brand) return;
    if (brand.pageTitle) document.title = brand.pageTitle;
    const colors = brand.colors || {};
    const root = document.documentElement.style;
    if (colors.primaryDark) {
      root.setProperty('--purple-700', colors.primaryDark);
      root.setProperty('--purple-600', colors.primaryDark);
    }
    if (colors.primary) {
      root.setProperty('--purple-500', colors.primary);
      root.setProperty('--accent-1', colors.primary);
    }
    if (colors.primaryLight) {
      root.setProperty('--purple-100', colors.primaryLight);
    }
  }

  async function loadScopeData() {
    const scope = state.scope;
    const gen = ++scopeLoad;

    const [resources, activity] = await Promise.all([
      SW.api.resources(scope.id),
      SW.api.activity(scope.id),
    ]);
    if (gen !== scopeLoad) return;
    applyResourceGroups(resources.groups, { aliases: resources.aliases, errors: {} });
    state.resourcesLoading = false;
    state.activity = activity;
    notify();

    Promise.all([
      SW.api.project().catch(() => ({ attached: [] })),
      SW.api.resourceListing(),
    ]).then(([project, listing]) => {
      if (gen !== scopeLoad) return;
      const files = [
        ...(project.scratch || []).map((e) => ({
          id: `file:${e.path}`,
          name: e.name || (e.path || '').split('/').pop(),
          kind: 'file',
          path: e.path,
          source: 'scratch',
        })),
        ...(project.attached || [])
          .filter((e) => !SW.util.isHiddenFromExplorer(e.path))
          .map((e) => ({
            id: `file:${e.path}`,
            name: (e.path || '').split('/').pop(),
            kind: 'file',
            path: e.path,
          })),
      ];
      applyResourceGroups(
        SW.api.overlayResourceListing({ ...state.resourceGroups, file: files }, listing),
        {
          aliases: (listing.groups && listing.groups.model_llm) || [],
          errors: listing.errors || {},
        }
      );
      // The overlay keeps only the Datasets already in the rail. A scratch file can be promoted
      // onto any Dataset this container mounts writable, so that set is kept whole here.
      state.datasetTargets = ((listing.groups && listing.groups.dataset) || []).filter((d) => d.writable);
      notify();
    }).catch(() => {});

    const members = await SW.api.members(scope.id || null);
    if (gen !== scopeLoad) return;
    state.members = members.members;
    state.directory = members.directory;

    // Anything that renders a name or avatar looks the person up here, so
    // author IDs on plans and comments resolve even for non-members.
    state.userIndex = {};
    [...members.directory, ...members.members].forEach((user) => {
      state.userIndex[user.id] = user;
    });
    if (state.me) state.userIndex[state.me.id] = state.me;
    notify();
  }

  async function loadThreadList() {
    state.threads = await SW.api.threads(state.scope.id);
    notify();
  }

  // The app a select is on the way to, or null. Two things ask for the same app before the first
  // answer lands — the route asserts it on mount, and the rail's own load re-renders underneath —
  // and the second request would be refused by the turn lock the first one is holding, which
  // reaches the person as a warning about a build that is not running.
  let selecting = null;

  // The Build rail's list. `activeApp` follows it rather than being set beside it, so the row that
  // is lit and the app the server is pointed at cannot drift apart.
  async function loadAppList() {
    state.apps = await SW.api.apps().catch(() => []);
    state.activeApp = state.apps.find((a) => a.selected) || null;
    notify();
  }

  async function refreshAttachments() {
    const id = conversationId();
    state.attachments = id ? await SW.api.conversationContext(id) : [];
    notify();
  }

  async function refreshRequires() {
    const appId = state.activeApp && state.activeApp.id;
    state.requires = appId ? await SW.api.appRequires(appId) : [];
    notify();
  }

  // ---------------------------------------------------------------------
  // Scripted assistant delivery
  // ---------------------------------------------------------------------

  function fillTemplate(blocks, values) {
    return JSON.parse(
      JSON.stringify(blocks).replace(/\{\{(\w+)\}\}/g, (_, key) => values[key] || '')
    );
  }

  function pushMessage(message) {
    state.messages = [...state.messages, message];
    notify();
  }

  function summarise(text) {
    const trimmed = text.trim();
    return trimmed.length > 48 ? `${trimmed.slice(0, 48)}…` : trimmed;
  }

  function fileUrl(path) {
    return `./api/project/file/raw?path=${encodeURIComponent(path)}`;
  }

  async function blocksForArtifacts(items) {
    const blocks = [];
    for (const art of items || []) {
      const path = art.path || '';
      const lower = path.toLowerCase();
      if (art.kind === 'chart' || lower.endsWith('.png')) {
        blocks.push({ type: 'image', title: art.title || art.name, src: fileUrl(path) });
      } else if (art.kind === 'table' || lower.endsWith('.table.json')) {
        try {
          const body = await fetch(`./api/project/file?path=${encodeURIComponent(path)}`).then((r) => r.json());
          const data = JSON.parse(body.content || '{}');
          blocks.push({
            type: 'table',
            title: data.title || art.title,
            columns: data.columns || [],
            rows: data.rows || [],
          });
        } catch (err) {
          blocks.push({ type: 'file', name: art.name || path, path });
        }
      } else if (path) {
        blocks.push({ type: 'file', name: art.name || path, path });
      }
    }
    return blocks;
  }

  async function historyToMessages(history, handoff) {
    const messages = [];
    let assistant = null;
    const ensureAssistant = () => {
      if (!assistant) {
        assistant = { id: `a_${messages.length}`, role: 'assistant', at: new Date().toISOString(), blocks: [] };
        messages.push(assistant);
      }
      return assistant;
    };
    const hideSuggest = handoff && (handoff.suppressed || handoff.status === 'suppressed'
      || handoff.status === 'bound' || handoff.status === 'planned');
    const shownArts = new Set();
    for (const ev of history || []) {
      if (ev.type === 'user') {
        assistant = null;
        messages.push({
          id: `u_${messages.length}`,
          role: 'user',
          at: ev.at,
          blocks: [{ type: 'text', value: ev.text || '' }],
          contextIds: ev.contextIds,
          attachments: attachmentsFromUserEvent(ev),
        });
      } else if (ev.type === 'agent' && ev.kind === 'text' && ev.text) {
        ensureAssistant().blocks.push({ type: 'text', value: ev.text });
      } else if (ev.type === 'agent' && ev.kind === 'tool') {
        continue;
      } else if (ev.type === 'artifacts' || (ev.type === 'done' && ev.artifacts && ev.artifacts.length)) {
        const items = (ev.items || ev.artifacts || []).filter((a) => {
          const key = a.path || a.id;
          if (!key || shownArts.has(key)) return false;
          shownArts.add(key);
          return true;
        });
        if (items.length) {
          ensureAssistant().blocks.push(...(await blocksForArtifacts(items)));
        }
      } else if (ev.type === 'error' || ev.type === 'stopped') {
        // Why the turn ended, on reload as well as live. The server has always persisted these —
        // the timeout sentence that names what to try instead, and now Stop — and this loop
        // dropped both, so reopening a Thread showed a question with no answer and no reason.
        ensureAssistant().blocks.push({
          type: 'status',
          ok: false,
          value: ev.message || (ev.type === 'stopped' ? 'Stopped.' : 'The turn failed.'),
        });
      } else if (ev.type === 'handoff-suggest' && !hideSuggest) {
        assistant = null;
        messages.push({
          id: `sug_${messages.length}`,
          role: 'system',
          blocks: [{ type: 'plan_suggestion', reason: ev.reason }],
        });
      }
    }
    return withHandoffCallout(messages, handoff);
  }

  function attachmentsFromUserEvent(ev) {
    const snap = ev.context;
    if (!Array.isArray(snap) || !snap.length) return [];
    return snap.map((c) => ({
      resourceId: c.id,
      name: c.name,
      kind: SW.util.uiKind(c.kind),
    }));
  }

  function withHandoffCallout(messages, handoff) {
    if (!handoff || handoff.suppressed || handoff.status !== 'suggested') return messages;
    if (messages.some((m) => (m.blocks || []).some((b) => b.type === 'plan_suggestion'))) return messages;
    return messages.concat([{
      id: 'handoff_suggest',
      role: 'system',
      blocks: [{ type: 'plan_suggestion' }],
    }]);
  }

  async function readSSE(res, onEvent) {
    const reader = res.body.getReader();
    const dec = new TextDecoder();
    let buf = '';
    while (true) {
      const { done, value } = await reader.read();
      if (done) break;
      buf += dec.decode(value, { stream: true });
      const parts = buf.split('\n\n');
      buf = parts.pop();
      for (const part of parts) {
        for (const line of part.split('\n')) {
          if (line.startsWith('data: ')) {
            try { onEvent(JSON.parse(line.slice(6))); } catch (err) { /* keep-alive or partial */ }
          }
        }
      }
    }
  }

  // Decisions whose own card already says what happened and what to do next. A red "Stopped —"
  // line under one of those reads as a failure the user has to fix, when the turn stopped exactly
  // as designed and the thing above it is asking them a question.
  const GATE_DECISIONS = {
    'awaiting approval': true,
    'architecture ready': true,
    'reset offered': true,
  };

  function buildHistoryToMessages(history) {
    const messages = [];
    let assistant = null;
    let pendingPlan = null;
    const ensureAssistant = () => {
      if (!assistant) {
        assistant = { id: `ba_${messages.length}`, role: 'assistant', at: new Date().toISOString(), blocks: [] };
        messages.push(assistant);
      }
      return assistant;
    };
    for (const ev of history || []) {
      if (ev.type === 'user') {
        assistant = null;
        messages.push({
          id: `bu_${messages.length}`,
          role: 'user',
          at: ev.at,
          blocks: [{ type: 'text', value: ev.text || '' }],
        });
      } else if (ev.type === 'agent' && ev.kind === 'text' && ev.text) {
        ensureAssistant().blocks.push({ type: 'text', value: ev.text });
      } else if (ev.type === 'agent' && ev.kind === 'tool') {
        ensureAssistant().blocks.push({
          type: 'sandbox_run',
          label: ev.tool === 'bash' ? 'Ran a command' : `Ran ${ev.tool || 'tool'}`,
          // Absent on turns OpenCode did not time, and on every turn recorded before Sage started
          // reading the clock. The card leaves the duration off rather than inventing one.
          durationMs: ev.durationMs,
          code: ev.detail || '',
        });
      } else if (ev.type === 'plan-proposed') {
        assistant = null;
        if (pendingPlan) pendingPlan.pending = false;
        const block = {
          type: 'build_plan',
          plan: ev.plan || '',
          kind: ev.kind || 'plan',
          // The plan document this turn wrote. Empty for an architecture, which has no document.
          planId: ev.planId || '',
          steps: ev.steps || 0,
          pending: true,
        };
        pendingPlan = block;
        messages.push({
          id: `bp_${messages.length}`,
          role: 'assistant',
          blocks: [block],
        });
      } else if (ev.type === 'plan-stale') {
        if (pendingPlan) pendingPlan.pending = false;
      } else if (ev.type === 'typecheck') {
        ensureAssistant().blocks.push({
          type: 'status',
          ok: ev.ok,
          value: ev.ok ? 'Typecheck passed' : `Typecheck: ${ev.errors} error(s)`,
        });
      } else if (ev.type === 'done') {
        if (!GATE_DECISIONS[ev.decision]) {
          if (pendingPlan) pendingPlan.pending = false;
          ensureAssistant().blocks.push({
            type: 'status',
            ok: ev.ok,
            value: ev.decision === 'answered'
              ? 'Answered'
              : (ev.ok ? 'Done — build is clean' : `Stopped — ${ev.decision}`),
          });
        }
      } else if (ev.type === 'error' && ev.message) {
        ensureAssistant().blocks.push({ type: 'status', ok: false, value: ev.message });
      } else if (ev.type === 'saved') {
        const value = ev.ok
          ? (ev.pushed ? 'Saved and pushed' : `Saved${ev.detail ? ` — ${ev.detail}` : ''}`)
          : `Couldn't save — ${ev.detail || 'git error'}`;
        ensureAssistant().blocks.push({ type: 'status', ok: !!ev.ok, value });
      } else if ((ev.type === 'ask-blocked' || ev.type === 'ask-active') && ev.message) {
        ensureAssistant().blocks.push({ type: 'status', ok: false, value: ev.message });
      } else if (ev.type === 'reset-offer' && ev.message) {
        // `live` is set only on the frame that arrived over SSE this session (see applyBuildEvent),
        // and a reload replaces buildHistory with plain server rows that never carry it. So a
        // replayed offer renders as text with no buttons: an old message must not be able to reset
        // the app on a page load nobody connected it to.
        ensureAssistant().blocks.push({
          type: 'reset_offer',
          message: ev.message,
          prompt: ev.prompt || '',
          live: !!ev.live,
        });
      } else if (ev.type === 'app-reset') {
        ensureAssistant().blocks.push({
          type: 'status',
          ok: true,
          value: 'App reset to the starter template. Your attached files, Resources and this '
            + 'conversation are unchanged.',
        });
      }
    }
    return messages;
  }

  function applyBuildEvent(ev) {
    if (!ev || ev.busy || (ev.type === 'done' && ev.decision === 'busy')) {
      if (ev && (ev.busy || ev.decision === 'busy')) {
        state.buildTyping = 'Another build is still running…';
      }
      return;
    }
    if (ev.type === 'user') return;
    if (ev.type === 'active' || (ev.type === 'agent' && ev.kind === 'tool')) {
      const verb = ev.tool === 'bash' ? 'Running a command' : (ev.detail || ev.tool || 'Working');
      state.buildTyping = verb;
    } else if (ev.type === 'typecheck-start') {
      state.buildTyping = 'Typechecking…';
    } else if (ev.type === 'iterate') {
      state.buildTyping = ev.reason || 'Fixing errors…';
    } else if (ev.type === 'agent' && ev.kind === 'text') {
      state.buildTyping = null;
    } else if (ev.type === 'plan-proposed' || ev.type === 'done' || ev.type === 'error' || ev.type === 'stopped') {
      state.buildTyping = null;
    }
    // A plan appearing and a plan being consumed are the two events the pin exists to show. Fired
    // and not awaited: applyBuildEvent is called once per SSE frame and must not block the stream.
    if (ev.type === 'plan-proposed' || ev.type === 'done') {
      refreshProjectPlan().then(notify, () => {});
    }
    if (ev.type === 'stopped') return;
    if (ev.type === 'active' || ev.type === 'phase' || ev.type === 'typecheck-start' || ev.type === 'iterate') return;
    // The buttons on a reset offer belong to the offer the user is looking at, not to every copy of
    // it the transcript keeps. Marking the live frame is what separates the two — the server row a
    // reload returns has no `live`, so it replays as text (see buildHistoryToMessages).
    if (ev.type === 'reset-offer') ev.live = true;
    state.buildHistory = state.buildHistory.concat([ev]);
    state.buildMessages = buildHistoryToMessages(state.buildHistory);
  }

  async function refreshBindings() {
    const body = await SW.api.bindings().catch(() => ({ bindings: [] }));
    state.bindings = body.bindings || [];
  }

  // The plan the panel pins. Two moments move it and nothing else does: a gate turn or a Chat
  // handoff proposes one, and an approve consumes it (the server archives the plan the moment a
  // build reads it, which is what flips the pin from "Plan" to "Working from").
  //
  // `projectPlan`, not `activePlan`: this is plan.md's `{title, markdown, status, steps}`, and
  // `activePlan` is the plan document `loadPlan` fetches. They used to share a key and only got
  // away with it because nothing ever set `thread.planId`, so `loadPlan` never ran.
  async function refreshProjectPlan() {
    const plan = await SW.api.projectPlan();
    state.projectPlan = plan && plan.markdown ? plan : null;
  }

  async function probePreview() {
    const url = `./preview/?t=${Date.now()}`;
    try {
      const res = await fetch(url, { cache: 'no-store' });
      if (res.ok) {
        state.previewSrc = url;
        state.previewStatus = 'ok';
      } else if (res.status === 502) {
        state.previewStatus = 'starting';
      } else {
        state.previewStatus = 'err';
      }
    } catch (err) {
      state.previewStatus = 'starting';
    }
    notify();
  }

  async function deliverTurn(turn, meta = {}, values = {}) {
    // A turn that waits parks itself until the user actually attaches
    // something. That is what makes the manual path feel like a conversation.
    if (turn.waitsForAttachment) {
      state.pendingTurn = { turn, meta };
      notify();
      return;
    }

    state.typing = turn.thinkingLabel || 'Thinking…';
    notify();
    await SW.util.sleep(turn.delayMs || 700);

    for (const attachSpec of turn.attaches || []) {
      await store.attach(attachSpec.resourceId, attachSpec.addedBy || 'sage', attachSpec.rationale, {
        silent: true,
      });
    }
    for (const resourceId of turn.installs || []) {
      await SW.api.addToProject(state.scope.id, resourceId);
    }
    if ((turn.installs || []).length || (turn.attaches || []).length) {
      await loadScopeData();
    }

    state.typing = null;
    state.assistantTurns += 1;

    const blocks = fillTemplate(turn.blocks, values);
    pushMessage({
      id: `local_${Date.now()}`,
      role: 'assistant',
      at: new Date().toISOString(),
      turnId: turn.id,
      blocks,
    });

    if (state.thread) {
      SW.api.appendMessage(state.thread.id, blocks, 'assistant').catch(() => {});
    }

    if (meta.suggestPlan && !state.activePlanId) {
      pushMessage({ id: `sug_${Date.now()}`, role: 'system', blocks: [{ type: 'plan_suggestion' }] });
      if (state.thread) SW.api.patchThread(state.thread.id, { planSuggested: true }).catch(() => {});
    }

  }

  // Both ways out of this builder — switching Project and creating one — end the same way: a URL
  // in another container, and a wait while its session comes up. One Sage Builder is bound to one
  // project volume, so there is no version of this that stays on the page.
  //
  // The builder being left stays running. Stopping it would have to commit, pull, resolve and push
  // first, and could cut off a build mid-turn; coming back is then a resume instead of a reuse.
  async function handOver({ title, detail, failure, start }) {
    const modal = antd.Modal.info({
      title,
      content: detail,
      okButtonProps: { style: { display: 'none' } },
      closable: false,
      maskClosable: false,
    });
    try {
      const opened = await start();
      const projectId = (opened.project && opened.project.id) || opened.project_id;
      let url = opened.running ? opened.open_url : null;
      // A launched or resumed workspace reports Started while its session is still booting, so
      // going in now would land on a page that isn't ready. ~4 minutes, then say so.
      for (let i = 0; !url && i < 80; i++) {
        await new Promise((r) => setTimeout(r, 3000));
        const s = await SW.api.projectStatus(projectId, opened.workspace_id).catch(() => null);
        if (s && s.running && s.open_url) url = s.open_url;
      }
      if (!url) throw new Error('The workspace is taking longer than expected to start.');
      modal.destroy();
      window.location.replace(url);
      return opened;
    } catch (err) {
      modal.destroy();
      antd.Modal.error({ title: failure, content: String((err && err.message) || err) });
      return null;
    }
  }

  const store = {

    get: () => state,
    getConversationId: conversationId,

    subscribe(fn) {
      listeners.add(fn);
      return () => listeners.delete(fn);
    },

    set(patch) {
      Object.assign(state, patch);
      notify();
    },

    async setBuildMode(mode) {
      if (!['auto', 'ask', 'plan', 'implement'].includes(mode)) return;
      state.buildMode = mode;
      notify();
      try {
        const status = await SW.api.setBuildMode(mode);
        applyModelStatus(status);
        notify();
      } catch (err) {
        antd.message.error(String((err && err.message) || err));
      }
    },

    async setChatModel(alias, effort) {
      state.model = alias || '';
      state.reasoningEffort = effort || null;
      notify();
      try {
        const status = await SW.api.setChatModel(alias, effort);
        applyModelStatus(status);
        notify();
      } catch (err) {
        antd.message.error(String((err && err.message) || err));
      }
    },

    async init() {
      const [me, projects, charts, starters, notifications, brand] = await Promise.all([
        SW.api.me(),
        SW.api.projects(),
        SW.api.charts(),
        SW.api.starters(),
        SW.api.notifications(),
        SW.api.brand().catch(() => state.brand),
      ]);
      state.me = me;
      if (brand) {
        state.brand = brand;
        applyBrandChrome(brand);
      }
      state.projects = projects;
      state.canProvision = !!(projects[0] && projects[0].provisioning);
      state.scope = projects[0] || state.scope;
      applyModelStatus(projects[0]);
      state.charts = charts;
      state.starters = starters;
      state.notifications = notifications;
      state.ready = true;
      state.resourcesLoading = true;
      notify();
      // A reload during a turn lands here with no stream and no memory of one. Ask the lock, so
      // the composer opens disabled with a Stop beside it rather than taking a question the
      // server is about to refuse.
      await Promise.all([loadScopeData(), loadThreadList(), store.refreshTurnState()]);
    },

    // Scope --------------------------------------------------------------

    // Scope survives mode switches. This is the continuity mechanism.
    async setScope(project, options = {}) {
      if (state.scope.id === project.id) return;
      // A conversation that belongs to the project we are moving into stays
      // open. Opening a deep link resolves the app and the conversation at the
      // same time, and whichever settled the scope used to wipe the other.
      const keepThread =
        state.thread && (state.thread.projectId || NO_SCOPE.id) === project.id;

      state.scope = project;
      state.activePlanId = null;
      state.activePlan = null;
      // The apps belong to the Project being left, and a stale list is worse than none: the rail
      // would offer rows that select an app this Builder is not attached to.
      state.apps = [];
      state.activeApp = null;
      state.requires = [];
      state.railAppFilter = null;
      if (!keepThread) {
        state.thread = null;
        state.messages = [];
        state.attachments = [];
        state.touched = [];
        state.assistantTurns = 0;
        state.pendingTurn = null;
      }
      state.scopeFlash = true;
      notify();
      setTimeout(() => { state.scopeFlash = false; notify(); }, 500);

      if (!options.silent) {
        antd.message.info(`Switched scope to ${project.name}`);
      }
      await Promise.all([loadScopeData(), loadThreadList()]);
    },

    // Switching Project means LEAVING this container (#47). One Sage Builder is bound to one
    // project volume, so the viewer's work in another Project lives in their builder there — this
    // attaches it (reuse, resume, or create) and hands the browser over, the same move the door
    // makes. A collaborator's builder in that Project is never taken over.
    async attachProject(project) {
      if (!project || project.current) return;
      await handOver({
        title: `Opening ${project.name}`,
        detail: 'Starting your workspace there. This takes about a minute if it was stopped.',
        failure: `Sage couldn't open ${project.name}`,
        start: () => SW.api.openProject(project.id),
      });
    },

    // New project is a real verb (#46): a private sage-* repo, the template seeded and pushed, a
    // git-based Domino project, then this viewer's builder in it. The name typed here becomes the
    // chip there — it rides into the repo, because the Domino project has to be named sage-<slug>
    // for Sage to find it again.
    async createProject(name) {
      const trimmed = String(name || '').trim();
      if (!trimmed) return null;   // the picker disables Create, so this is only belt-and-braces
      return handOver({
        title: `Creating ${trimmed}`,
        detail: 'Setting up the repository and starting your workspace. This takes about a minute.',
        failure: `Sage couldn't create ${trimmed}`,
        start: () => SW.api.createProject(trimmed),
      });
    },

    // Opening a thread adopts its project, so you never attach a resource
    // from the wrong project to an old conversation.
    async adoptThreadScope(thread) {
      if (!thread.projectId) return;
      const target = state.projects.find((p) => p.id === thread.projectId);
      if (target && target.id !== state.scope.id) {
        await store.setScope(target);
      }
    },

    // Same rule for an app, so a link to one lands in the project that owns it
    // rather than in whatever scope happened to be open.
    async adoptAppScope(app) {
      const target = state.projects.find((p) => p.id === app.projectId);
      if (target && target.id !== state.scope.id) {
        await store.setScope(target, { silent: true });
      }
      return target;
    },

    async reloadProjects() {
      state.projects = await SW.api.projects();
      notify();
    },

    // Dock ---------------------------------------------------------------

    toggleDock(tab = 'resources') {
      state.dockTab = state.dockTab === tab ? null : tab;
      if (!state.dockTab) state.panelFilter = null;
      notify();
    },

    openDock(tab = 'resources') {
      state.dockTab = tab;
      notify();
    },

    toggleRail() {
      state.railHidden = !state.railHidden;
      notify();
    },

    // Called when a script turn has opensPanel. Sage asking you to pick a kind
    // of thing is a browse task, so it opens the catalogue scoped to that kind
    // rather than filtering a panel that may not contain the answer yet.
    focusPanel(kind) {
      state.dockTab = 'resources';
      state.panelFilter = kind;
      state.catalogOpen = true;
      state.catalogKind = kind;
      notify();
    },

    clearPanelFilter() {
      state.panelFilter = null;
      notify();
    },

    openCatalog(kind) {
      state.catalogOpen = true;
      state.catalogKind = kind || null;
      notify();
    },

    closeCatalog() {
      state.catalogOpen = false;
      state.catalogKind = null;
      state.panelFilter = null;
      notify();
    },

    previewResource(resourceId) {
      state.previewResourceId = resourceId;
      notify();
    },

    // Project membership ---------------------------------------------------

    // The only way anything becomes usable here. Everything else — chips,
    // @-mentions, an app's requirements — points at something that already
    // went through this.
    async addToProject(resource, options = {}) {
      const result = await SW.api.addToProject(state.scope.id, resource);
      await loadScopeData();
      if (!options.silent && result.added) {
        antd.message.success(`${resource.name} is now in ${state.scope.name}`);
      }
      return result;
    },

    async removeFromProject(resource) {
      const scopeName = state.scope.name;
      return new Promise((resolve) => {
        antd.Modal.confirm({
          title: `Remove ${resource.name} from ${scopeName}?`,
          content: 'It leaves this project. You can add it again from Browse Domino.',
          okText: 'Remove',
          okButtonProps: { danger: true },
          onOk: async () => {
            try {
              await SW.api.removeFromProject(state.scope.id, resource.id);
            } catch (err) {
              // A Built App still binds it, so the removal is refused. A toast saying so is a
              // dead end — name the apps that still bind it and the source that still uses it,
              // because that is the change the creator has to make first, and the app refusing is
              // often not the one on screen. (Removing the record is not removing the code: an app
              // whose Summarise button calls an Alias it no longer has keeps the button.)
              const apps = (err.payload && err.payload.apps) || [];
              const refs = (err.payload && err.payload.refs) || [];
              if (apps.length) {
                const subject = apps.length > 1 ? 'Built Apps' : 'a Built App';
                const fix = refs.length
                  ? ` Used in: ${refs.join(', ')}. Remove those uses in Build, then remove it here.`
                  : ' Unbind it in Build, then remove it here.';
                antd.Modal.info({
                  title: `${resource.name} is still used by ${subject}`,
                  content: `${err.message}, so it can't leave ${scopeName} yet.` + fix,
                  okText: 'Got it',
                });
              } else {
                antd.message.error(err.message);
              }
              resolve(false);
              return;
            }
            const tid = conversationId();
            const drop = (state.attachments || []).filter(
              (a) => a.resourceId === resource.id || a.parentId === resource.id
            );
            if (tid) {
              await Promise.all(
                drop.map((a) => SW.api.removeFromConversation(tid, a.id).catch(() => null))
              );
            }
            state.attachments = (state.attachments || []).filter(
              (a) => a.resourceId !== resource.id && a.parentId !== resource.id
            );
            await loadScopeData();
            antd.message.info(`${resource.name} is out of ${scopeName}`);
            resolve(true);
          },
          onCancel: () => resolve(false),
        });
      });
    },

    // Conversation context ------------------------------------------------

    async attach(resourceId, addedBy = 'user', rationale, options = {}) {
      // Putting something in context is intent, the same as typing, so it opens
      // a conversation. Only navigation is free.
      if (!conversationId()) await store.newThread({ appId: options.appId });
      const attachment = await SW.api.addToConversation(
        conversationId(),
        resourceId,
        addedBy,
        rationale
      );
      if (!state.attachments.some((a) => a.id === attachment.id)) {
        state.attachments = [...state.attachments, attachment];
      }
      state.panelFilter = null;
      notify();

      // Pointing at something from the catalogue brings it into the project on
      // the way in, so the panel has to hear about its new member.
      if (attachment.joinedProject) {
        await loadScopeData();
        if (!options.silent) {
          antd.message.success(`${attachment.resourceName} added to ${state.scope.name}`);
        }
      }

      // The assistant acknowledges manual picks; this closes the loop between
      // the panel and the conversation.
      if (addedBy === 'user' && !options.silent) {
        store.acknowledgeAttachment(attachment, { quiet: options.quiet });
      }
      return attachment;
    },

    // Out of this conversation's context. The resource stays in the project, and
    // the app keeps needing it — that separation is the whole point of the split.
    async detach(attachment) {
      await SW.api.removeFromConversation(conversationId(), attachment.id);
      state.attachments = state.attachments.filter((a) => a.id !== attachment.id);
      notify();
      const stillNeeded = state.requires.some((r) => r.resourceId === attachment.resourceId);
      antd.message.info(
        stillNeeded
          ? `${attachment.resourceName} is out of this conversation. ${state.activeApp.name} still needs it.`
          : `${attachment.resourceName} is out of context — still in ${state.scope.name}.`
      );
    },

    detachResource(resourceId) {
      const attachment = state.attachments.find((a) => a.resourceId === resourceId);
      if (attachment) return store.detach(attachment);
      return Promise.resolve();
    },

    // App requirements ---------------------------------------------------

    // Promotion changes the artifact, so it is its own act with its own
    // feedback rather than a side effect of working in a conversation.
    async promote(attachment) {
      const app = state.activeApp;
      if (!app) return null;
      const requirement = await SW.api.addRequirement(
        app.id,
        attachment.resourceId,
        attachment.addedBy,
        attachment.rationale
      );
      if (!state.requires.some((r) => r.id === requirement.id)) {
        state.requires = [...state.requires, requirement];
      }
      notify();
      antd.message.success(`${attachment.resourceName} is now part of ${app.name}.`);
      return requirement;
    },

    async demote(requirement) {
      const app = state.activeApp;
      if (!app) return;
      await SW.api.removeRequirement(app.id, requirement.id);
      state.requires = state.requires.filter((r) => r.id !== requirement.id);
      notify();
      antd.message.info(`${app.name} no longer needs ${requirement.resourceName}.`);
    },

    // The panel acts on resources rather than on attachment records, because a
    // row there is a project member first and a dependency second.
    async promoteResource(resource) {
      const app = state.activeApp;
      if (!app) return null;
      const requirement = await SW.api.addRequirement(app.id, resource.id, 'user');
      if (!state.requires.some((r) => r.id === requirement.id)) {
        state.requires = [...state.requires, requirement];
      }
      notify();
      await loadScopeData();
      antd.message.success(`${app.name} now needs ${resource.name} to run.`);
      return requirement;
    },

    async demoteResource(resource) {
      const requirement = state.requires.find((r) => r.resourceId === resource.id);
      if (!requirement) return;
      await store.demote(requirement);
      await loadScopeData();
    },

    // Bring one of the app's requirements back into a conversation that dropped it.
    async restore(requirement) {
      return store.attach(requirement.resourceId, requirement.addedBy, requirement.rationale, {
        silent: true,
      });
    },

    // @-mentions and panel picks land in the same place: this conversation's context.
    // Mentions pass quiet: the chip in the composer is already the feedback,
    // so Sage speaking up before the message is even sent just adds noise.
    addToContext(resource, options = {}) {
      if (!resource || !resource.id) return Promise.resolve();
      if (state.attachments.some((a) => a.resourceId === resource.id)) return Promise.resolve();
      state.resourceIndex[resource.id] = { ...(state.resourceIndex[resource.id] || {}), ...resource };
      return store.attach(resource.id, 'user', undefined, options);
    },

    acknowledgeAttachment(attachment, options = {}) {
      const pending = state.pendingTurn;
      const wantedKind = pending && pending.turn.waitsForAttachment;
      const meta = SW.util.RESOURCE_META;
      const matches =
        wantedKind &&
        (wantedKind === attachment.resourceKind ||
          (meta[wantedKind] &&
            meta[attachment.resourceKind] &&
            meta[wantedKind].group === meta[attachment.resourceKind].group));

      if (matches) {
        state.pendingTurn = null;
        notify();
        // The wait is over, so the flag has to come off before the turn goes
        // back through delivery — otherwise its own guard parks it again and
        // the thread sits on "waiting to attach" forever.
        const { waitsForAttachment, ...turn } = pending.turn;
        deliverTurn(turn, pending.meta, { resourceName: attachment.resourceName });
        return;
      }

      if (!options.quiet && state.thread && state.messages.length) {
        pushMessage({
          id: `ack_${Date.now()}`,
          role: 'assistant',
          at: new Date().toISOString(),
          blocks: [
            {
              type: 'text',
              value: `Got it — I can see **${attachment.resourceName}**. We'll use that.`,
            },
          ],
        });
      }
    },

    // Chat ---------------------------------------------------------------

    async newThread(options = {}) {
      const thread = await SW.api.createThread(
        state.scope.id || null,
        options.title || 'New chat',
        options.appId
      );
      state.thread = thread;
      state.messages = [];
      state.activePlanId = null;
      state.activePlan = null;
      state.touched = [];
      state.assistantTurns = 0;
      state.pendingTurn = null;
      state.planViewerId = null;
      // A turn still running in the conversation being left owns its own label, not this view's.
      // Leaving it set drew "Thinking…" under an empty new conversation that was doing nothing.
      state.typing = null;
      notify();
      // Starting from an app copies what that app needs into the conversation,
      // so the context it opens with is the app's rather than a stranger's.
      await Promise.all([refreshAttachments(), loadThreadList()]);
      return thread;
    },

    async openThread(threadId) {
      const thread = await SW.api.thread(threadId);
      await store.adoptThreadScope(thread);
      state.thread = thread;
      state.messages = await historyToMessages(thread.history || thread.messages || [], thread.handoff);
      state.activePlanId = thread.planId || null;
      state.touched = thread.touched || [];
      state.assistantTurns = state.messages.filter((m) => m.role === 'assistant').length;
      state.pendingTurn = null;
      state.planViewerId = null;
      state.typing = null;
      notify();
      await refreshAttachments();
      if (thread.planId) await store.loadPlan(thread.planId);
      return thread;
    },

    // No conversation open. Not the same as an empty one — nothing is persisted
    // and nothing shows up in a list.
    clearConversation() {
      state.thread = null;
      state.messages = [];
      state.buildHistory = [];
      state.buildMessages = [];
      state.attachments = [];
      state.touched = [];
      state.assistantTurns = 0;
      state.pendingTurn = null;
      state.typing = null;
      notify();
    },

    loadApps: loadAppList,

    // Which app Build has in front of it. Looking is free and reversible, so this changes freely
    // and never implies a change to either app. The server is refused while a build is streaming —
    // it holds one working tree — and says so, which is the one case worth a message.
    async selectApp(app) {
      const id = typeof app === 'string' ? app : app && app.id;
      // Already there, or already on the way there. `selecting` is what keeps a second asker from
      // racing the first into the server's turn lock — see where it is declared.
      if (!id || id === selecting || (state.activeApp && state.activeApp.id === id)) {
        return state.activeApp;
      }
      selecting = id;
      try {
        await SW.api.selectApp(id);
        // Reloads the app list with it: the transcript, the Bindings, the plan pin and the preview
        // are all the app's, so switching reloads the whole of Build, not one row's flag.
        await store.loadBuild();
        const selected = state.activeApp;
        state.activePlanId = (selected && selected.planId) || null;
        state.activePlan = null;
        notify();
        await refreshRequires();
        if (selected && selected.planId) await store.loadPlan(selected.planId);
        return selected;
      } catch (err) {
        antd.message.warning(err.message || 'Sage could not switch to that Built App.');
        return state.activeApp;
      } finally {
        selecting = null;
      }
    },

    // The name is the mutable half of an app's identity; its id names the directory and cannot
    // move, because a published App's entry point is fixed when the App is created.
    async renameApp(id, name) {
      await SW.api.patchApp(id, { name });
      await loadAppList();
    },

    clearApp() {
      state.activeApp = null;
      state.requires = [];
      state.activePlanId = null;
      state.activePlan = null;
      notify();
    },

    // A change landed on an app, so the conversation earns the tag. Committing
    // is what earns it; having the app on screen never does.
    async recordChange(appId, kind = 'changed') {
      if (!state.thread || !appId) return null;
      const summary = await SW.api.touchApp(state.thread.id, appId, kind);
      state.touched = summary.touched;
      state.thread = { ...state.thread, touched: summary.touched };
      notify();
      await Promise.all([refreshAttachments(), loadThreadList()]);
      return summary;
    },

    // A Build turn belongs to a conversation: it opens that conversation's own OpenCode
    // session, and its events are tagged with it in `.sage/history.jsonl`. Typing is intent, so
    // it opens one, the same way Chat does.
    // `skipResetGate` is only ever set by a button on a reset offer (#36), and only for the prompt
    // that offer was about: the gate already stopped this request once and the user answered it, so
    // re-matching it would hand back the same offer forever.
    async sendBuildPrompt(text, { skipResetGate = false } = {}) {
      if (!text.trim() || state.buildRunning) return null;
      if (!state.thread) await store.newThread();
      state.buildTurnMode = state.buildMode;
      // Echo what the server will write to the transcript, so live and reloaded read the same. For a
      // click that is the click, not the request — the request is already a bubble above the offer,
      // and repeating it would say the user asked twice (see build_stream's `user_text`).
      const bubble = skipResetGate ? 'Build it.' : text;
      state.buildHistory = state.buildHistory.concat([{ type: 'user', text: bubble }]);
      state.buildMessages = buildHistoryToMessages(state.buildHistory);
      state.buildRunning = true;
      state.buildTyping = 'Working…';
      notify();
      try {
        const res = await fetch('./api/project/build/stream', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ prompt: text, conversation: state.thread.id, skipResetGate }),
        });
        if (!res.ok) {
          const payload = await res.json().catch(() => ({}));
          throw new Error(payload.error || payload.message || res.statusText);
        }
        let stopped = false;
        await readSSE(res, (ev) => {
          if (!ev) return;
          if (ev.type === 'stopped') stopped = true;
          applyBuildEvent(ev);
          notify();
        });
        if (stopped) await store.loadBuild({ keepPreview: true });
      } catch (err) {
        applyBuildEvent({ type: 'error', message: String(err.message || err) });
      } finally {
        state.buildRunning = false;
        state.buildTyping = null;
        notify();
        await Promise.all([probePreview(), refreshBindings()]);
        notify();
      }
    },

    // Starting over is its own action (#36). The three exits below are the answers to a reset offer,
    // and `resetApp` is also what the composer's own Reset app control calls. Reloading the
    // transcript afterwards is what retires the offer: the server's copy carries no `live`, so the
    // buttons go with it and the same offer can't be answered twice.
    async resetApp() {
      if (state.buildRunning) throw new Error('A build is running. Stop it first, then reset.');
      await SW.api.resetApp();
      await store.loadBuild();
    },

    async resetAndBuild(prompt) {
      await store.resetApp();
      return store.sendBuildPrompt(prompt, { skipResetGate: true });
    },

    async buildWithoutReset(prompt) {
      await store.loadBuild({ keepPreview: true });
      return store.sendBuildPrompt(prompt, { skipResetGate: true });
    },

    async approveBuild(answers, planEdits, planId) {
      if (state.buildRunning) return null;
      if (!state.thread) await store.newThread();
      state.buildHistory = state.buildHistory.concat([{ type: 'user', text: 'Approved the plan.' }]);
      state.buildMessages = buildHistoryToMessages(state.buildHistory);
      state.buildRunning = true;
      state.buildTyping = 'Building…';
      notify();
      try {
        const payload = { answers: answers || '', conversation: state.thread.id };
        if (planEdits) payload.plan_edits = planEdits;
        // Which document this card's plan came from. Without it the server has to assume the newest
        // document is the one being approved, and a plan drafted by hand since then breaks that.
        if (planId) payload.plan_id = planId;
        const res = await fetch('./api/project/build/approve', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify(payload),
        });
        if (!res.ok) {
          const body = await res.json().catch(() => ({}));
          throw new Error(body.error || body.message || res.statusText);
        }
        let stopped = false;
        await readSSE(res, (ev) => {
          if (!ev) return;
          if (ev.type === 'stopped') stopped = true;
          applyBuildEvent(ev);
          notify();
        });
        if (stopped) await store.loadBuild({ keepPreview: true });
      } catch (err) {
        applyBuildEvent({ type: 'error', message: String(err.message || err) });
      } finally {
        state.buildRunning = false;
        state.buildTyping = null;
        notify();
        await Promise.all([probePreview(), refreshBindings(), loadThreadList()]);
        notify();
      }
    },

    async cancelBuildPlan() {
      await SW.api.cancelPlan();
      for (const msg of state.buildMessages) {
        (msg.blocks || []).forEach((b) => {
          if (b.type === 'build_plan') b.pending = false;
        });
      }
      await refreshProjectPlan();
      notify();
    },

    async stopBuild() {
      await SW.api.stopBuild();
      state.buildRunning = false;
      state.buildTyping = null;
      await store.loadBuild({ keepPreview: true });
    },

    async loadBuild(options = {}) {
      const project = await SW.api.project().catch(() => ({}));
      applyModelStatus(project);
      // No conversation open means a new one: nothing to replay. Asking for the whole project
      // here is what used to make "New conversation" look dead — the transcript never changed.
      const conversation = state.thread && state.thread.id;
      const [hist, running] = await Promise.all([
        conversation
          ? SW.api.history(conversation).catch(() => ({ history: [] }))
          : Promise.resolve({ history: [] }),
        SW.api.buildState().catch(() => ({ running: false })),
        refreshBindings(),
        refreshProjectPlan(),
        loadAppList(),
      ]);
      state.buildHistory = hist.history || [];
      state.buildMessages = buildHistoryToMessages(state.buildHistory);
      state.buildRunning = !!running.running;
      state.buildTyping = state.buildRunning ? 'Working…' : null;
      if (!options.keepPreview) state.previewStatus = 'starting';
      notify();
      await probePreview();
      if (state.buildRunning) store._watchBuild();
    },

    async refreshPreview() {
      state.previewStatus = 'starting';
      notify();
      await probePreview();
    },

    _watchBuild() {
      if (store._watchTimer) return;
      const tick = async () => {
        const running = await SW.api.buildState().catch(() => ({ running: true }));
        // Same scope as loadBuild: polling the whole project here would pull other
        // conversations' turns into the one on screen.
        const watched = state.thread && state.thread.id;
        const hist = watched
          ? await SW.api.history(watched).catch(() => ({ history: [] }))
          : { history: [] };
        state.buildHistory = hist.history || [];
        state.buildMessages = buildHistoryToMessages(state.buildHistory);
        state.buildRunning = !!running.running;
        if (!state.buildRunning) {
          state.buildTyping = null;
          clearInterval(store._watchTimer);
          store._watchTimer = null;
          await Promise.all([probePreview(), refreshBindings()]);
        }
        notify();
      };
      store._watchTimer = setInterval(tick, 2000);
    },

    async sendMessage(text) {
      if (!text.trim()) return;
      // The server refuses a second turn and says so in the transcript, which read as Sage
      // answering a question with a complaint about a build. Refusing here instead means the
      // composer is already disabled and the Stop button is already on screen: the answer to
      // "a turn is running" is a control, not a sentence.
      if (state.chatRunning) return;
      let thread = state.thread;
      if (!thread) thread = await store.newThread();
      // The conversation this turn belongs to. Everything below writes to the view only while it
      // is still the one on screen: a turn keeps running when you open another conversation or
      // start a new one, and its answer used to land in whichever Thread you had moved to.
      const turnThread = thread.id;
      // A latch, not a live test. Coming back to a conversation you left mid-turn re-reads the
      // transcript from the server, so a stream that resumed writing here would be appending to a
      // list that already contains what it wrote. Once it lets go, it stays let go, and the
      // `finally` re-reads the Thread for whoever is looking at it by then.
      let left = false;
      const mine = () => {
        if (left) return false;
        if (state.thread && state.thread.id === turnThread) return true;
        left = true;
        return false;
      };

      const attachments = state.attachments.map((a) => ({
        resourceId: a.resourceId,
        name: a.resourceName,
        kind: a.resourceKind,
        addedBy: a.addedBy,
      }));
      pushMessage({
        id: `u_${Date.now()}`,
        role: 'user',
        at: new Date().toISOString(),
        blocks: [{ type: 'text', value: text }],
        attachments,
      });
      state.typing = 'Thinking…';
      state.chatRunning = true;
      state.chatTurnThread = turnThread;
      notify();

      const assistant = {
        id: `a_${Date.now()}`,
        role: 'assistant',
        at: new Date().toISOString(),
        blocks: [],
      };
      const ensurePushed = () => {
        if (!state.messages.some((m) => m.id === assistant.id)) pushMessage(assistant);
      };

      // The answer as it is being written. `liveIndex` is where that block sits in this message,
      // or -1 when no block is open — a turn can write more than one, and each `final` closes the
      // one it completes so the next fragment starts a fresh one.
      //
      // Deltas arrive faster than the screen refreshes, and every one of them re-renders the whole
      // Thread — including earlier messages' charts, which stringify their options to decide
      // whether to redraw. So fragments accumulate and repaint once a frame. The text is identical
      // either way; the difference is a Thread that scrolls smoothly and one that stutters.
      let liveIndex = -1;
      let streamed = '';
      let painting = false;
      const flush = () => {
        painting = false;
        if (liveIndex < 0) return;
        const blocks = [...assistant.blocks];
        blocks[liveIndex] = { type: 'text', value: streamed, streaming: true };
        assistant.blocks = blocks;
        notify();
      };
      const paint = () => {
        if (painting) return;
        painting = true;
        requestAnimationFrame(flush);
      };

      try {
        const res = await fetch(`./api/threads/${thread.id}/chat/stream`, {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ prompt: text }),
        });
        if (!res.ok) {
          const payload = await res.json().catch(() => ({}));
          throw new Error(payload.error || payload.message || res.statusText);
        }
        await readSSE(res, async (ev) => {
          if (!ev || ev.type === 'user') return;
          // Moved on. The turn is still running and the server is still writing its transcript, so
          // nothing is lost — reopening the conversation replays it. What is not wanted is this
          // answer appearing under a different question.
          if (!mine()) return;
          if (ev.type === 'delta') {
            state.typing = null;
            ensurePushed();
            if (liveIndex < 0) {
              liveIndex = assistant.blocks.length;
              assistant.blocks = [...assistant.blocks, { type: 'text', value: '', streaming: true }];
              streamed = '';
            }
            if (ev.final) {
              // The whole text rather than the last fragment. The stream cannot be replayed, so
              // this is what repairs a live copy that dropped a frame — and it closes the block.
              streamed = ev.text || '';
              flush();
              liveIndex = -1;
            } else {
              streamed += ev.text || '';
              paint();
            }
          } else if (ev.type === 'agent' && ev.kind === 'text' && ev.text) {
            state.typing = null;
            ensurePushed();
            // What streamed was the turn happening. This is the record of it, and it is the only
            // part the server keeps, so the live blocks go: a Thread has to look the same on
            // reload as it did while it ran. Intermediate "let me read that file" text is not in
            // the transcript and so is not in the Thread either way. A queued repaint is harmless
            // once liveIndex is -1.
            liveIndex = -1;
            assistant.blocks = [...assistant.blocks.filter((b) => !b.streaming),
                                { type: 'text', value: ev.text }];
            notify();
          } else if (ev.type === 'agent' && ev.kind === 'tool') {
            state.typing = SW.util.activityLabel(ev);
            notify();
          } else if (ev.type === 'artifacts' || (ev.type === 'done' && ev.artifacts && ev.artifacts.length)) {
            state.typing = null;
            ensurePushed();
            const items = ev.items || ev.artifacts;
            const have = new Set(
              assistant.blocks.filter((b) => b.type === 'image' || b.type === 'table' || b.type === 'file')
                .map((b) => b.src || b.path || b.title)
            );
            const fresh = (items || []).filter((a) => !have.has(fileUrl(a.path)) && !have.has(a.path) && !have.has(a.title));
            if (fresh.length) {
              assistant.blocks = [...assistant.blocks, ...(await blocksForArtifacts(fresh))];
            }
            if (state.thread && items && items.length) {
              const have = new Set((state.thread.artifacts || []).map((a) => a.path));
              const extra = items.filter((a) => a.path && !have.has(a.path));
              if (extra.length) {
                state.thread = {
                  ...state.thread,
                  artifacts: [...(state.thread.artifacts || []), ...extra],
                };
              }
            }
            notify();
            refreshAttachments();
          } else if (ev.type === 'error' || ev.type === 'stopped') {
            state.typing = null;
            ensurePushed();
            // A status line, not a text block: this is Sage reporting on the turn, and it should
            // not read like the answer to the question. Same shape the reload path builds. Whatever
            // had streamed stays above it — a stopped turn's half-answer is still worth reading.
            assistant.blocks = [...assistant.blocks, {
              type: 'status',
              ok: false,
              value: ev.message || (ev.type === 'stopped' ? 'Stopped.' : 'The turn failed.'),
            }];
            notify();
          } else if (ev.type === 'done') {
            state.typing = null;
            notify();
          } else if (ev.type === 'handoff-suggest') {
            state.typing = null;
            pushMessage({
              id: `sug_${Date.now()}`,
              role: 'system',
              at: new Date().toISOString(),
              blocks: [{ type: 'plan_suggestion', reason: ev.reason }],
            });
          }
        });
      } catch (err) {
        if (mine()) {
          state.typing = null;
          ensurePushed();
          assistant.blocks = [...assistant.blocks, { type: 'text', value: String(err.message || err) }];
        }
        notify();
      } finally {
        // The turn is over wherever the reader is now, so the flag clears either way — it says
        // "this project is busy", not "this conversation is busy".
        state.chatRunning = false;
        state.chatTurnThread = null;
        if (mine()) state.typing = null;
        notify();
      }
      // Back on the conversation this turn ran in, but the stream stopped writing to the view when
      // it was left. Re-read it, so the answer is there rather than in a Thread nobody reloaded.
      if (left && state.thread && state.thread.id === turnThread) {
        await store.openThread(turnThread).catch(() => {});
      }
      await loadThreadList();
      await refreshAttachments();
    },

    // Stop is the answer to a turn that will not end. Chat already caps a turn at ten minutes, and
    // the comment that chose that number said it was generous "because by then the person can
    // press Stop" — which was true of Build and of nothing in Chat. Same endpoint Build uses: one
    // project runs one turn, so there is one thing to interrupt.
    async stopChat() {
      state.typing = 'Stopping…';
      notify();
      try {
        await SW.api.stopBuild();
      } catch (err) {
        antd.message.error(String((err && err.message) || err));
        state.typing = null;
        notify();
        return;
      }
      // A turn this tab is streaming reports its own stop and clears the flag as it unwinds. The
      // watcher is for the turn it is not — after a reload, or one another tab started — and for
      // the stop the stream never hears, so the composer never stays disabled on a freed lock.
      store._watchTurn();
    },

    // Whether the project is mid-turn, straight from the server's turn lock. The one place the
    // answer is authoritative — a tab that reloaded mid-turn has no stream and no memory of it,
    // and used to offer a composer that the server would then refuse.
    async refreshTurnState() {
      const { running } = await SW.api.buildState().catch(() => ({ running: false }));
      const was = state.chatRunning;
      state.chatRunning = !!running;
      if (running) {
        notify();
        store._watchTurn();
        return;
      }
      // The lock is free, so nothing is running whatever this tab still believes.
      state.chatTurnThread = null;
      state.typing = null;
      notify();
      // It finished while nothing here was listening, so the transcript on screen is behind.
      if (was && state.thread) await store.openThread(state.thread.id).catch(() => {});
    },

    // Poll the lock so the composer comes back by itself. The lock is the only thing that knows:
    // a stream can still be reading an SSE the server has finished with, and a Stop can land on a
    // turn that never says a word back — both look like "still running" from in here, and both end
    // with a free lock. Errs towards running, so a poll that fails never enables a composer the
    // server is about to refuse.
    _watchTurn() {
      if (store._turnWatchTimer) return;
      store._turnWatchTimer = setInterval(async () => {
        const { running } = await SW.api.buildState().catch(() => ({ running: true }));
        if (running) return;
        clearInterval(store._turnWatchTimer);
        store._turnWatchTimer = null;
        await store.refreshTurnState();
      }, 2000);
    },

    async chooseOption(option) {
      if (option.opensPanel) store.focusPanel(option.opensPanel);
      pushMessage({
        id: `uc_${Date.now()}`,
        role: 'user',
        at: new Date().toISOString(),
        blocks: [{ type: 'text', value: option.label }],
      });
      const reply = await SW.api.advance(state.thread.id, option.next);
      state.scriptMeta = { planTemplate: reply.planTemplate };
      await deliverTurn(reply.message, { suggestPlan: reply.suggestPlan });
    },

    dismissNudge() {
      state.nudgeDismissed = true;
      state.messages = state.messages.filter(
        (m) => !m.blocks.some((b) => b.type === 'graduation_nudge')
      );
      notify();
    },

    dismissPlanSuggestion() {
      state.messages = state.messages.filter(
        (m) => !m.blocks.some((b) => b.type === 'plan_suggestion')
      );
      if (state.thread) {
        SW.api.patchThread(state.thread.id, { handoff: 'suppress' }).catch(() => {});
        state.thread = { ...state.thread, handoff: { ...(state.thread.handoff || {}), suppressed: true, status: 'suppressed' } };
      }
      notify();
    },

    async draftHandoffPlan(threadId) {
      const id = threadId || (state.thread && state.thread.id);
      if (!id) return null;
      if (!state.thread || state.thread.id !== id) await store.openThread(id);
      state.typing = 'Writing a plan…';
      notify();
      try {
        const draft = await SW.api.draftHandoffPlan(id);
        state.thread = { ...state.thread, handoff: draft.handoff };
        state.messages = state.messages.filter(
          (m) => !(m.blocks || []).some((b) => b.type === 'plan_suggestion')
        );
        state.handoffDraft = draft;
        state.handoffOpen = true;
        notify();
        return draft;
      } catch (err) {
        antd.message.error(String((err && err.message) || err));
        return null;
      } finally {
        state.typing = null;
        notify();
      }
    },

    async confirmHandoff(include) {
      const id = state.thread && state.thread.id;
      if (!id) return null;
      const result = await SW.api.confirmHandoff(id, include);
      state.handoffOpen = false;
      state.handoffDraft = null;
      state.thread = { ...state.thread, handoff: result.handoff };
      const projects = await SW.api.projects().catch(() => state.projects);
      state.projects = projects;
      const current = projects.find((p) => p.id === state.scope.id) || projects[0];
      if (current) state.scope = { ...state.scope, ...current };
      notify();
      SW.router.go(`#/build/${id}`);
      return result;
    },

    // An upload is both things at once: it adds the file to the project and
    // puts it in context for this conversation.
    async uploadFile(file) {
      let thread = state.thread;
      if (!thread) thread = await store.newThread();
      const name = (file && file.name) || String(file);
      const res = await fetch(`./api/project/upload?name=${encodeURIComponent(name)}`, {
        method: 'POST',
        body: file instanceof Blob ? file : undefined,
      });
      const body = await res.json().catch(() => ({}));
      if (!res.ok) throw new Error(body.error || 'Upload failed');
      await loadScopeData();
      const resource = {
        id: `file:${body.path}`,
        name: name,
        kind: 'file',
        path: body.path,
        source: body.source || 'scratch',
      };
      state.resourceIndex[resource.id] = resource;
      await store.attach(resource.id, 'user', 'Uploaded in this conversation.', { silent: true });
      antd.message.success(`${name} added to this context`);
      return resource;
    },

    async pinLeaf(parent, pin) {
      if (!parent || !parent.id) return null;
      await store.addToProject({ ...parent, pin }, { silent: true });
      return true;
    },

    async unpinLeaf(parent, pin) {
      await SW.api.unpinFromProject(parent.id, pin);
      await loadScopeData();
      return true;
    },

    async addScratchToDataset(resource, datasetId) {
      if (!resource || !resource.path || !datasetId) return null;
      const res = await SW.api.promoteScratch(resource.path, datasetId);
      const oldId = resource.id;
      await loadScopeData();
      const tid = conversationId();
      const old = (state.attachments || []).find(
        (a) => a.resourceId === oldId || a.path === resource.path
      );
      if (old && tid) {
        await SW.api.removeFromConversation(tid, old.id).catch(() => null);
        state.attachments = state.attachments.filter((a) => a.id !== old.id);
        const next = {
          id: `file:${res.path}`,
          name: resource.name,
          kind: 'file',
          path: res.path,
        };
        state.resourceIndex[next.id] = next;
        await store.attach(next.id, 'user', undefined, { silent: true, quiet: true });
      }
      antd.message.success(`${resource.name} is on the Dataset`);
      return res;
    },

    // Plans --------------------------------------------------------------

    // The plan opens beside the work, not over it. Build swaps its centre
    // pane instead of stacking a panel, so it only tracks the id.
    openPlanViewer(planId) {
      state.planViewerId = planId;
      notify();
    },

    closePlanViewer() {
      state.planViewerId = null;
      notify();
    },

    // Same artifact, different treatment per mode: Chat gets the friendly
    // editor beside the conversation, Build gets it in the IDE.
    openPlanArtifact(planId) {
      const { mode } = SW.router.get();
      if (mode === 'chat' || mode === 'build') {
        state.planViewerId = planId;
        notify();
        return;
      }
      SW.router.go(`#/plan/${planId}`);
    },

    // The plan is a document the app owns, not the thing context hangs off, so
    // loading one no longer reshuffles what the conversation can see.
    async loadPlan(planId) {
      const plan = await SW.api.plan(planId);
      state.activePlan = plan;
      state.activePlanId = plan.id;
      notify();
      return plan;
    },

    async draftPlan() {
      const plan = await SW.api.createPlan({
        threadId: state.thread && state.thread.id,
        projectId: state.scope.id,
        template: state.scriptMeta.planTemplate,
      });
      state.activePlanId = plan.id;
      state.messages = state.messages.filter(
        (m) => !m.blocks.some((b) => b.type === 'plan_suggestion')
      );
      pushMessage({
        id: `plan_${Date.now()}`,
        role: 'assistant',
        at: new Date().toISOString(),
        blocks: [{ type: 'plan_card', planId: plan.id }],
      });
      await store.loadPlan(plan.id);
      // The plan is an artifact in the project the moment it exists, so the
      // panel has to hear about it.
      await Promise.all([loadThreadList(), loadScopeData()]);
      antd.message.success('Plan drafted — it is in the panel under Artifacts');
      return plan;
    },

    async refreshPlan() {
      if (state.activePlanId) await store.loadPlan(state.activePlanId);
    },

    async saveToProject(body) {
      const result = await SW.api.saveToProject(state.thread.id, body);
      await store.reloadProjects();
      const project = state.projects.find((p) => p.id === result.projectId);
      state.nudgeDismissed = true;
      state.messages = state.messages.filter(
        (m) => !m.blocks.some((b) => b.type === 'graduation_nudge')
      );
      if (project) {
        state.scope = project;
        state.scopeFlash = true;
        notify();
        setTimeout(() => { state.scopeFlash = false; notify(); }, 500);
        await Promise.all([loadScopeData(), loadThreadList()]);
      }
      state.thread = await SW.api.thread(state.thread.id);
      notify();
      return result;
    },

    // Shared refresh helpers ---------------------------------------------

    reloadScopeData: loadScopeData,
    reloadThreads: loadThreadList,
    reloadAttachments: refreshAttachments,
    reloadRequires: refreshRequires,

    async reloadNotifications() {
      state.notifications = await SW.api.notifications();
      notify();
    },
  };

  SW.store = store;

  SW.brand = {
    assistant() {
      const brand = store.get().brand || {};
      return brand.assistantName || 'Sage';
    },
    product() {
      const brand = store.get().brand || {};
      return brand.productName || 'AI Workbench';
    },
  };
})();
