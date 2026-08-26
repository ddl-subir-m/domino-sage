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
    activeApp: null,
    activePlanId: null,
    activePlan: null,

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
      } else if (ev.type === 'handoff-suggest' && !hideSuggest) {
        assistant = null;
        messages.push({
          id: `sug_${messages.length}`,
          role: 'system',
          blocks: [{ type: 'plan_suggestion' }],
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

  const GATE_DECISIONS = { 'awaiting approval': true, 'architecture ready': true };

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
          durationMs: 0,
          code: ev.detail || '',
        });
      } else if (ev.type === 'plan-proposed') {
        assistant = null;
        if (pendingPlan) pendingPlan.pending = false;
        const block = {
          type: 'build_plan',
          plan: ev.plan || '',
          kind: ev.kind || 'plan',
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
    if (ev.type === 'stopped') return;
    if (ev.type === 'active' || ev.type === 'phase' || ev.type === 'typecheck-start' || ev.type === 'iterate') return;
    state.buildHistory = state.buildHistory.concat([ev]);
    state.buildMessages = buildHistoryToMessages(state.buildHistory);
  }

  async function refreshBindings() {
    const body = await SW.api.bindings().catch(() => ({ bindings: [] }));
    state.bindings = body.bindings || [];
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
      state.scope = projects[0] || state.scope;
      applyModelStatus(projects[0]);
      state.charts = charts;
      state.starters = starters;
      state.notifications = notifications;
      state.ready = true;
      state.resourcesLoading = true;
      notify();
      await Promise.all([loadScopeData(), loadThreadList()]);
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

    async createProject(name) {
      antd.message.info('This project is the current scope.');
      return null;
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
              antd.message.error(err.message);
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
      state.attachments = [];
      state.touched = [];
      state.assistantTurns = 0;
      state.pendingTurn = null;
      state.typing = null;
      notify();
    },

    // Which app Build has in the preview. Looking is free and reversible, so
    // this changes freely and never implies a change to the app.
    async selectApp(app) {
      state.activeApp = app;
      state.activePlanId = (app && app.planId) || null;
      state.activePlan = null;
      notify();
      await refreshRequires();
      if (app && app.planId) await store.loadPlan(app.planId);
      return app;
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

    // Changes asked for in Build belong to the project's Build session, not
    // the Chat Thread. History is `.sage/history.jsonl`.
    async sendBuildPrompt(text) {
      if (!text.trim() || state.buildRunning) return null;
      state.buildTurnMode = state.buildMode;
      state.buildHistory = state.buildHistory.concat([{ type: 'user', text }]);
      state.buildMessages = buildHistoryToMessages(state.buildHistory);
      state.buildRunning = true;
      state.buildTyping = 'Working…';
      notify();
      try {
        const res = await fetch('./api/project/build/stream', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ prompt: text }),
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

    async approveBuild(answers, planEdits) {
      if (state.buildRunning) return null;
      state.buildHistory = state.buildHistory.concat([{ type: 'user', text: 'Approved the plan.' }]);
      state.buildMessages = buildHistoryToMessages(state.buildHistory);
      state.buildRunning = true;
      state.buildTyping = 'Building…';
      notify();
      try {
        const payload = { answers: answers || '' };
        if (planEdits) payload.plan_edits = planEdits;
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
      const [hist, running] = await Promise.all([
        SW.api.history().catch(() => ({ history: [] })),
        SW.api.buildState().catch(() => ({ running: false })),
        refreshBindings(),
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
        const hist = await SW.api.history().catch(() => ({ history: [] }));
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
      let thread = state.thread;
      if (!thread) thread = await store.newThread();

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
          if (ev.type === 'agent' && ev.kind === 'text' && ev.text) {
            state.typing = null;
            ensurePushed();
            assistant.blocks = [...assistant.blocks, { type: 'text', value: ev.text }];
            notify();
          } else if (ev.type === 'agent' && ev.kind === 'tool') {
            if (ev.tool === 'bash') state.typing = 'Running Python…';
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
          } else if (ev.type === 'error') {
            state.typing = null;
            ensurePushed();
            assistant.blocks = [...assistant.blocks, { type: 'text', value: ev.message || 'The turn failed.' }];
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
              blocks: [{ type: 'plan_suggestion' }],
            });
          }
        });
      } catch (err) {
        state.typing = null;
        ensurePushed();
        assistant.blocks = [...assistant.blocks, { type: 'text', value: String(err.message || err) }];
        notify();
      } finally {
        state.typing = null;
        notify();
      }
      await loadThreadList();
      await refreshAttachments();
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
