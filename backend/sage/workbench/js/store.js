window.SW = window.SW || {};

(function () {
  const SANDBOX = {
    id: 'sandbox',
    name: 'Personal sandbox',
    color: '#8F8FA3',
    ephemeral: true,
    appCount: 0,
    planCount: 0,
    memberCount: 1,
  };

  const state = {
    ready: false,
    me: null,
    projects: [],
    scope: SANDBOX,
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
    handoffPlanId: null,
    graduationOpen: false,
    inviteOpen: false,
    paletteOpen: false,
    scopePickerOpen: false,
    helpOpen: false,

    // Project-scoped data
    resourceGroups: {},
    resourceIndex: {},
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
    model: 'auto',
    phase: 'planning',
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

  function indexResources(groups) {
    const index = {};
    Object.values(groups || {}).forEach((list) => list.forEach((r) => { index[r.id] = r; }));
    return index;
  }

  async function loadScopeData() {
    const scope = state.scope;
    const [resources, activity] = await Promise.all([
      SW.api.resources(scope.id),
      SW.api.activity(scope.id),
    ]);
    state.resourceGroups = resources.groups;
    state.resourceIndex = indexResources(resources.groups);
    state.activity = activity;

    const members = await SW.api.members(scope.ephemeral ? null : scope.id);
    state.members = scope.ephemeral ? [] : members.members;
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
    for (const ev of history || []) {
      if (ev.type === 'user') {
        assistant = null;
        messages.push({
          id: `u_${messages.length}`,
          role: 'user',
          at: ev.at,
          blocks: [{ type: 'text', value: ev.text || '' }],
          contextIds: ev.contextIds,
        });
      } else if (ev.type === 'agent' && ev.kind === 'text' && ev.text) {
        ensureAssistant().blocks.push({ type: 'text', value: ev.text });
      } else if (ev.type === 'agent' && ev.kind === 'tool') {
        ensureAssistant().blocks.push({
          type: 'sandbox_run',
          label: ev.tool === 'bash' ? 'Ran Python' : `Ran ${ev.tool || 'tool'}`,
          durationMs: 0,
          code: ev.detail || '',
        });
      } else if (ev.type === 'artifacts') {
        ensureAssistant().blocks.push(...(await blocksForArtifacts(ev.items)));
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

    maybeNudge();
  }

  // The sandbox nudge fires once, after enough real work has happened that
  // losing it would actually hurt.
  function maybeNudge() {
    if (!state.scope.ephemeral) return;
    if (state.nudgeDismissed) return;
    if (state.assistantTurns < 2) return;
    if (state.messages.some((m) => m.blocks.some((b) => b.type === 'graduation_nudge'))) return;
    pushMessage({ id: `nudge_${Date.now()}`, role: 'system', blocks: [{ type: 'graduation_nudge' }] });
  }

  const store = {
    SANDBOX,

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

    async init() {
      const [me, projects, charts, starters, notifications] = await Promise.all([
        SW.api.me(),
        SW.api.projects(),
        SW.api.charts(),
        SW.api.starters(),
        SW.api.notifications(),
      ]);
      state.me = me;
      state.projects = projects;
      state.scope = projects[0] || state.scope;
      state.charts = charts;
      state.starters = starters;
      state.notifications = notifications;
      state.ready = true;
      await Promise.all([loadScopeData(), loadThreadList()]);
      notify();
    },

    // Scope --------------------------------------------------------------

    // Scope survives mode switches. This is the continuity mechanism.
    async setScope(project, options = {}) {
      if (state.scope.id === project.id) return;
      // A conversation that belongs to the project we are moving into stays
      // open. Opening a deep link resolves the app and the conversation at the
      // same time, and whichever settled the scope used to wipe the other.
      const keepThread =
        state.thread && (state.thread.projectId || SANDBOX.id) === project.id;

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
      antd.message.info('Named apps are created from the Sage hub. This project is the current scope.');
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
      const result = await SW.api.addToProject(state.scope.id, resource.id);
      await loadScopeData();
      if (!options.silent && result.added) {
        antd.message.success(`${resource.name} is now in ${state.scope.name}`);
      }
      return result;
    },

    async removeFromProject(resource) {
      try {
        await SW.api.removeFromProject(state.scope.id, resource.id);
      } catch (err) {
        // The server refuses when an app still needs it, and that refusal is
        // the useful part — it names what would break.
        antd.message.error(err.message);
        return false;
      }
      await loadScopeData();
      antd.message.info(`${resource.name} is out of ${state.scope.name}`);
      return true;
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
      if (state.attachments.some((a) => a.resourceId === resource.id)) return Promise.resolve();
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
        state.scope.ephemeral ? null : state.scope.id,
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

    // Changes asked for inside Build belong to the conversation like anything
    // else — the builder used to keep them in local state, where they died on
    // navigation.
    //
    // A turn can land on more than one app, because a project is one file tree
    // and apps in it share code. Every app that changed gets its own entry in
    // the transcript, so the one in the preview is not the only one you can see
    // or act on. "I changed something else too, go and look" is not an
    // acceptable substitute for showing it.
    async sendBuildMessage(text, options = {}) {
      if (!text.trim()) return null;
      let thread = state.thread;
      if (!thread) {
        thread = await store.newThread({
          appId: options.appId,
          title: summarise(text),
        });
      }

      const userBlocks = [{ type: 'text', value: text }];
      pushMessage({ id: `u_${Date.now()}`, role: 'user', at: new Date().toISOString(), blocks: userBlocks });
      SW.api.appendMessage(thread.id, userBlocks, 'user').catch(() => {});

      state.typing = 'Working out the change…';
      notify();
      await SW.util.sleep(600);
      state.typing = null;

      // The previewed app always changes. Naming another app in the project is
      // taken as asking for it too, which is how the multi-app case is reachable
      // without pretending to infer shared code.
      const needle = text.toLowerCase();
      const alsoNamed = (options.apps || []).filter(
        (a) => a.id !== options.appId && needle.includes(a.name.toLowerCase())
      );
      const targets = [
        ...(options.appId ? [{ id: options.appId, name: options.appName }] : []),
        ...alsoNamed,
      ];

      const blocks = [
        {
          type: 'text',
          value:
            targets.length > 1
              ? `That lands on ${targets.length} apps in this project. Both are below — review them separately, because publishing one does not publish the other.`
              : state.activePlanId
              ? `Making that change now — I'll update the plan's **Done when** list to match, then rebuild the affected screen.`
              : `Making that change now — I'll rebuild the affected screen. This app has no plan, so I'll note the decision on the app instead.`,
        },
        ...targets.map((target) => ({
          type: 'app_change',
          appId: target.id,
          summary: summarise(text),
        })),
      ];
      pushMessage({ id: `a_${Date.now()}`, role: 'assistant', at: new Date().toISOString(), blocks });
      SW.api.appendMessage(thread.id, blocks, 'assistant').catch(() => {});

      for (const target of targets) {
        await store.recordChange(target.id, 'changed');
      }
      await loadThreadList();
      return targets;
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
            state.typing = ev.tool === 'bash' ? 'Running Python…' : `Using ${ev.tool}…`;
            notify();
          } else if (ev.type === 'artifacts') {
            state.typing = null;
            ensurePushed();
            assistant.blocks = [...assistant.blocks, ...(await blocksForArtifacts(ev.items))];
            notify();
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
      }
      await loadThreadList();
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
      };
      state.resourceIndex[resource.id] = resource;
      await store.attach(resource.id, 'user', 'Uploaded in this conversation.', { silent: true });
      antd.message.success(`${name} added to this context`);
      return resource;
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
      if (state.scope.ephemeral) {
        antd.message.warning('Save this chat to a project first — plans live at project scope.');
        return null;
      }
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
})();
