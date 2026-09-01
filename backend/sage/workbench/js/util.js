window.SW = window.SW || {};

(function () {
  const { createElement: h } = React;

  // The world's "today". Fixture dates are written against this, so relative
  // times read correctly no matter when the prototype is demoed.
  const TODAY = new Date();

  // The two rows the pack renames read their label through a getter rather than holding a string:
  // this file is evaluated before /api/brand answers, and `RESOURCE_META[kind].label` is read
  // directly as well as through `labelFor`, so read time is the only point that covers both.
  const RESOURCE_META = {
    plan:             { icon: '📋', label: 'plan',           group: 'artifacts' },
    dataset:          { icon: '📦', group: 'data', get label() { return SW.brand.text('{dataset}'); } },
    table:            { icon: '▦',  label: 'database table', group: 'data' },
    datasource:       { icon: '🔌', group: 'data', get label() { return SW.brand.text('{dataSource}'); } },
    model_llm:        { icon: '🧠', label: 'model',          group: 'models' },
    model_predictive: { icon: '🤖', label: 'predictive model', group: 'models' },
    tool:             { icon: '🔧', label: 'tool',           group: 'tools' },
    agent:            { icon: '✨', label: 'agent',          group: 'agents' },
    file:             { icon: '📄', label: 'file',           group: 'files' },
    artifact:         { icon: '🖼', label: 'artifact',       group: 'artifacts' },
    skill:            { icon: '📘', label: 'skill',          group: 'skills' },
    mcp:              { icon: '🧩', label: 'MCP',            group: 'mcp' },
  };

  // The Resource kinds that get a membership row of their own, in the UI id space. A Dataset file
  // and a warehouse table hang off one of these and have no row. The same four in Domino's own
  // spelling are `_MEMBERSHIP_PARENT_KINDS` in `sage/orchestrator/service.py`; `uiKind` below is
  // the mapping between the two.
  //
  // Shared because two surfaces have to agree on it exactly: the store keeps the non-member ones
  // out of the rail so the @ menu can offer them, and the composer draws the group it keeps. A
  // kind in one list and not the other is either a row held and never shown, or shown and never
  // held — and both look like the menu forgetting things.
  const MEMBERSHIP_PARENT_KINDS = ['dataset', 'datasource', 'model_llm', 'model_predictive'];

  const PLAN_STATUS = {
    draft:      { label: 'Draft',      color: 'default' },
    in_review:  { label: 'In review',  color: 'blue' },
    approved:   { label: 'Approved',   color: 'green' },
    building:   { label: 'Building',   color: 'purple' },
    shipped:    { label: 'Shipped',    color: 'success' },
    superseded: { label: 'Superseded', color: 'default' },
    failed:     { label: 'Failed',     color: 'error' },
  };

  const APP_STATUS = {
    draft:    { label: 'Draft' },
    building: { label: 'Building' },
    running:  { label: 'Running' },
    stopped:  { label: 'Stopped' },
    failed:   { label: 'Failed' },
  };

  SW.util = {
    TODAY,
    RESOURCE_META,
    MEMBERSHIP_PARENT_KINDS,
    PLAN_STATUS,
    APP_STATUS,

    // Resolve a host-relative Domino path against the MAIN Domino host.
    //
    // The server hands these out with no host on purpose: DOMINO_API_HOST is the internal cluster
    // address, so the only browser-reachable host it can name is the one this page came from. That
    // is right in a Builder workspace, which is served from the main host. The published Workbench
    // App is served from apps.<host>, where the same path would resolve against the apps origin and
    // 404 — so the `apps.` label comes off first. Same rule as door.html's builderUrl().
    //
    // An absolute URL is somebody's deliberate override and passes through untouched.
    mainHostUrl(path) {
      if (!path || /^https?:\/\//i.test(path)) return path;
      const host = window.location.hostname;
      if (host.indexOf('apps.') === 0) {
        return `${window.location.protocol}//${host.slice(5)}${path}`;
      }
      return path;
    },

    // Which LLM Aliases can hold a conversation. Here rather than in either caller because both
    // the Chat picker and the model panel offer the same list, and two copies of this rule is how
    // they come to disagree about what a person may pick (ADR-0017).
    chatCapable(aliases) {
      return (aliases || []).filter((a) => {
        const caps = a.capabilities || [];
        return !(caps.includes('embeddings') && !caps.includes('chat'));
      });
    },

    // What every control that offers a one-click act does with a click: mark which button is
    // running so the row can show a spinner and disable its siblings, report the failure where the
    // person is looking, and let go either way. Here rather than beside one of them because two
    // surfaces now draw the same acts — the transcript's refusal card (#135) and the composer's
    // warning (#136) — and the copies were already at three when the second surface asked.
    // Returns `[busy, run]`: `busy` is the running button's key or '', `run(key, fn)` an onClick.
    useBusyAct() {
      const [busy, setBusy] = React.useState('');
      const run = (key, fn) => () => {
        setBusy(key);
        // `new Promise` and not `Promise.resolve(fn())`: that form calls `fn` BEFORE the chain
        // exists, so a synchronous throw goes straight past the catch — leaving `busy` set, every
        // button in the row disabled for good, and no sentence saying why. Two of the four mention
        // acts return synchronously, so the throw that does this has a caller.
        new Promise((resolve) => resolve(fn()))
          .catch((err) => antd.message.error(String(err.message || err)))
          .finally(() => setBusy(''));
      };
      return [busy, run];
    },

    iconFor(kind) {
      return (RESOURCE_META[kind] || RESOURCE_META.file).icon;
    },

    labelFor(kind) {
      return (RESOURCE_META[kind] || RESOURCE_META.file).label;
    },

    uiKind(kind) {
      if (kind === 'data_source') return 'datasource';
      if (kind === 'llm_alias') return 'model_llm';
      if (kind === 'model_api') return 'model_predictive';
      return kind || 'file';
    },

    // A Binding, in the id space everything else keys a Resource on.
    //
    // A Binding records the BARE Domino id beside its kind (`{kind: 'data_source', id: 'ds_1'}`),
    // while a Project Resource id and an attachment's `resourceId` both carry the kind as a prefix
    // (`data_source:ds_1`). So asking "is this row the thing the app is bound to" is a join, and a
    // join on `b.id` alone matches nothing — silently, and looking exactly like the empty state.
    //
    // Lives here because three surfaces now have to agree on it: the panel's "Required by {app}"
    // subtitle, its "In this app" rows, and the sentence `removeFromConversation` draws when a chip
    // leaves a Conversation the app still depends on. This is the same join `api.js` already makes
    // when it recovers a missing `resourceId` from `bindingKey.join(':')`.
    bindingId(binding) {
      return `${binding.kind}:${binding.id}`;
    },

    // What the Build header and the panel's app section both say when the selected app records
    // neither Bindings nor Attachments. One sentence in one place, because the two surfaces had
    // drifted into two answers to one question (ADR-0011) — the header's runs on from "{app}
    // ships" and the panel's stands alone, so only the lead differs.
    //
    // It names the handoff rather than an upload: `_promote_chat_file` writes the Attachment and
    // `_bind_from_handoff` the Bindings, while the composer's upload writes a scratch file and a
    // Conversation chip. A sentence naming the upload would leave a first-timer doing as they were
    // told and seeing neither list change.
    appScopeEmpty(lead) {
      return `${lead} Resources and data files from Chat land here after Open Builder.`;
    },

    // The "@token" one row is named by. Prefer the file's basename so "@data.csv" matches the path
    // OpenCode reads. Lives here rather than in the composer because the menu that INSERTS a token
    // and the turn that reads it back off the prompt have to derive it the same way — a token only
    // one of them can produce is a mention that silently carries nothing (see store.collectTurnRefs).
    mentionToken(resource) {
      const fromPath = String((resource && resource.path) || '').split('/').pop();
      const fromName = String((resource && resource.name) || '').split('/').pop();
      const token = (fromPath || fromName || 'resource').replace(/\s+/g, '_').replace(/^@+/, '');
      return '@' + token;
    },

    // Whether "@<token>" still stands in the text as its own word. Punctuation may follow it —
    // people write "@data.csv, please" — but a longer name must not match a shorter one's prefix.
    mentionedIn(text, token) {
      const bare = String(token || '').replace(/^@+/, '');
      if (!bare) return false;
      const escaped = bare.replace(/[.*+?^${}()|[\]\\]/g, '\\$&');
      return new RegExp(`(^|\\s)@${escaped}(?=[\\s,.;:!?)\\]}'"]|$)`).test(String(text || ''));
    },

    // The Chat explorer is the project's pickable working set, not the repo.
    isHiddenFromExplorer(path) {
      const p = String(path || '').replace(/^\.\//, '');
      if (!p) return false;
      const base = p.split('/').pop();
      if (base === 'AGENTS.md') return true;
      if (p === '.sage/scratch' || p.startsWith('.sage/scratch/')) return false;
      if (p === '.sage' || p.startsWith('.sage/')) return true;
      return false;
    },

    // The order a menu offers a Resource in: what this Project already holds, then the wider Domino
    // catalogue behind it. `groups` is the working set's own groups in the caller's order, and
    // `catalogue` is what the Project has not joined — always LAST, because it is the only group
    // whose rows are not here already, and picking one joins the Project on the way in (ADR-0018).
    //
    // Shared because two menus draw the same choice and one of them had it inline: the composer's @
    // menu and the Build header's picker (ADR-0021). The picker was written from the menu's shape,
    // and a second copy of the ordering is how the two come to disagree about where a Resource is —
    // which is the one thing a person carries from one surface to the other.
    //
    // One row per id, the first occurrence winning, so a Resource two groups both hold is offered by
    // the nearer one. `query` matches the name or the file's basename, because the basename is what
    // `mentionToken` builds a token from — asking the same question of the same string keeps what a
    // person typed and what the menu keeps from drifting apart.
    workingSetFirst({ groups, catalogue, query, limit }) {
      const lowered = String(query || '').trim().toLowerCase();
      const matches = (row) => {
        if (!lowered) return true;
        const name = String(row.name || '').toLowerCase();
        const base = String(row.path || '').split('/').pop().toLowerCase();
        return name.includes(lowered) || base.includes(lowered);
      };
      const seen = new Set();
      const out = [];
      [...(groups || []), catalogue || []].forEach((group) => {
        (group || []).forEach((row) => {
          if (!row || !row.id || seen.has(row.id) || !matches(row)) return;
          seen.add(row.id);
          out.push(row);
        });
      });
      return limit ? out.slice(0, limit) : out;
    },

    thumbUrl(name) {
      return `./img/thumbs/${name || 'thumb-dashboard.svg'}`;
    },

    // Formatting ---------------------------------------------------------

    money(value, digits) {
      const decimals = digits === undefined ? 2 : digits;
      return `$${Number(value || 0).toLocaleString('en-US', {
        minimumFractionDigits: decimals,
        maximumFractionDigits: decimals,
      })}`;
    },

    compactMoney(value) {
      const n = Number(value || 0);
      if (n >= 1000000) return `$${(n / 1000000).toFixed(1)}M`;
      if (n >= 1000) return `$${(n / 1000).toFixed(1)}K`;
      return `$${n.toFixed(2)}`;
    },

    number(value) {
      return Number(value || 0).toLocaleString('en-US');
    },

    compactNumber(value) {
      const n = Number(value || 0);
      if (n >= 1000000000) return `${(n / 1000000000).toFixed(1)}B`;
      if (n >= 1000000) return `${(n / 1000000).toFixed(1)}M`;
      if (n >= 1000) return `${(n / 1000).toFixed(1)}K`;
      return String(n);
    },

    percent(value, digits) {
      return `${Number(value || 0).toFixed(digits === undefined ? 0 : digits)}%`;
    },

    // "2 hours ago" inside 7 days, an absolute date beyond it — per the
    // Domino writing guidelines.
    relativeTime(iso) {
      if (!iso) return '';
      const then = new Date(iso);
      const diffMs = TODAY - then;
      const minutes = Math.round(diffMs / 60000);
      if (minutes < 1) return 'just now';
      if (minutes < 60) return `${minutes} min ago`;
      const hours = Math.round(minutes / 60);
      if (hours < 24) return `${hours} ${hours === 1 ? 'hour' : 'hours'} ago`;
      const days = Math.round(hours / 24);
      if (days === 1) return 'yesterday';
      if (days < 7) return `${days} days ago`;
      return then.toLocaleDateString('en-US', { month: 'long', day: 'numeric', year: 'numeric' });
    },

    shortDate(iso) {
      if (!iso) return '';
      return new Date(iso).toLocaleDateString('en-US', { month: 'short', day: 'numeric' });
    },

    isoDaysAgo(days) {
      const d = new Date(TODAY);
      d.setDate(d.getDate() - days);
      return d.toISOString().slice(0, 10);
    },

    // Groups a thread list the way the rail displays it.
    groupThreads(threads) {
      const buckets = [
        { key: 'pinned', label: 'Pinned', items: [] },
        { key: 'today', label: 'Today', items: [] },
        { key: 'week', label: 'Previous 7 days', items: [] },
        { key: 'month', label: 'Previous 30 days', items: [] },
        { key: 'older', label: 'Older', items: [] },
      ];
      threads.forEach((thread) => {
        if (thread.pinned) return buckets[0].items.push(thread);
        const days = (TODAY - new Date(thread.updatedAt)) / 86400000;
        if (days < 1) return buckets[1].items.push(thread);
        if (days < 7) return buckets[2].items.push(thread);
        if (days < 30) return buckets[3].items.push(thread);
        return buckets[4].items.push(thread);
      });
      return buckets.filter((b) => b.items.length);
    },

    // What Chat says it is doing while it is doing it. The server names the work, because which
    // bash command is a Data Source query is Sage's business; the sentence is the UI's. Nothing
    // here is kept — the Thread keeps the chart and the answer, not a tool log.
    activityLabel(ev) {
      const name = String(ev.detail || '').split('/').pop();
      switch (ev.doing) {
        case 'read': return name ? `Reading ${name}…` : 'Reading the data…';
        case 'query': return name ? `Querying ${name}…` : 'Running the query…';
        case 'write': return name ? `Saving ${name}…` : 'Saving the results…';
        case 'bash': return 'Running Python…';
        case 'idle': return 'Thinking…';
        // No `doing` at all is the transcript fallback, which only ever names bash.
        default: return ev.tool === 'bash' ? 'Running Python…' : 'Thinking…';
      }
    },

    // Lightweight markdown — bold, inline code, lists, paragraphs. Enough for
    // scripted assistant copy without pulling in a parser.
    markdown(text) {
      if (!text) return null;
      const blocks = String(text).split(/\n\n+/);
      return blocks.map((block, blockIndex) => {
        const lines = block.split('\n');
        if (/^#{1,6}\s/.test(lines[0])) {
          const title = h(
            'p',
            { key: `${blockIndex}-h`, style: { fontWeight: 600, margin: '8px 0 4px' } },
            SW.util.inline(lines[0].replace(/^#{1,6}\s+/, ''))
          );
          const rest = lines.slice(1).join('\n').trim();
          if (!rest) return title;
          return h('div', { key: blockIndex }, title, SW.util.markdown(rest));
        }
        // A pipe table. Without this it falls through to a paragraph, where the newlines
        // collapse and the whole table lands as one run of pipes — worse than plain prose,
        // and what the reader sees is the product looking broken.
        // The header needs a pipe and the second line has to be the |---|---| rule: outer pipes
        // are optional both ways, since a model writes the table either way. Requiring a pipe in
        // the rule too is what keeps a paragraph followed by a --- underline out of here.
        const isRule = (l) => /^[\s:|-]+$/.test(l) && l.includes('-') && l.includes('|');
        if (lines.length >= 2 && lines[0].includes('|') && isRule(lines[1])) {
          const cells = (line) => line.trim().replace(/^\||\|$/g, '').split('|').map((c) => c.trim());
          const head = cells(lines[0]);
          return h(
            'div',
            { key: blockIndex, className: 'sw-md-table-wrap' },
            h(
              'table',
              { className: 'sw-md-table' },
              h('thead', null, h('tr', null, head.map((c, i) => h('th', { key: i }, SW.util.inline(c))))),
              h('tbody', null, lines.slice(2).map((line, r) =>
                h('tr', { key: r }, cells(line).map((c, i) => h('td', { key: i }, SW.util.inline(c))))))
            )
          );
        }
        const isOrdered = lines.length > 0 && lines.every((l) => /^\s*\d+\.\s/.test(l));
        const isBullet = lines.length > 0 && lines.every((l) => /^\s*[-*]\s/.test(l));

        if (isOrdered || isBullet) {
          const tag = isOrdered ? 'ol' : 'ul';
          return h(
            tag,
            { key: blockIndex },
            lines.map((line, i) =>
              h('li', { key: i }, SW.util.inline(line.replace(/^\s*(\d+\.|[-*])\s/, '')))
            )
          );
        }
        return h('p', { key: blockIndex }, SW.util.inline(block));
      });
    },

    inline(text) {
      const parts = String(text).split(/(\*\*[^*]+\*\*|`[^`]+`)/g);
      return parts.filter(Boolean).map((part, i) => {
        if (part.startsWith('**') && part.endsWith('**')) {
          return h('strong', { key: i }, part.slice(2, -2));
        }
        if (part.startsWith('`') && part.endsWith('`')) {
          return h('code', { key: i }, part.slice(1, -1));
        }
        return part;
      });
    },

    // Very small tokenizer so the read-only code view is not a wall of text.
    highlight(code, language) {
      const keywords = /\b(import|from|export|default|function|const|let|var|return|if|else|for|while|async|await|new|class|extends|type|interface|try|catch|throw|SELECT|FROM|WHERE|JOIN|ON|GROUP BY|ORDER BY|WITH|AS|SUM|MAX|SQRT|POWER)\b/g;
      const lines = String(code).split('\n');
      return lines.map((line, lineIndex) => {
        const trimmed = line.trimStart();
        const isComment =
          trimmed.startsWith('//') ||
          trimmed.startsWith('#') ||
          trimmed.startsWith('*') ||
          trimmed.startsWith('/*') ||
          trimmed.startsWith('--');
        if (isComment || language === 'markdown') {
          return h('div', { key: lineIndex, className: isComment ? 'sw-tok-com' : undefined }, line || ' ');
        }
        const segments = line.split(/('[^']*'|"[^"]*")/g);
        return h(
          'div',
          { key: lineIndex },
          segments.filter((s) => s !== undefined).map((segment, i) => {
            if (/^['"]/.test(segment)) return h('span', { key: i, className: 'sw-tok-str' }, segment);
            const words = segment.split(keywords);
            return words.map((word, j) =>
              keywords.test(word) && word.match(keywords)
                ? h('span', { key: `${i}-${j}`, className: 'sw-tok-key' }, word)
                : word
            );
          })
        );
      });
    },

    initialsOf(name) {
      return String(name || '?')
        .split(' ')
        .map((w) => w[0])
        .slice(0, 2)
        .join('')
        .toUpperCase();
    },

    copy(text, label) {
      const done = () => antd.message.success(label || 'Copied');
      if (navigator.clipboard && navigator.clipboard.writeText) {
        navigator.clipboard.writeText(text).then(done, () => antd.message.error('Copy failed'));
      } else {
        const area = document.createElement('textarea');
        area.value = text;
        document.body.appendChild(area);
        area.select();
        document.execCommand('copy');
        document.body.removeChild(area);
        done();
      }
    },

    sleep(ms) {
      return new Promise((resolve) => setTimeout(resolve, ms));
    },

    detectOs() {
      const ua = navigator.userAgent;
      if (/Win/i.test(ua)) return 'windows';
      if (/Linux|X11/i.test(ua) && !/Android/i.test(ua)) return 'linux';
      return 'macos';
    },
  };

  // Small shared presentational pieces used across modes ------------------

  SW.Avatar = function Avatar({ user, size = 24 }) {
    const dimension = { width: size, height: size, fontSize: Math.round(size * 0.42) };
    return h(
      'span',
      {
        className: 'sw-avatar',
        style: { ...dimension, background: (user && user.color) || '#8F8FA3' },
        title: user && user.name,
      },
      (user && user.initials) || SW.util.initialsOf(user && user.name)
    );
  };

  SW.ProvenanceBadge = function ProvenanceBadge({ addedBy, rationale }) {
    if (addedBy !== 'sage') {
      return h(
        antd.Tooltip,
        { title: 'You chose this.' },
        h(antd.Tag, { className: 'sw-prov sw-prov-you', bordered: false }, 'you')
      );
    }
    return h(
      antd.Tooltip,
      { title: rationale || `Picked for you by ${SW.brand.assistant()}.` },
      h(antd.Tag, { className: 'sw-prov sw-prov-sage', bordered: false }, SW.brand.assistant())
    );
  };

  SW.PlanStatusTag = function PlanStatusTag({ status }) {
    const meta = PLAN_STATUS[status] || PLAN_STATUS.draft;
    const props = { color: meta.color === 'default' ? undefined : meta.color, bordered: false };
    if (status === 'building') {
      return h(antd.Tag, props, h(antd.Space, { size: 4 }, h(antd.Spin, { size: 'small' }), meta.label));
    }
    return h(antd.Tag, props, meta.label);
  };

  SW.StatusDot = function StatusDot({ status }) {
    return h('span', { className: `sw-dot sw-dot-${status || 'draft'}` });
  };

  SW.Sparkline = function Sparkline({ data, width = 64, height = 18, color = '#543FDE' }) {
    const values = (data && data.length ? data : [0]).slice(-14);
    const max = Math.max(...values, 1);
    const step = values.length > 1 ? width / (values.length - 1) : width;
    const points = values
      .map((v, i) => `${(i * step).toFixed(1)},${(height - (v / max) * (height - 2) - 1).toFixed(1)}`)
      .join(' ');
    return h(
      'svg',
      { className: 'sw-sparkline', width, height, viewBox: `0 0 ${width} ${height}`, 'aria-hidden': true },
      h('polyline', {
        points,
        fill: 'none',
        stroke: color,
        strokeWidth: 1.5,
        strokeLinejoin: 'round',
        strokeLinecap: 'round',
      })
    );
  };
})();
