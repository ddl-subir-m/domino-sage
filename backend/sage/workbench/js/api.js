window.SW = window.SW || {};

// Always relative. Domino's nginx proxy rewrites the app root, so a leading
// slash would escape the app's mount path.
const BASE = './api';

async function request(path, options = {}) {
  const url = `${BASE}${path}`;
  try {
    const headers = { ...(options.headers || {}) };
    if (options.body && !(options.body instanceof Blob) && typeof options.body !== 'string') {
      headers['Content-Type'] = headers['Content-Type'] || 'application/json';
    }
    const res = await fetch(url, {
      ...options,
      headers,
      body: options.body && typeof options.body === 'object' && !(options.body instanceof Blob)
        && !(options.body instanceof ArrayBuffer) && !(options.body instanceof Uint8Array)
        ? JSON.stringify(options.body)
        : options.body,
    });
    if (!res.ok) {
      let detail = `${res.status} ${res.statusText}`;
      let payload = null;
      try {
        payload = await res.json();
        if (payload && (payload.detail || payload.error || payload.message)) {
          detail = payload.detail || payload.error || payload.message;
          if (typeof detail === 'object') detail = detail.message || JSON.stringify(detail);
        }
      } catch (parseError) {
        // Response wasn't JSON; the status line is the best we have.
      }
      // A refusal often carries more than a sentence — which files still use the Resource, which
      // rows a query touched. Flattening it to a message throws that away, and the caller is the
      // only one that knows what to do with it.
      const err = new Error(detail);
      err.status = res.status;
      err.payload = payload;
      throw err;
    }
    if (res.status === 204) return null;
    const ct = res.headers.get('content-type') || '';
    if (ct.includes('application/json')) return await res.json();
    return await res.json().catch(() => ({}));
  } catch (err) {
    console.error('[api]', url, err);
    throw err;
  }
}

const post = (path, body) => request(path, { method: 'POST', body });
const patch = (path, body) => request(path, { method: 'PATCH', body });
const del = (path) => request(path, { method: 'DELETE' });

function empty() {
  return Promise.resolve([]);
}

function kindFromPrefix(id) {
  const i = (id || '').indexOf(':');
  return i === -1 ? 'file' : id.slice(0, i);
}

function rawFromPrefix(id) {
  const i = (id || '').indexOf(':');
  return i === -1 ? id : id.slice(i + 1);
}

async function fetchDominoListing() {
  const [res, assets] = await Promise.all([
    request('/resources').catch(() => ({ data_sources: [], llm_aliases: [], model_apis: [], errors: {
      data_sources: SW.brand.text('Could not list {dataSourcePlural}.'),
      llm_aliases: 'Could not list language models.',
      model_apis: SW.brand.text('Could not list {modelApiPlural}.'),
    } })),
    request('/assets').catch(() => ({
      assets: [], error: SW.brand.text('Could not list {datasetPlural}.'),
    })),
  ]);
  const errors = { ...(res.errors || {}) };
  if (assets.error) errors.datasets = assets.error;
  return {
    errors,
    groups: {
      dataset: (assets.assets || []).map((a) => ({
        id: `dataset:${a.id}`, name: a.name, kind: 'dataset',
        description: a.project ? `in ${a.project}` : '',
        project: a.project,
        path: a.mount_path || a.mountPath || undefined,
        writable: !!a.writable,
      })),
      datasource: (res.data_sources || []).map((d) => ({
        id: `data_source:${d.id}`,
        name: d.name,
        kind: 'datasource',
        description: d.connector || '',
        bindingKey: ['data_source', d.id],
        levels: d.levels || [],
        default_database: d.default_database,
        default_schema: d.default_schema,
        connector: d.connector,
      })),
      model_llm: (res.llm_aliases || []).map((a) => ({
        id: `llm_alias:${a.id}`,
        name: a.display_name || a.name,
        alias: a.name,
        kind: 'model_llm',
        capabilities: a.capabilities || [],
        reasoning_efforts: a.reasoning_efforts || [],
        bindingKey: ['llm_alias', a.id],
      })),
      model_predictive: (res.model_apis || []).map((m) => ({
        id: `model_api:${m.id}`,
        name: m.name || m.display_name,
        kind: 'model_predictive',
        description: m.project || m.status || '',
        bindingKey: ['model_api', m.id],
      })),
    },
  };
}

function membershipKind(item) {
  const kind = item && item.kind;
  return (SW.util && SW.util.uiKind(kind)) || kind;
}

function emptyResourceGroups() {
  return {
    dataset: [], table: [], datasource: [], model_llm: [], model_predictive: [],
    tool: [], agent: [], skill: [], mcp: [], file: [], pin: [],
  };
}

function pinRow(parent, pin) {
  const kind = membershipKind(parent);
  const bare = rawFromPrefix(parent.id);
  if (kind === 'dataset' && pin && pin.path) {
    return {
      id: `dsfile:${bare}:${pin.path}`,
      name: pin.name || String(pin.path).split('/').pop(),
      kind: 'file',
      datasetId: bare,
      datasetRelPath: pin.path,
      datasetName: parent.name,
      parentId: parent.id,
      subtitle: parent.name,
    };
  }
  if (kind === 'datasource' && pin && pin.table) {
    const dotted = [pin.database, pin.schema, pin.table].filter(Boolean).join('.');
    return {
      id: `table:${bare}:${dotted}`,
      name: pin.name || pin.table,
      kind: 'table',
      bindingKey: parent.bindingKey || ['data_source', bare],
      scope: {
        database: pin.database || '',
        schema: pin.schema || '',
        table: pin.table,
      },
      parentId: parent.id,
      subtitle: parent.name,
    };
  }
  return null;
}

function rowFromMember(item) {
  const kind = membershipKind(item);
  return {
    id: item.id,
    name: item.name,
    kind,
    description: item.description || (item.project ? `in ${item.project}` : ''),
    project: item.project,
    path: item.path,
    bindingKey: item.bindingKey,
    alias: item.alias,
    capabilities: item.capabilities || [],
    reasoning_efforts: item.reasoning_efforts || [],
    pins: item.pins || [],
    membershipParent: true,
    writable: item.writable,
    levels: item.levels,
    default_database: item.default_database,
    default_schema: item.default_schema,
  };
}

function groupsFromMembership(items, attached) {
  const groups = emptyResourceGroups();
  (items || []).forEach((item) => {
    const row = rowFromMember(item);
    if (!groups[row.kind]) return;
    groups[row.kind].push(row);
    (item.pins || []).forEach((pin) => {
      const leaf = pinRow(row, pin);
      if (leaf) groups.pin.push(leaf);
    });
  });
  groups.file = (attached || [])
    .filter((e) => !SW.util.isHiddenFromExplorer(e.path))
    .map((e) => ({
      id: `file:${e.path}`,
      name: e.name || (e.path || '').split('/').pop(),
      kind: 'file',
      path: e.path,
      source: e.source || (String(e.path || '').startsWith('.sage/scratch/') ? 'scratch' : undefined),
    }));
  return groups;
}

function overlayListing(groups, listing) {
  const next = { ...groups };
  ['dataset', 'datasource', 'model_llm', 'model_predictive'].forEach((kind) => {
    const liveById = {};
    ((listing.groups || {})[kind] || []).forEach((r) => { liveById[r.id] = r; });
    next[kind] = (groups[kind] || []).map((row) => {
      const live = liveById[row.id];
      if (!live) return row;
      return { ...row, ...live, pins: row.pins, membershipParent: true };
    });
  });
  return next;
}

SW.api = {
  me: () => request('/me'),
  brand: () => request('/brand'),
  project: () => request('/project'),

  // The project this builder is bound to, first, followed by the viewer's other Sage Projects (#47).
  // Only the first entry can be described in full: its display name, its untitled flag and its model
  // are read from this container. The rest are Domino names and an id to attach by — a Sage overlay
  // lives in the builder that owns it, and this one cannot read another's.
  projects: async () => {
    const [p, listing] = await Promise.all([
      request('/project'),
      request('/projects').catch(() => ({ items: [], provisioning: false })),
    ]);
    const here = {
      id: p.id,
      name: p.name || p.id,
      color: '#543FDE',
      untitled: !!p.untitled,
      ownerName: 'you',
      memberCount: 1,
      appCount: 1,
      planCount: 0,
      model: p.model,
      current: true,
      // A fact about this container, not this project — and this row is the only one that
      // describes the container the Workbench is running in.
      provisioning: listing.provisioning !== false,
    };
    const elsewhere = (listing.items || [])
      .filter((it) => it && it.id && !it.current)
      .map((it) => ({ id: it.id, name: it.name || it.id, color: '#543FDE', current: false }));
    return [here, ...elsewhere];
  },
  gallery: () => request('/gallery'),
  openProject: (id) => request(`/projects/${encodeURIComponent(id)}/open`, { method: 'POST' }),
  projectStatus: (id, workspaceId) =>
    request(`/projects/status?project_id=${encodeURIComponent(id)}` +
      (workspaceId ? `&workspace_id=${encodeURIComponent(workspaceId)}` : '')),
  createProject: (name) => request('/projects', { method: 'POST', body: { name } }),
  resources: async () => {
    // Membership is a local file. Do not wait on the Domino listing or on /project
    // (which starts the preview) — a hard refresh otherwise paints Data (0) for seconds.
    const membership = await request('/project/resources').catch(() => ({ items: [] }));
    return {
      groups: groupsFromMembership(membership.items || [], []),
      members: membership.items || [],
      errors: {},
      aliases: [],
    };
  },
  resourceListing: () => fetchDominoListing(),
  overlayResourceListing: overlayListing,
  resource: (id) => {
    const { resourceIndex, catalogueParents } = SW.store.get();
    // The index holds the project's working set. A catalogue parent is not in it yet and the
    // drawer opens on one — without this fallback it would show the bare Domino id as the name,
    // and `Use in this chat` would write that id into the project rail as the resource's name.
    const known = resourceIndex[id] || (catalogueParents || []).find((r) => r.id === id);
    return Promise.resolve(known || { id, name: rawFromPrefix(id), kind: kindFromPrefix(id) });
  },
  restrictedIn: () => Promise.resolve([]),

  catalog: async ({ q, kind } = {}) => {
    const [listing, membership] = await Promise.all([
      fetchDominoListing(),
      request('/project/resources').catch(() => ({ items: [] })),
    ]);
    const memberIds = new Set((membership.items || []).map((i) => i.id));
    const allKeys = ['dataset', 'datasource', 'model_llm', 'model_predictive', 'agent', 'skill', 'mcp'];
    const keys = kind ? [kind] : allKeys;
    const needle = (q || '').trim().toLowerCase();
    const counts = {};
    allKeys.forEach((k) => { counts[k] = (listing.groups[k] || []).length; });
    const results = [];
    keys.forEach((k) => {
      (listing.groups[k] || []).forEach((r) => {
        if (needle && !(r.name || '').toLowerCase().includes(needle)) return;
        results.push({
          ...r,
          inProject: memberIds.has(r.id),
          description: r.description || '',
          originName: r.project || SW.brand.platform(),
          ownerName: '',
        });
      });
    });
    return { results, counts, errors: listing.errors };
  },
  addToProject: (projectId, resource) => {
    const row = typeof resource === 'string'
      ? { id: resource, kind: kindFromPrefix(resource), name: rawFromPrefix(resource) }
      : {
        id: resource.id,
        kind: resource.kind,
        name: resource.name,
        description: resource.description,
        project: resource.project,
        path: resource.path,
        bindingKey: resource.bindingKey,
        alias: resource.alias,
        capabilities: resource.capabilities,
        reasoning_efforts: resource.reasoning_efforts,
        pin: resource.pin,
      };
    return post('/project/resources', row);
  },
  removeFromProject: (projectId, resourceId) =>
    del(`/project/resources?id=${encodeURIComponent(resourceId)}`),
  pinToProject: (parentId, pin) => post('/project/resources/pins', { id: parentId, ...pin }),
  unpinFromProject: (parentId, pin) => {
    const q = new URLSearchParams({ id: parentId });
    if (pin && pin.path) q.set('path', pin.path);
    if (pin && pin.database) q.set('database', pin.database);
    if (pin && pin.schema) q.set('schema', pin.schema);
    if (pin && pin.table) q.set('table', pin.table);
    return del(`/project/resources/pins?${q.toString()}`);
  },
  assetFiles: (datasetId) => request(`/project/assets/${encodeURIComponent(datasetId)}/files`),
  attachDatasetFile: (datasetId, path) =>
    post(`/project/assets/${encodeURIComponent(datasetId)}/files/attach`, { path }),
  dataSourceDatabases: (sourceId) => request(`/data-sources/${encodeURIComponent(sourceId)}/databases`),
  dataSourceSchemas: (sourceId, database) =>
    request(`/data-sources/${encodeURIComponent(sourceId)}/schemas?database=${encodeURIComponent(database || '')}`),
  dataSourceTables: (sourceId, database, schema) =>
    request(`/data-sources/${encodeURIComponent(sourceId)}/tables?database=${encodeURIComponent(database || '')}&schema=${encodeURIComponent(schema || '')}`),
  promoteScratch: (path, datasetId) => post('/project/scratch/promote', { path, dataset: datasetId }),

  conversationContext: async (id) => {
    const ctx = await request(`/threads/${id}/context`);
    return (ctx.items || []).map((item) => ({
      id: item.id,
      resourceId: item.resourceId
        || (item.path ? `file:${item.path}` : (item.bindingKey ? item.bindingKey.join(':') : item.id)),
      resourceName: item.name,
      resourceKind: item.kind === 'data_source' && item.scope && item.scope.table
        ? 'table'
        : SW.util.uiKind(item.kind),
      addedBy: item.addedBy || 'user',
      path: item.path,
      bindingKey: item.bindingKey,
      parentId: item.parentId,
      datasetId: item.datasetId,
      datasetRelPath: item.datasetRelPath,
      scope: item.scope,
    }));
  },
  addToConversation: async (id, resourceId, addedBy) => {
    const { resourceIndex } = SW.store.get();
    const resource = resourceIndex[resourceId] || {};
    const kind = resource.kind === 'table' ? 'data_source'
      : resource.kind === 'datasource' ? 'data_source'
      : resource.kind === 'model_llm' ? 'llm_alias'
      : resource.kind === 'model_predictive' ? 'model_api'
      : resource.kind || kindFromPrefix(resourceId);
    const row = await post(`/threads/${id}/context`, {
      kind,
      name: resource.name || rawFromPrefix(resourceId),
      // Recover a missing path ONLY from a `file:<path>` id, where the rest of the id IS the path.
      // A Dataset file is `dsfile:<datasetId>:<relPath>`, so stripping one prefix yields
      // `<datasetId>:<relPath>` — which reads as a path everywhere downstream and is not one. That
      // fabricated value stopped the server attaching the file, stopped it offering the Domino data
      // library route, and made the composer's @token name a file nothing could match.
      path: resource.path
        || (kind === 'file' && kindFromPrefix(resourceId) === 'file'
          ? rawFromPrefix(resourceId)
          : undefined),
      project: resource.project,
      bindingKey: resource.bindingKey,
      // For the membership row this post may write, not for the chip: a mention of a catalogue
      // parent joins it to the project (see `_join_project_on_mention`), and the join is the only
      // place these four are wanted. Sent because the catalogue row is the only thing that has
      // them — without them the join writes a model with no `alias` and no efforts, which the
      // model picker then renders as a blank option nothing can select. The server strips them
      // back off before the chip is stored.
      description: resource.description,
      alias: resource.alias,
      capabilities: resource.capabilities,
      reasoning_efforts: resource.reasoning_efforts,
      addedBy: addedBy || 'user',
      resourceId,
      parentId: resource.parentId,
      datasetId: resource.datasetId,
      datasetRelPath: resource.datasetRelPath,
      datasetName: resource.datasetName,
      scope: resource.scope,
    });
    return {
      id: row.id,
      resourceId: row.resourceId || resourceId,
      resourceName: row.name,
      resourceKind: resource.kind || 'file',
      addedBy: row.addedBy || addedBy || 'user',
      path: row.path,
      bindingKey: row.bindingKey,
      parentId: row.parentId || resource.parentId,
      datasetId: row.datasetId || resource.datasetId,
      datasetRelPath: row.datasetRelPath || resource.datasetRelPath,
      scope: row.scope || resource.scope,
      // Set only when THIS post is what made the resource a project member. The store refreshes
      // the rail off it, so dropping it here would leave the panel denying a join that happened.
      joinedProject: Boolean(row.joinedProject),
    };
  },
  removeFromConversation: (id, attachmentId) => del(`/threads/${id}/context/${attachmentId}`),

  threads: async () => request('/threads'),
  thread: (id) => request(`/threads/${id}`),
  createThread: () => post('/threads', {}),
  flushChat: () => post('/threads/save', {}),
  draftHandoffPlan: (id) => post(`/threads/${id}/handoff/plan`, {}),
  confirmHandoff: (id, include, target) => post(`/threads/${id}/handoff/confirm`, { include, target }),
  // Change on the plan card: the same crossing, different answers (#60). No `target` — which
  // Built App a handoff lands in is decided once, on the sheet (ADR-0008). `planId` says which
  // of this Conversation's handoffs the card belongs to, since it may have made several.
  recrossHandoff: (id, include, planId) =>
    post(`/threads/${id}/handoff/recross`, { include, planId: planId || '' }),
  patchThread: (id, body) => patch(`/threads/${id}`, body),
  deleteThread: (id) => del(`/threads/${id}`),
  sendMessage: () => Promise.reject(new Error('use chatStream')),
  advance: async () => ({ message: { blocks: [] } }),
  appendMessage: async () => ({}),
  saveToProject: async () => ({}),
  upload: async () => ({}),

  charts: () => Promise.resolve({}),
  starters: () => Promise.resolve({ chat: { 'cross-industry': [], 'financial-services': [] } }),

  // The plan the app is being built from: `{title, markdown, status, steps}`, or `{}` when there
  // is none. `status` is 'awaiting' while .sage/plan.md is live and 'built' once a build has
  // archived it. Markdown, because plan.md is markdown — the group below reads the same plan as a
  // document, which is a different question and outlives the build this one describes.
  projectPlan: () => request('/project/plan').catch(() => ({})),

  plans: () => request('/plans').then((r) => r.items || []),
  plan: (id) => request(`/plans/${encodeURIComponent(id)}`),
  planMarkdown: (id) => request(`/plans/${encodeURIComponent(id)}/markdown`),
  createPlan: (body) => post('/plans', body),
  patchPlan: (id, body) => patch(`/plans/${encodeURIComponent(id)}`, body),
  review: (id, body) => post(`/plans/${encodeURIComponent(id)}/review`, body),

  handoff: (payload) => post('/handoff', payload),

  // The Built Apps in this Project — the Build rail's list, as /threads is the Chat rail's. A
  // directory scan on the server: there is no index file to keep in step (ADR-0008).
  apps: () => request('/apps').then((r) => r.items || []),
  // Point Build at another app. 409 while a build is streaming; the caller shows what it says.
  selectApp: (id) => post(`/apps/${encodeURIComponent(id)}/select`, {}),
  // Only the name is writable. The id names the app's directory and never changes.
  patchApp: (id, body) => patch(`/apps/${encodeURIComponent(id)}`, body),
  // New app in the Build rail: minted, seeded and selected server-side, with no Thread and no plan
  // behind it. 409 while a build is streaming, because a turn holds one working tree.
  createApp: () => post('/apps', {}),
  // Take a Built App out of the Project. `deleteDominoApp` is the answer to the offer a published
  // app earns — the default is the one that destroys less, because a Domino App that is still
  // serving can still be deleted and one that is gone cannot come back.
  deleteApp: (id, { deleteDominoApp = false } = {}) =>
    del(`/apps/${encodeURIComponent(id)}?domino_app=${deleteDominoApp ? 'delete' : 'keep'}`),
  // Publish (or republish) the SELECTED Built App as a live Domino App (#89). No id on the wire:
  // the server publishes the app Build is pointed at, and an id travelling beside it would be a
  // second answer to "which app" — shipping over the wrong one is the failure #70 exists to stop.
  // Answers {published, app_id, url, manage_url, republished}; a 409 carries `refused` problems
  // and every other failure carries a sentence, both of which `request` already surfaces.
  publish: () => post('/publish', {}),
  // The two reads the pre-publish notice is built from (#35). Separate routes, asked together and
  // neither awaited before the confirm opens: `publishCheck` is local disk and pure Python, and
  // `publishEgress` may reach the gateway for the Alias listing — folded into one, a slow listing
  // would hold up query warnings that were already sitting on the disk.
  //
  // Both are reads, and neither can refuse a publish (ADR-0012). A failure here is nothing to say,
  // which is why the caller swallows it rather than reporting it: an unverified credential is a
  // hole, an unwritten notice is not.
  publishCheck: () => request('/publish-check'),
  publishEgress: () => request('/publish-egress'),
  // Both halves of one Conversation, merged and labelled with the half each turn came from (#56).
  // Beside `thread(id).history`, not instead of it: the split conversation view still asks Chat
  // what Chat said, and this asks what the whole Conversation did — a question about the Project's
  // Built Apps too, which is why the server scans them all rather than reading the selected one.
  conversation: (threadId) =>
    request(`/threads/${encodeURIComponent(threadId)}/conversation`).then((r) => r.history || []),
  appConversations: () => empty(),

  addDecision: async () => ({}),
  removeDecision: async () => ({}),
  holdBuild: async () => ({}),
  releaseBuild: async () => ({}),

  // Build's transcript is per conversation, the way Chat's already was. No conversation
  // means the whole project — the agent's view, not the rail's.
  history: (conversation) =>
    request(`/project/history${conversation ? `?conversation=${encodeURIComponent(conversation)}` : ''}`),
  // The SELECTED Built App's whole build log, every conversation that drove it included (#88).
  // The same route, asked the other question it has always been able to answer and nobody asked:
  // no conversation on the wire, deliberately. One Conversation can drive several apps (#72), so a
  // history filtered to one would leave out the builds that made this app what it is — and the log
  // is never another app's, because it lives in the app's own directory (ADR-0008).
  //
  // Its own entry rather than a bare `history()`: the rule that this call names no conversation is
  // the whole of what tells the two questions apart, and a rule kept in the caller is a rule the
  // next caller does not read.
  appHistory: () => request('/project/history').then((r) => r.history || []),
  bindings: () => request('/bindings'),
  // The two app-scoped removals (ADR-0011). Both answer with the app source that STILL uses what
  // just went — read by the route before the record goes, because a Data Source's queries are found
  // through the record — so neither caller has to scan anything to report it.
  unbind: (kind, resourceId) =>
    del(`/bindings/${encodeURIComponent(kind)}/${encodeURIComponent(resourceId)}`),
  detachFile: (path) => post('/project/files/detach', { path }),
  buildState: () => request('/project/build/state'),
  setBuildMode: (mode) => post('/project/model', { mode }),
  // Build's model override. `pick` alone, without `mode`: ModelControl.pick is mode-independent
  // and the router reads it only in Plan and Implement, so sending the mode too would re-assert a
  // standing choice the picker never touched.
  setBuildModel: (pick) => post('/project/model', { pick: pick || null }),
  // The model panel's two calls (ADR-0017). These write an ASSIGNMENT — the Project's standing
  // choice, persisted and shared — which is a different thing from `setBuildModel` above, and the
  // reason they are not folded together. `null` clears one, putting the slot back on the
  // deployment default; the backend tells that from a slot nobody mentioned.
  modelAssignments: () => request('/project/model/assignments'),
  setModelAssignment: (slot, model) =>
    post('/project/model', { catalog: { [slot]: model || null } }),
  setChatModel: (chat_model, reasoning_effort) =>
    post('/project/model', { chat_model: chat_model || null, reasoning_effort: reasoning_effort || null }),
  // The Conversation is optional and is what makes Undo on a handoff card readable after a
  // reload (#60): the card is rebuilt from the transcript, so the cancel has to leave a row there.
  cancelPlan: (body) => post('/project/plan/cancel', body || {}),
  // `target` is `{kind, conversation}` — the turn this Stop was aimed at (#126). Sending it is
  // what makes a mis-aimed press a no-op instead of a killed question: the queue can hand the lock
  // on between the press and the POST. Omitting it means "stop whatever is running".
  stopBuild: (target) => post('/project/build/stop', target || {}),
  // Stop and Cancel are not the same control (#79). Stop interrupts the turn that is RUNNING;
  // this drops one that is still waiting in line, and leaves the running one alone. The ticket
  // comes from the `pending` event that queued turn's own stream yielded.
  cancelTurn: (ticket) => post('/project/turn/cancel', { ticket }),
  // Puts the SELECTED app's code back to the starter template (#36, narrowed to one Built App in
  // #75). Attachments, Resources, the transcript and every other app survive it — see
  // Orchestrator.reset_app.
  resetApp: () => post('/project/reset'),
  // Pull a teammate's changes into the workspace and push the merged result, so the repo and the
  // Project agree (#78). Conflicts are resolved by the agent on the way through, so this can take
  // a model turn — see Orchestrator.sync.
  syncProject: () => post('/project/sync'),

  buildSteps: () => empty(),
  buildRuns: () => empty(),
  buildRun: async () => ({}),
  buildComplete: async () => ({}),
  buildFiles: () => empty(),
  buildPreview: async () => ({ url: './preview/' }),
  // Off `BASE` on purpose: /healthz sits outside /api (app.py's _UNPROXIED), and it is the only
  // route that says which open-weight models this gateway will accept as an override.
  health: async () => {
    const res = await fetch('./healthz');
    if (!res.ok) throw new Error(`${res.status} ${res.statusText}`);
    return res.json();
  },

  usage: async () => ({ rows: [] }),
  usageDimensions: async () => ({ dimensions: [] }),
  monitoring: async () => ({ apps: [] }),

  facets: async () => ({ tags: [], kinds: [] }),
  remix: async () => ({}),
  requestAccess: async () => ({}),

  // The project's collaborators. Empty off Domino, and empty when the record can't be read —
  // the plan page then shows ids where it would show names rather than failing to load.
  members: () => request('/members').catch(() => ({ members: [], directory: [] })),
  invite: async () => ({}),
  activity: async () => [],
  notifications: () => empty(),
  readNotification: async () => ({}),
  readAllNotifications: async () => ({}),

  cli: async () => ({ devices: [] }),
  revokeDevice: async () => ({}),

  search: async () => ({ results: [] }),
};
