window.SW = window.SW || {};

// The Project's remote MCP servers in the resources panel (#640, #643), temporary until the MCP
// gateway: one row each with OpenCode's status and the tools it listed, a Project-wide switch, and
// the dialog that connects one by URL. A header value may name a secret as `{env:NAME}`, which
// OpenCode resolves when it connects, so the value itself is never in the config.
(function () {
  const { createElement: h, useState } = React;
  const { Tooltip, Dropdown, Switch, Modal, Input, Alert, Button, Select } = antd;
  const { MoreOutlined, DeleteOutlined, PlusOutlined } = icons;

  const STATUS = {
    connected: 'Connected',
    failed: 'Failed',
    disabled: 'Off',
    pending: 'Loads before the next turn',
    unknown: 'Status unknown',
  };

  SW.McpRow = function McpRow({ server }) {
    const said = STATUS[server.status] || server.status || STATUS.unknown;
    const tools = (server.tools || []).slice().sort();
    const subtitle = [server.warning ? `${said}: ${server.warning}` : said,
      `${tools.length} tool${tools.length === 1 ? '' : 's'}`].join(' · ');
    const menu = {
      items: [
        tools.length && {
          type: 'group',
          label: 'Its tools',
          children: tools.map((tool) => ({ key: `tool:${tool}`, label: tool, disabled: true })),
        },
        { key: 'reread', label: 'Read its tools again' },
        { key: 'remove', label: 'Remove from project', danger: true },
      ].filter(Boolean),
      onClick: ({ key }) => {
        if (key === 'reread') return SW.store.readMcpTools(server);
        if (key === 'remove') return SW.store.removeMcpServer(server);
        return undefined;
      },
    };
    return h(
      'div',
      { className: 'sw-res-row sw-mcp-row' },
      h(
        'span',
        { className: 'sw-res-open sw-skill-main' },
        h('span', { className: 'sw-res-icon' }, SW.util.iconNodeFor('mcp')),
        h(
          'span',
          { className: 'sw-res-main' },
          h('span', { className: 'sw-res-name-line' },
            h(Tooltip, { title: server.url || server.name, mouseEnterDelay: 0.4 },
              h('span', { className: 'sw-res-name' }, server.name))),
          h(Tooltip, { title: subtitle, mouseEnterDelay: 0.4 },
            h('span', { className: `sw-res-sub${server.status === 'failed' ? ' is-failed' : ''}` },
              subtitle))
        )
      ),
      h(
        Tooltip,
        { title: `${server.enabled ? 'On' : 'Off'} for every conversation and app in this project` },
        h(Switch, {
          size: 'small',
          className: 'sw-skill-switch',
          checked: !!server.enabled,
          'aria-label': `Use ${server.name} in this project`,
          onChange: (on) => SW.store.setMcpEnabled(server, on),
        })
      ),
      h(
        Dropdown,
        { menu, trigger: ['click'] },
        h('button', { type: 'button', className: 'sw-res-more', 'aria-label': `Actions for ${server.name}` },
          h(MoreOutlined, null))
      )
    );
  };

  // What the dialog sends: header rows with no key are dropped, and no headers at all is left out.
  SW.mcpBody = function mcpBody({ name, url, pairs }) {
    const headers = {};
    (pairs || []).forEach(({ key, value }) => {
      const k = String(key || '').trim();
      if (k) headers[k] = String(value || '').trim();
    });
    return Object.assign({ name: String(name || '').trim(), url: String(url || '').trim() },
                         Object.keys(headers).length ? { headers } : {});
  };

  const blankPair = () => ({ key: '', value: '' });

  SW.AddMcpModal = function AddMcpModal({ open, onClose }) {
    const [name, setName] = useState('');
    const [url, setUrl] = useState('');
    const [pairs, setPairs] = useState([blankPair()]);
    const [busy, setBusy] = useState(false);
    const [error, setError] = useState('');
    const { secrets } = SW.store.get();
    const secretNames = ((secrets && secrets.secrets) || []).map((s) => s.name);

    const close = () => {
      setName(''); setUrl(''); setPairs([blankPair()]); setError(''); setBusy(false);
      onClose();
    };
    const ready = name.trim() && url.trim();
    const add = async () => {
      setError('');
      setBusy(true);
      try {
        await SW.store.addMcpServer(SW.mcpBody({ name, url, pairs }));
        close();
      } catch (err) {
        setError(err.message);
        setBusy(false);
      }
    };
    const setPair = (i, patch) => setPairs(pairs.map((p, j) => (j === i ? Object.assign({}, p, patch) : p)));
    // After whatever is typed already, so `Bearer ` then a secret reads `Bearer {env:NAME}`.
    const useSecret = (i, secret) => {
      const typed = pairs[i].value.trimEnd();
      setPair(i, { value: `${typed ? `${typed} ` : ''}{env:${secret}}` });
    };

    return h(
      Modal,
      {
        open,
        title: 'Add MCP server',
        okText: 'Add server',
        onOk: add,
        onCancel: close,
        confirmLoading: busy,
        okButtonProps: { disabled: !ready },
        destroyOnClose: true,
      },
      h('p', { className: 'sw-caption', style: { margin: '0 0 12px' } },
        'Temporary until the MCP gateway. Every conversation and app in this project can use its tools.'),
      h(Input, {
        placeholder: 'Name, e.g. crm',
        value: name,
        'aria-label': 'Name',
        onChange: (e) => setName(e.target.value),
        style: { marginBottom: 12 },
      }),
      h(Input, {
        placeholder: 'https://crm.example.com/mcp',
        value: url,
        'aria-label': 'URL',
        onChange: (e) => setUrl(e.target.value),
        style: { marginBottom: 12 },
      }),
      h('div', { className: 'sw-caption', style: { marginBottom: 4 } }, 'Headers'),
      pairs.map((pair, i) => h(
        'div',
        { key: i, className: 'sw-mcp-pair' },
        h(Input, { placeholder: 'Authorization', value: pair.key, 'aria-label': 'Header name',
                   onChange: (e) => setPair(i, { key: e.target.value }) }),
        h(Input, { placeholder: 'Value', value: pair.value, 'aria-label': 'Header value',
                   onChange: (e) => setPair(i, { value: e.target.value }) }),
        h(Select, {
          className: 'sw-mcp-secret-pick',
          placeholder: 'Use a secret',
          value: null,
          disabled: !secretNames.length,
          options: secretNames.map((s) => ({ value: s, label: s })),
          onChange: (s) => useSecret(i, s),
          popupMatchSelectWidth: false,
        }),
        h(Button, { type: 'text', size: 'small', icon: h(DeleteOutlined, null),
                    'aria-label': 'Remove this header',
                    onClick: () => setPairs(pairs.filter((_, j) => j !== i)) })
      )),
      h(Button, { type: 'link', size: 'small', icon: h(PlusOutlined, null), style: { padding: 0 },
                  onClick: () => setPairs(pairs.concat([blankPair()])) }, 'Add a header'),
      h('p', { className: 'sw-caption', style: { margin: '6px 0 0' } },
        secretNames.length
          ? 'A key or token goes in as a secret: pick one and the header holds its name, never its value.'
          : 'A key or token goes in as a secret. Add one under Secrets first, then pick it here.'),
      error && h(Alert, { type: 'error', showIcon: true, message: error, style: { marginTop: 12 } })
    );
  };
})();
