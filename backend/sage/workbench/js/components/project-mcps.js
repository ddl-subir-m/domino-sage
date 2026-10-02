window.SW = window.SW || {};

// The Project's own MCP servers in the resources panel (ADR-0071, #621): one row each with
// OpenCode's own status, and the dialog that connects one by URL, by command, or from git.
(function () {
  const { createElement: h, useState } = React;
  const { Tooltip, Dropdown, Switch, Modal, Input, Segmented, Alert, Button, Checkbox } = antd;
  const { MoreOutlined, DeleteOutlined, PlusOutlined } = icons;

  const STATUS = {
    connected: 'Connected',
    failed: 'Failed',
    disabled: 'Disabled',
    needs_auth: 'Needs sign-in',
    needs_client_registration: 'Needs client registration',
    pending: 'Loads before the next turn',
    unknown: 'Status unknown',
  };
  const VARIABLE = /^[A-Za-z_][A-Za-z0-9_]*$/;

  // `where` is what a switch answers for: 'conversation', 'app', or '' in Build with no app picked.
  SW.McpRow = function McpRow({ server, where }) {
    const status = server.status || {};
    const said = STATUS[status.status] || status.status || STATUS.unknown;
    const tools = Object.keys(server.tools || {}).sort();
    const subtitle = [status.error ? `${said}: ${status.error}` : said,
      server.source && server.source.type === 'git' ? 'From git' : '',
      `${tools.length} tool${tools.length === 1 ? '' : 's'}`].filter(Boolean).join(' · ');
    const tip = !where
      ? 'Pick an app to switch this off for it.'
      : `${server.enabled ? 'On' : 'Off'} for this ${where}`;
    const menu = {
      items: [
        tools.length && {
          type: 'group',
          label: 'Offered on Ask and plan turns only if read-only',
          children: tools.map((tool) => ({
            key: `ro:${tool}`,
            label: `${tool}: ${server.tools[tool] ? 'read-only' : 'may change things'}`,
          })),
        },
        { key: 'reread', label: 'Read its tools again' },
        { key: 'remove', label: 'Remove from project', danger: true },
      ].filter(Boolean),
      onClick: ({ key }) => {
        if (key.startsWith('ro:')) {
          const tool = key.slice(3);
          return SW.store.setMcpToolReadOnly(server, tool, !server.tools[tool]);
        }
        if (key === 'reread') return SW.store.readMcpTools(server);
        return SW.store.removeExtension(server);
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
            h(Tooltip, { title: server.name, mouseEnterDelay: 0.4 },
              h('span', { className: 'sw-res-name' }, server.name))),
          h(Tooltip, { title: subtitle, mouseEnterDelay: 0.4 },
            h('span', { className: `sw-res-sub${status.status === 'failed' ? ' is-failed' : ''}` },
              subtitle))
        )
      ),
      h(
        Tooltip,
        { title: tip },
        h(Switch, {
          size: 'small',
          className: 'sw-skill-switch',
          checked: !!server.enabled,
          disabled: !where,
          'aria-label': `Use ${server.name} in this ${where || 'conversation'}`,
          onChange: (on) => SW.store.setExtensionEnabled(server, on),
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

  // What the dialog sends. A secret row's value is a variable name, written as `{env:NAME}` so the
  // secret itself never leaves the app's environment. Throws a sentence for the person.
  SW.mcpBody = function mcpBody({ name, how, url, command, git, server, pairs }) {
    if (how === 'git') return { name, git: String(git || '').trim(), server: String(server || '').trim() };
    const values = {};
    (pairs || []).forEach(({ key, value, secret }) => {
      const k = String(key || '').trim();
      const v = String(value || '').trim();
      if (!k) return;
      if (secret && !VARIABLE.test(v)) {
        throw new Error(`For ${k}, enter the variable name that holds the secret, not the secret.`);
      }
      values[k] = secret ? `{env:${v}}` : v;
    });
    const extra = Object.keys(values).length ? values : null;
    if (how === 'remote') {
      return { name, config: Object.assign({ type: 'remote', url: String(url || '').trim() },
                                           extra ? { headers: extra } : {}) };
    }
    return { name, config: Object.assign(
      { type: 'local', command: String(command || '').trim().split(/\s+/).filter(Boolean) },
      extra ? { environment: extra } : {}) };
  };

  const blankPair = () => ({ key: '', value: '', secret: true });

  SW.AddMcpModal = function AddMcpModal({ open, onClose }) {
    const [name, setName] = useState('');
    const [how, setHow] = useState('remote');
    const [url, setUrl] = useState('');
    const [command, setCommand] = useState('');
    const [git, setGit] = useState('');
    const [server, setServer] = useState('');
    const [pairs, setPairs] = useState([blankPair()]);
    const [busy, setBusy] = useState(false);
    const [error, setError] = useState('');

    const close = () => {
      setName(''); setUrl(''); setCommand(''); setGit(''); setServer('');
      setPairs([blankPair()]); setError(''); setBusy(false);
      onClose();
    };
    const ready = name.trim() && { remote: url.trim(), local: command.trim(), git: git.trim() }[how];
    const add = async () => {
      setError('');
      let body;
      try {
        body = SW.mcpBody({ name: name.trim(), how, url, command, git, server, pairs });
      } catch (err) {
        setError(err.message);
        return;
      }
      setBusy(true);
      try {
        await SW.store.addMcp(body);
        close();
      } catch (err) {
        setError(err.message);
        setBusy(false);
      }
    };
    const setPair = (i, patch) => setPairs(pairs.map((p, j) => (j === i ? Object.assign({}, p, patch) : p)));
    const noun = how === 'remote' ? 'header' : 'variable';

    return h(
      Modal,
      {
        open,
        title: 'Add an MCP server',
        okText: 'Add server',
        onOk: add,
        onCancel: close,
        confirmLoading: busy,
        okButtonProps: { disabled: !ready },
        destroyOnClose: true,
      },
      h('p', { className: 'sw-caption', style: { margin: '0 0 12px' } },
        'Every conversation and app in this project can use its tools.'),
      h(Input, {
        placeholder: 'Name, e.g. crm',
        value: name,
        onChange: (e) => setName(e.target.value),
        style: { marginBottom: 12 },
      }),
      h(Segmented, {
        block: true,
        value: how,
        onChange: (v) => { setHow(v); setError(''); },
        options: [{ label: 'Remote URL', value: 'remote' }, { label: 'Local command', value: 'local' },
                  { label: 'Git URL', value: 'git' }],
        style: { marginBottom: 12 },
      }),
      how === 'git'
        ? h(
            'div',
            null,
            h(Input, {
              placeholder: 'https://github.com/team/crm-mcp.git',
              value: git,
              onChange: (e) => setGit(e.target.value),
              style: { marginBottom: 8 },
            }),
            h(Input, {
              placeholder: 'Server name, if the repo declares several',
              value: server,
              onChange: (e) => setServer(e.target.value),
              style: { marginBottom: 8 },
            }),
            h('p', { className: 'sw-caption', style: { margin: 0 } },
              'The repo declares its server in opencode.json or .mcp.json.')
          )
        : h(
            'div',
            null,
            how === 'remote'
              ? h(Input, { placeholder: 'https://crm.example.com/mcp', value: url,
                           onChange: (e) => setUrl(e.target.value), style: { marginBottom: 12 } })
              : h(Input, { placeholder: 'npx -y @team/crm-mcp', value: command,
                           onChange: (e) => setCommand(e.target.value), style: { marginBottom: 12 } }),
            h('div', { className: 'sw-caption', style: { marginBottom: 4 } },
              how === 'remote' ? 'Headers' : 'Environment'),
            pairs.map((pair, i) => h(
              'div',
              { key: i, className: 'sw-mcp-pair' },
              h(Input, { placeholder: how === 'remote' ? 'Authorization' : 'API_KEY', value: pair.key,
                         onChange: (e) => setPair(i, { key: e.target.value }) }),
              h(Input, { placeholder: pair.secret ? 'Variable name, e.g. CRM_TOKEN' : 'Value',
                         value: pair.value, onChange: (e) => setPair(i, { value: e.target.value }) }),
              h(Checkbox, { checked: pair.secret, onChange: (e) => setPair(i, { secret: e.target.checked }) },
                'Secret'),
              h(Button, { type: 'text', size: 'small', icon: h(DeleteOutlined, null),
                          'aria-label': `Remove this ${noun}`,
                          onClick: () => setPairs(pairs.filter((_, j) => j !== i)) })
            )),
            h(Button, { type: 'link', size: 'small', icon: h(PlusOutlined, null), style: { padding: 0 },
                        onClick: () => setPairs(pairs.concat([blankPair()])) }, `Add a ${noun}`),
            h('p', { className: 'sw-caption', style: { margin: '6px 0 0' } },
              SW.brand.text('A secret is the name of an environment variable that holds it. '
                + '{assistantName} writes {env:NAME} and never stores the value. A variable added '
                + 'after the app started needs a restart.'))
          ),
      error && h(Alert, { type: 'error', showIcon: true, message: error, style: { marginTop: 12 } })
    );
  };
})();
