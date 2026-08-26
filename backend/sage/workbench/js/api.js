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
      try {
        const payload = await res.json();
        if (payload && (payload.detail || payload.error || payload.message)) {
          detail = payload.detail || payload.error || payload.message;
          if (typeof detail === 'object') detail = detail.message || JSON.stringify(detail);
        }
      } catch (parseError) {
        // Response wasn't JSON; the status line is the best we have.
      }
      throw new Error(detail);
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
      })),
      datasource: (res.data_sources || []).map((d) => ({
        id: `data_source:${d.id}`,
        name: d.name,
        kind: 'datasource',
        description: d.connector || '',
        bindingKey: ['data_source', d.id],
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
    tool: [], agent: [], skill: [], mcp: [], file: [],
  };
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
  };
}

function groupsFromMembership(items, attached) {
  const groups = emptyResourceGroups();
  (items || []).forEach((item) => {
    const row = rowFromMember(item);
    if (!groups[row.kind]) return;
    groups[row.kind].push(row);
  });
  groups.file = (attached || [])
    .filter((e) => !SW.util.isHiddenFromExplorer(e.path))
    .map((e) => ({
      id: `file:${e.path}`,
      name: (e.path || '').split('/').pop(),
      kind: 'file',
      path: e.path,
    }));
  return groups;
}

function overlayListing(groups, listing) {
  const next = { ...groups };
  ['dataset', 'datasource', 'model_llm', 'model_predictive'].forEach((kind) => {
    const liveById = {};
    ((listing.groups || {})[kind] || []).forEach((r) => { liveById[r.id] = r; });
    next[kind] = (groups[kind] || []).map((row) => (liveById[row.id] ? { ...row, ...liveById[row.id] } : row));
  });
  return next;
}

SW.api = {
  me: () => request('/me'),
  project: () => request('/project'),

  projects: async () => {
    const p = await request('/project');
    return [{
      id: p.id,
      name: p.name || p.id,
      color: '#543FDE',
      ephemeral: false,
      untitled: !!p.untitled,
      ownerName: 'you',
      memberCount: 1,
      appCount: 1,
      planCount: 0,
      model: p.model,
    }];
  },
  createProject: async () => {
    throw new Error('Named apps are created from the Sage hub, not from this project.');
  },
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
      };
    return post('/project/resources', row);
  },
  removeFromProject: (projectId, resourceId) =>
    del(`/project/resources?id=${encodeURIComponent(resourceId)}`),

  conversationContext: async (id) => {
    const ctx = await request(`/threads/${id}/context`);
    return (ctx.items || []).map((item) => ({
      id: item.id,
      resourceId: item.resourceId
        || (item.path ? `file:${item.path}` : (item.bindingKey ? item.bindingKey.join(':') : item.id)),
      resourceName: item.name,
      resourceKind: SW.util.uiKind(item.kind),
      addedBy: item.addedBy || 'user',
      path: item.path,
      bindingKey: item.bindingKey,
    }));
  },
  addToConversation: async (id, resourceId, addedBy) => {
    const { resourceIndex } = SW.store.get();
    const resource = resourceIndex[resourceId] || {};
    const kind = resource.kind === 'datasource' ? 'data_source'
      : resource.kind === 'model_llm' ? 'llm_alias'
      : resource.kind === 'model_predictive' ? 'model_api'
      : resource.kind || kindFromPrefix(resourceId);
    const row = await post(`/threads/${id}/context`, {
      kind,
      name: resource.name || rawFromPrefix(resourceId),
      path: resource.path || (kind === 'file' ? rawFromPrefix(resourceId) : undefined),
      project: resource.project,
      bindingKey: resource.bindingKey,
      addedBy: addedBy || 'user',
      resourceId,
    });
    return {
      id: row.id,
      resourceId: row.resourceId || resourceId,
      resourceName: row.name,
      resourceKind: resource.kind || 'file',
      addedBy: row.addedBy || addedBy || 'user',
      path: row.path,
      bindingKey: row.bindingKey,
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

  history: () => request('/project/history'),
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
