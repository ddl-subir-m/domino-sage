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
    }];
  },
  createProject: async () => {
    throw new Error('Named apps are created from the Sage hub, not from this project.');
  },
  resources: async () => {
    const [res, assets, project] = await Promise.all([
      request('/resources').catch(() => ({ data_sources: [], llm_aliases: [], model_apis: [] })),
      request('/assets').catch(() => ({ assets: [] })),
      request('/project').catch(() => ({ attached: [] })),
    ]);
    const groups = {
      dataset: (assets.assets || []).map((a) => ({
        id: `dataset:${a.id}`, name: a.name, kind: 'dataset',
      })),
      table: [],
      datasource: (res.data_sources || []).map((d) => ({
        id: `data_source:${d.id}`,
        name: d.name,
        kind: 'datasource',
        bindingKey: ['data_source', d.id],
      })),
      model_llm: (res.llm_aliases || []).map((a) => ({
        id: `llm_alias:${a.id}`,
        name: a.display_name || a.name,
        kind: 'model_llm',
        bindingKey: ['llm_alias', a.id],
      })),
      model_predictive: (res.model_apis || []).map((m) => ({
        id: `model_api:${m.id}`,
        name: m.name || m.display_name,
        kind: 'model_predictive',
        bindingKey: ['model_api', m.id],
      })),
      tool: [],
      agent: [],
      skill: [],
      file: (project.attached || []).map((e) => ({
        id: `file:${e.path}`,
        name: (e.path || '').split('/').pop(),
        kind: 'file',
        path: e.path,
      })),
    };
    return { groups };
  },
  resource: (id) => {
    const { resourceIndex } = SW.store.get();
    return Promise.resolve(resourceIndex[id] || { id, name: rawFromPrefix(id), kind: kindFromPrefix(id) });
  },
  restrictedIn: () => Promise.resolve([]),

  catalog: async () => ({ items: [], facets: {} }),
  addToProject: async () => ({ added: false }),
  removeFromProject: async () => ({ removed: false }),

  conversationContext: async (id) => {
    const ctx = await request(`/threads/${id}/context`);
    return (ctx.items || []).map((item) => ({
      id: item.id,
      resourceId: item.path
        ? `file:${item.path}`
        : (item.bindingKey ? item.bindingKey.join(':') : item.id),
      resourceName: item.name,
      resourceKind: item.kind === 'data_source' ? 'datasource'
        : item.kind === 'llm_alias' ? 'model_llm'
        : item.kind === 'model_api' ? 'model_predictive'
        : item.kind || 'file',
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
      bindingKey: resource.bindingKey,
      addedBy: addedBy || 'user',
    });
    return {
      id: row.id,
      resourceId,
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
