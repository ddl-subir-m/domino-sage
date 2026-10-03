window.SW = window.SW || {};

// The Project's own custom tools in the resources panel (ADR-0071, #622): one row each, and the
// dialog that adds them from a .ts or .py file, or a git URL.
(function () {
  const { createElement: h, useState, useRef } = React;
  const { Tooltip, Dropdown, Switch, Modal, Input, Segmented, Alert, Button, Checkbox } = antd;
  const { MoreOutlined, UploadOutlined } = icons;

  const isPython = (tool) => (tool.files || []).some((f) => /\.py$/.test(f));

  // `where` is what a switch answers for: 'conversation', 'app', or '' in Build with no app picked.
  SW.ToolRow = function ToolRow({ tool, where }) {
    const subtitle = [isPython(tool) ? 'Python' : 'TypeScript', tool.readOnly ? 'Read-only' : '',
                      tool.source && tool.source.type === 'git' ? 'From git' : 'Uploaded']
      .filter(Boolean).join(' · ');
    const tip = !where
      ? 'Pick an app to switch this off for it.'
      : `${tool.enabled ? 'On' : 'Off'} for this ${where}`;
    return h(
      'div',
      { className: 'sw-res-row sw-tool-row' },
      h(
        'span',
        { className: 'sw-res-open sw-tool-main' },
        h('span', { className: 'sw-res-icon' }, SW.util.iconNodeFor('tool')),
        h(
          'span',
          { className: 'sw-res-main' },
          h('span', { className: 'sw-res-name-line' },
            h(Tooltip, { title: tool.name, mouseEnterDelay: 0.4 },
              h('span', { className: 'sw-res-name' }, tool.name))),
          h('span', { className: 'sw-res-sub' }, subtitle)
        )
      ),
      h(
        Tooltip,
        { title: tip },
        h(Switch, {
          size: 'small',
          className: 'sw-tool-switch',
          checked: !!tool.enabled,
          disabled: !where,
          'aria-label': `Use ${tool.name} in this ${where || 'conversation'}`,
          onChange: (on) => SW.store.setExtensionEnabled(tool, on),
        })
      ),
      h(
        Dropdown,
        {
          menu: {
            items: [
              { key: 'readOnly', label: tool.readOnly ? 'Mark not read-only' : 'Mark read-only' },
              { key: 'remove', label: 'Remove from project', danger: true },
            ],
            onClick: ({ key }) => (key === 'readOnly'
              ? SW.store.setExtensionReadOnly(tool, !tool.readOnly)
              : SW.store.removeExtension(tool)),
          },
          trigger: ['click'],
        },
        h('button', { type: 'button', className: 'sw-res-more', 'aria-label': `Actions for ${tool.name}` },
          h(MoreOutlined, null))
      )
    );
  };

  SW.AddToolModal = function AddToolModal({ open, onClose }) {
    const [how, setHow] = useState('upload');
    const [file, setFile] = useState(null);
    const [url, setUrl] = useState('');
    const [path, setPath] = useState('');
    const [readOnly, setReadOnly] = useState(false);
    const [busy, setBusy] = useState(false);
    const [error, setError] = useState('');
    const fileRef = useRef(null);

    const close = () => {
      setFile(null); setUrl(''); setPath(''); setReadOnly(false); setError(''); setBusy(false);
      onClose();
    };
    const source = how === 'upload' ? file : (url.trim() ? { url: url.trim(), path: path.trim() } : null);
    // A Python tool's SPEC says whether it is read-only, so the checkbox has nothing to say for it.
    const python = how === 'upload' && !!file && /\.py$/i.test(file.name || '');
    const add = async () => {
      setBusy(true);
      setError('');
      try {
        await SW.store.addTools(source, readOnly && !python);
        close();
      } catch (err) {
        setError(err.message);
        setBusy(false);
      }
    };

    return h(
      Modal,
      {
        open,
        title: 'Add tool',
        okText: 'Add tool',
        onOk: add,
        onCancel: close,
        confirmLoading: busy,
        okButtonProps: { disabled: !source },
        destroyOnClose: true,
      },
      h('p', { className: 'sw-caption', style: { margin: '0 0 12px' } },
        'Every conversation and app in this project can use it. A .ts tool is named after its '
        + 'file. A .py tool declares SPEC (name, description, args, readOnly) and run(**args).'),
      h(Segmented, {
        block: true,
        value: how,
        onChange: (v) => { setHow(v); setError(''); },
        options: [{ label: 'Upload', value: 'upload' }, { label: 'Git URL', value: 'git' }],
        style: { marginBottom: 12 },
      }),
      how === 'upload'
        ? h(
            'div',
            { style: { marginBottom: 12 } },
            h(Button, { icon: h(UploadOutlined, null), onClick: () => fileRef.current && fileRef.current.click() },
              file ? file.name : 'Choose a .ts or .py file'),
            h('input', {
              ref: fileRef,
              type: 'file',
              accept: '.ts,.py',
              style: { display: 'none' },
              onChange: (e) => { setFile((e.target.files || [])[0] || null); e.target.value = ''; },
            })
          )
        : h(
            'div',
            { style: { marginBottom: 12 } },
            h(Input, {
              placeholder: 'https://github.com/team/tools.git',
              value: url,
              onChange: (e) => setUrl(e.target.value),
              style: { marginBottom: 8 },
            }),
            h(Input, {
              placeholder: 'Folder or file in it (optional; the root otherwise)',
              value: path,
              onChange: (e) => setPath(e.target.value),
            })
          ),
      h(Checkbox, {
        checked: readOnly && !python,
        disabled: python,
        onChange: (e) => setReadOnly(e.target.checked),
      }, 'Read-only'),
      h('p', { className: 'sw-caption', style: { margin: '6px 0 0' } },
        python
          ? 'A Python tool says whether it is read-only in its SPEC.'
          : 'Ask and plan turns are offered only tools marked read-only. You can change it later.'),
      error && h(Alert, { type: 'error', showIcon: true, message: error, style: { marginTop: 12 } })
    );
  };
})();
