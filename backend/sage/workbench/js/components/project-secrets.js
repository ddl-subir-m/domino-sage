window.SW = window.SW || {};

// The Project's secrets in the resources panel (#640, #643): one row each, name and note, and the
// dialog that adds one, replaces its value or edits its note. A value goes out once, in the PUT
// that sets it, and is never read back: no row, no field and no toast holds one.
(function () {
  const { createElement: h, useState } = React;
  const { Dropdown, Modal, Input, Alert, Tooltip } = antd;
  const { MoreOutlined } = icons;

  SW.SecretRow = function SecretRow({ secret, onEdit }) {
    return h(
      'div',
      { className: 'sw-res-row sw-secret-row' },
      h(
        'span',
        { className: 'sw-res-open sw-skill-main' },
        h('span', { className: 'sw-res-icon' }, SW.util.iconNodeFor('secret')),
        h(
          'span',
          { className: 'sw-res-main' },
          h('span', { className: 'sw-res-name-line' },
            h(Tooltip, { title: secret.name, mouseEnterDelay: 0.4 },
              h('span', { className: 'sw-res-name' }, secret.name))),
          h('span', { className: 'sw-res-sub' }, secret.note || 'No note')
        )
      ),
      h(
        Dropdown,
        {
          menu: {
            items: [
              { key: 'replace', label: 'Replace value' },
              { key: 'note', label: 'Edit note' },
              { key: 'remove', label: 'Remove from project', danger: true },
            ],
            onClick: ({ key }) => (key === 'remove'
              ? SW.store.removeSecret(secret) : onEdit(key, secret)),
          },
          trigger: ['click'],
        },
        h('button', { type: 'button', className: 'sw-res-more', 'aria-label': `Actions for ${secret.name}` },
          h(MoreOutlined, null))
      )
    );
  };

  // `mode` is 'add', 'replace' (a new value for `secret`) or 'note' (its note only).
  SW.SecretModal = function SecretModal({ mode, secret, onClose }) {
    const [name, setName] = useState('');
    const [value, setValue] = useState('');
    const [note, setNote] = useState(mode === 'note' ? (secret && secret.note) || '' : '');
    const [busy, setBusy] = useState(false);
    const [error, setError] = useState('');

    const close = () => {
      setName(''); setValue(''); setNote(''); setError(''); setBusy(false);
      onClose();
    };
    const target = mode === 'add' ? name.trim() : secret.name;
    const ready = target && (mode === 'note' || value);
    const save = async () => {
      setError('');
      setBusy(true);
      const body = mode === 'note' ? { note } : mode === 'replace' ? { value } : { value, note };
      try {
        await SW.store.saveSecret(target, body);
        close();
      } catch (err) {
        setError(err.message);
        setBusy(false);
      }
    };

    return h(
      Modal,
      {
        open: true,
        title: mode === 'add' ? 'Add secret'
          : mode === 'replace' ? `Replace the value of ${secret.name}` : `Note for ${secret.name}`,
        okText: 'Save',
        onOk: save,
        onCancel: close,
        confirmLoading: busy,
        okButtonProps: { disabled: !ready },
        destroyOnClose: true,
      },
      mode === 'add' && h(Input, {
        placeholder: 'Name, e.g. OPENAI_API_KEY',
        value: name,
        autoFocus: true,
        'aria-label': 'Name',
        onChange: (e) => setName(e.target.value),
        style: { marginBottom: 12 },
      }),
      mode !== 'note' && h(Input.Password, {
        placeholder: 'Value',
        value,
        autoFocus: mode === 'replace',
        autoComplete: 'new-password',
        visibilityToggle: false,
        'aria-label': 'Value',
        onChange: (e) => setValue(e.target.value),
        style: { marginBottom: 12 },
      }),
      mode !== 'replace' && h(Input, {
        placeholder: 'Note, e.g. OpenAI key; use with api.openai.com',
        value: note,
        'aria-label': 'Note',
        onChange: (e) => setNote(e.target.value),
      }),
      h('p', { className: 'sw-caption', style: { margin: '8px 0 0' } },
        mode === 'note'
          ? SW.brand.text('{assistantName} reads the note to know what the secret is for.')
          : 'The value is not shown again, here or anywhere.'),
      error && h(Alert, { type: 'error', showIcon: true, message: error, style: { marginTop: 12 } })
    );
  };
})();
