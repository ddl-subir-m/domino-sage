window.SW = window.SW || {};

// The Project's own skills in the resources panel (ADR-0071, #620): one row each, and the dialog
// that adds them from a SKILL.md, a zip of skill folders, or a git URL.
(function () {
  const { createElement: h, useState, useRef } = React;
  const { Tooltip, Dropdown, Switch, Modal, Input, Select, Segmented, Alert, Button } = antd;
  const { MoreOutlined, UploadOutlined } = icons;

  // `where` is what a switch answers for: 'conversation', 'app', or '' when neither exists yet.
  SW.SkillRow = function SkillRow({ skill, where }) {
    const subtitle = skill.shadowed
      ? SW.brand.text('Hidden: {assistantName} now ships a skill with this name. Rename yours.')
      : [skill.replaces ? `Replaces ${skill.replaces}` : '',
         skill.source && skill.source.type === 'git' ? 'From git' : 'Uploaded'].filter(Boolean).join(' · ');
    const tip = !where
      ? 'Start a conversation or pick an app to switch this off for it.'
      : `${skill.enabled ? 'On' : 'Off'} for this ${where}`;
    return h(
      'div',
      { className: 'sw-res-row sw-skill-row' },
      h(
        'span',
        { className: 'sw-res-open sw-skill-main' },
        h('span', { className: 'sw-res-icon' }, SW.util.iconNodeFor('skill')),
        h(
          'span',
          { className: 'sw-res-main' },
          h('span', { className: 'sw-res-name-line' },
            h(Tooltip, { title: skill.name, mouseEnterDelay: 0.4 },
              h('span', { className: 'sw-res-name' }, skill.name))),
          h('span', { className: 'sw-res-sub' }, subtitle)
        )
      ),
      h(
        Tooltip,
        { title: tip },
        h(Switch, {
          size: 'small',
          className: 'sw-skill-switch',
          checked: !!skill.enabled && !skill.shadowed,
          disabled: !where || !!skill.shadowed,
          'aria-label': `Use ${skill.name} in this ${where || 'conversation'}`,
          onChange: (on) => SW.store.setExtensionEnabled(skill, on),
        })
      ),
      h(
        Dropdown,
        {
          menu: {
            items: [{ key: 'remove', label: 'Remove from project', danger: true }],
            onClick: () => SW.store.removeExtension(skill),
          },
          trigger: ['click'],
        },
        h('button', { type: 'button', className: 'sw-res-more', 'aria-label': `Actions for ${skill.name}` },
          h(MoreOutlined, null))
      )
    );
  };

  // What in a Dataset's file listing can be added: each folder holding a SKILL.md, and each zip.
  SW.skillCandidates = function skillCandidates(files) {
    const out = new Map();
    (files || []).forEach(({ path }) => {
      const parts = String(path || '').split('/');
      if (parts[parts.length - 1] === 'SKILL.md') {
        const folder = parts.slice(0, -1).join('/');
        out.set(folder, { value: folder, label: folder ? `${folder}/` : 'The whole dataset' });
      } else if (/\.zip$/i.test(path)) {
        out.set(path, { value: path, label: path });
      }
    });
    return Array.from(out.values()).sort((a, b) => a.value.localeCompare(b.value));
  };

  SW.AddSkillModal = function AddSkillModal({ open, builtinSkills, onClose }) {
    const [how, setHow] = useState('upload');
    const [file, setFile] = useState(null);
    const [url, setUrl] = useState('');
    const [dataset, setDataset] = useState('');
    const [found, setFound] = useState(null);
    const [picked, setPicked] = useState(null);
    const datasets = (SW.store.get().resourceGroups || {}).dataset || [];
    const pickDataset = async (id) => {
      setDataset(id); setFound(null); setPicked(null); setError('');
      try {
        const listing = await SW.api.assetFiles(id);
        setFound(SW.skillCandidates(listing.files));
      } catch (err) {
        setFound([]);
        setError(err.message);
      }
    };
    const [replaces, setReplaces] = useState('');
    const [busy, setBusy] = useState(false);
    const [error, setError] = useState('');
    const fileRef = useRef(null);

    const close = () => {
      setFile(null); setUrl(''); setDataset(''); setFound(null); setPicked(null);
      setReplaces(''); setError(''); setBusy(false);
      onClose();
    };
    const source = { upload: file, git: url.trim(),
                     dataset: picked === null ? null : { dataset, path: picked } }[how];
    const add = async () => {
      setBusy(true);
      setError('');
      try {
        await SW.store.addSkills(source, replaces);
        close();
      } catch (err) {
        setError(err.message);
        setBusy(false);
      }
    };
    const replaced = (builtinSkills || []).find((b) => b.name === replaces);

    return h(
      Modal,
      {
        open,
        title: 'Add a skill',
        okText: 'Add skill',
        onOk: add,
        onCancel: close,
        confirmLoading: busy,
        okButtonProps: { disabled: !source },
        destroyOnClose: true,
      },
      h('p', { className: 'sw-caption', style: { margin: '0 0 12px' } },
        'Every conversation and app in this project can use it. Its SKILL.md needs a name and a '
        + 'description in its frontmatter.'),
      h(Segmented, {
        block: true,
        value: how,
        onChange: (v) => { setHow(v); setError(''); },
        options: [{ label: 'Upload', value: 'upload' }, { label: 'Git URL', value: 'git' },
                  { label: SW.brand.text('{dataset}'), value: 'dataset' }],
        style: { marginBottom: 12 },
      }),
      how === 'dataset' &&
        h(
          'div',
          { style: { marginBottom: 12 } },
          datasets.length
            ? h(Select, {
                placeholder: SW.brand.text('Pick a {dataset} in this {project}'),
                value: dataset || undefined,
                onChange: pickDataset,
                style: { width: '100%', marginBottom: 8 },
                options: datasets.map((d) => ({ value: d.id, label: d.name })),
              })
            : h('p', { className: 'sw-caption', style: { margin: 0 } },
                SW.brand.text('Add a {dataset} to this {project} first.')),
          found && (found.length
            ? h(Select, {
                placeholder: 'Pick a skill folder or zip',
                value: picked === null ? undefined : picked,
                onChange: setPicked,
                style: { width: '100%' },
                options: found,
              })
            : !error && h('p', { className: 'sw-caption', style: { margin: 0 } },
                SW.brand.text('No SKILL.md or zip in this {dataset}.')))
        ),
      how === 'dataset' ? null : how === 'upload'
        ? h(
            'div',
            { style: { marginBottom: 12 } },
            h(Button, { icon: h(UploadOutlined, null), onClick: () => fileRef.current && fileRef.current.click() },
              file ? file.name : 'Choose a SKILL.md or .zip'),
            h('input', {
              ref: fileRef,
              type: 'file',
              accept: '.md,.zip',
              style: { display: 'none' },
              onChange: (e) => { setFile((e.target.files || [])[0] || null); e.target.value = ''; },
            })
          )
        : h(Input, {
            placeholder: 'https://github.com/team/skills.git',
            value: url,
            onChange: (e) => setUrl(e.target.value),
            style: { marginBottom: 12 },
          }),
      h('div', { className: 'sw-caption', style: { marginBottom: 4 } }, 'Replaces'),
      h(Select, {
        value: replaces,
        onChange: setReplaces,
        style: { width: '100%' },
        options: [{ value: '', label: SW.brand.text('Nothing: use it alongside {assistantName}\'s skills') }]
          .concat((builtinSkills || []).map((b) => ({ value: b.name, label: b.name }))),
      }),
      h('p', { className: 'sw-caption', style: { margin: '6px 0 0' } },
        replaced
          ? SW.brand.text('While yours is on, {assistantName} stops offering {name}: {description}',
                          { name: replaced.name, description: replaced.description })
          : SW.brand.text('Where it conflicts with {assistantName}\'s own instructions, those win.')),
      error && h(Alert, { type: 'error', showIcon: true, message: error, style: { marginTop: 12 } })
    );
  };
})();
