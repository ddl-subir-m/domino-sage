window.SW = window.SW || {};

(function () {
  const { createElement: h } = React;

  // The world's "today". Fixture dates are written against this, so relative
  // times read correctly no matter when the prototype is demoed.
  const TODAY = new Date();

  const RESOURCE_META = {
    plan:             { icon: '📋', label: 'plan',           group: 'artifacts' },
    dataset:          { icon: '📦', label: 'dataset',        group: 'data' },
    table:            { icon: '▦',  label: 'database table', group: 'data' },
    datasource:       { icon: '🔌', label: 'data source',    group: 'data' },
    model_llm:        { icon: '🧠', label: 'model',          group: 'models' },
    model_predictive: { icon: '🤖', label: 'predictive model', group: 'models' },
    tool:             { icon: '🔧', label: 'tool',           group: 'tools' },
    agent:            { icon: '✨', label: 'agent',          group: 'agents' },
    file:             { icon: '📄', label: 'file',           group: 'files' },
    artifact:         { icon: '🖼', label: 'artifact',       group: 'artifacts' },
    skill:            { icon: '📘', label: 'skill',          group: 'skills' },
    mcp:              { icon: '🧩', label: 'MCP',            group: 'mcp' },
  };

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
    PLAN_STATUS,
    APP_STATUS,

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

    // The Chat explorer is the project's pickable working set, not the repo.
    isHiddenFromExplorer(path) {
      const p = String(path || '').replace(/^\.\//, '');
      if (!p) return false;
      const base = p.split('/').pop();
      if (base === 'AGENTS.md') return true;
      if (p === '.sage' || p.startsWith('.sage/')) return true;
      return false;
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
      { title: rationale || 'Picked for you by Sage.' },
      h(antd.Tag, { className: 'sw-prov sw-prov-sage', bordered: false }, 'Sage')
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
