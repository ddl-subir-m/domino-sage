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
      data_sources: 'Could not list Data Sources.',
      llm_aliases: 'Could not list language models.',
      model_apis: 'Could not list Model APIs.',
    } })),
    request('/assets').catch(() => ({ assets: [], error: 'Could not list Datasets.' })),
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
    const { resourceIndex } = SW.store.get();
    return Promise.resolve(resourceIndex[id] || { id, name: rawFromPrefix(id), kind: kindFromPrefix(id) });
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
          originName: r.project || 'Domino',
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
    };
  },
  removeFromConversation: (id, attachmentId) => del(`/threads/${id}/context/${attachmentId}`),

  appRequires: () => empty(),
  addRequirement: async () => ({}),
  removeRequirement: async () => ({}),

  threads: async () => request('/threads'),
  thread: (id) => request(`/threads/${id}`),
  createThread: () => post('/threads', {}),
  flushChat: () => post('/threads/save', {}),
  draftHandoffPlan: (id) => post(`/threads/${id}/handoff/plan`, {}),
  confirmHandoff: (id, include) => post(`/threads/${id}/handoff/confirm`, { include }),
  patchThread: (id, body) => patch(`/threads/${id}`, body),
  touchApp: async () => ({ touched: [] }),
  deleteThread: (id) => del(`/threads/${id}`),
  sendMessage: () => Promise.reject(new Error('use chatStream')),
  advance: async () => ({ message: { blocks: [] } }),
  appendMessage: async () => ({}),
  saveToProject: async () => ({}),
  upload: async () => ({}),

  charts: () => Promise.resolve({}),
  starters: () => Promise.resolve({ chat: { 'cross-industry': [], 'financial-services': [] } }),

  plans: () => empty(),
  plan: async () => ({}),
  planMarkdown: async () => ({ markdown: '' }),
  createPlan: async () => ({}),
  patchPlan: async () => ({}),
  review: async () => ({}),

  handoff: (payload) => post('/handoff', payload),

  apps: () => empty(),
  app: async () => ({}),
  createApp: async () => ({}),
  appConversations: () => empty(),
  patchApp: async () => ({}),
  publish: async () => ({}),

  addDecision: async () => ({}),
  removeDecision: async () => ({}),
  holdBuild: async () => ({}),
  releaseBuild: async () => ({}),

  // Build's transcript is per conversation, the way Chat's already was. No conversation
  // means the whole project — the agent's view, not the rail's.
  history: (conversation) =>
    request(`/project/history${conversation ? `?conversation=${encodeURIComponent(conversation)}` : ''}`),
  bindings: () => request('/bindings'),
  buildState: () => request('/project/build/state'),
  setBuildMode: (mode) => post('/project/model', { mode }),
  setChatModel: (chat_model, reasoning_effort) =>
    post('/project/model', { chat_model: chat_model || null, reasoning_effort: reasoning_effort || null }),
  cancelPlan: () => post('/project/plan/cancel'),
  stopBuild: () => post('/project/build/stop'),

  buildSteps: () => empty(),
  buildRuns: () => empty(),
  buildRun: async () => ({}),
  buildComplete: async () => ({}),
  buildFiles: () => empty(),
  buildPreview: async () => ({ url: './preview/' }),

  usage: async () => ({ rows: [] }),
  usageDimensions: async () => ({ dimensions: [] }),
  monitoring: async () => ({ apps: [] }),

  facets: async () => ({ tags: [], kinds: [] }),
  remix: async () => ({}),
  requestAccess: async () => ({}),

  members: async () => ({ members: [], directory: [] }),
  invite: async () => ({}),
  activity: async () => [],
  notifications: () => empty(),
  readNotification: async () => ({}),
  readAllNotifications: async () => ({}),

  cli: async () => ({ devices: [] }),
  revokeDevice: async () => ({}),

  search: async () => ({ results: [] }),
};
