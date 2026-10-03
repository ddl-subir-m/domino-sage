window.SW = window.SW || {};

// The Project's own skills in the resources panel (ADR-0071, #620, #625): one row each, the drawer a
// row opens onto, and the picker that adds them from .md files, a zip of skill folders, a git repo
// or a Dataset — laid out like the Add to project catalogue, a source per sidebar entry and a row
// per skill found, each added on its own.
(function () {
  const { createElement: h, useState, useEffect, useRef } = React;
  const { Tooltip, Dropdown, Switch, Modal, Input, Select, Alert, Button, Drawer, Space, Skeleton } = antd;
  const { MoreOutlined, UploadOutlined, PlusOutlined, CheckOutlined } = icons;

  // Where a skill came from, in the row's words.
  const sourceLabel = (source) => {
    const s = source || {};
    if (s.type === 'git') return 'From git';
    if (s.type === 'dataset') return `From ${s.name || SW.brand.text('a {dataset}')}`;
    return 'Uploaded';
  };

  // `where` is what a switch answers for: 'conversation', 'app', or '' in Build with no app picked.
  SW.SkillRow = function SkillRow({ skill, where, onOpen }) {
    const subtitle = skill.shadowed
      ? SW.brand.text('Hidden: {assistantName} now ships a skill with this name. Rename yours.')
      : [skill.replaces ? `Replaces ${skill.replaces}` : '', sourceLabel(skill.source)]
          .filter(Boolean).join(' · ');
    const tip = !where
      ? 'Pick an app to switch this off for it.'
      : `${skill.enabled ? 'On' : 'Off'} for this ${where}`;
    return h(
      'div',
      { className: 'sw-res-row sw-skill-row' },
      h(
        'button',
        { type: 'button', className: 'sw-res-open', onClick: () => onOpen && onOpen(skill) },
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

  // The Replaces options: nothing, each skill Sage ships, each section of its build instructions.
  const replacesOptions = (builtinSkills, builtinSections) =>
    [{ value: '', label: SW.brand.text('Nothing: use it alongside {assistantName}\'s skills') }]
      .concat((builtinSkills || []).map((b) => ({ value: b.name, label: b.name })))
      .concat((builtinSections || []).map((s) => ({
        value: s.name, label: SW.brand.text('{name} rules in {assistantName}\'s build instructions',
                                            { name: s.name }) })));

  const replacesCaption = (replaces, builtinSkills, builtinSections) => {
    const skill = (builtinSkills || []).find((b) => b.name === replaces);
    const section = (builtinSections || []).find((s) => s.name === replaces);
    if (skill) {
      return SW.brand.text('While yours is on, {assistantName} stops offering {name}: {description}',
                           { name: skill.name, description: skill.description });
    }
    if (section) {
      return SW.brand.text('While yours is on, {assistantName} leaves its own {name} rules out of '
                           + 'the build instructions.', { name: section.name });
    }
    return SW.brand.text('Where it conflicts with {assistantName}\'s own instructions, those win.');
  };

  // One skill, opened from its row: what it says, where it came from, what it stands in for, and
  // the acts on it. The files are read when it opens, so the drawer shows what is on disk.
  SW.SkillDrawer = function SkillDrawer({ skill, builtinSkills, builtinSections, onClose }) {
    const [files, setFiles] = useState(null);
    const [error, setError] = useState('');
    const [updating, setUpdating] = useState(false);
    const id = skill && skill.id;
    const commit = skill && skill.source && skill.source.commit;

    useEffect(() => {
      if (!id) return;
      setFiles(null); setError('');
      SW.api.skillFiles(id).then((read) => setFiles(read.files || []))
        .catch((err) => setError(err.message));
    }, [id, commit]);

    if (!skill) return null;
    const source = skill.source || {};
    const fromSource = source.type === 'git' || source.type === 'dataset';
    const skillMd = (files || []).find((f) => f.path === 'SKILL.md');
    const update = async () => {
      setUpdating(true);
      await SW.store.updateSkillFromSource(skill);
      setUpdating(false);
    };
    const remove = async () => {
      if (await SW.store.removeExtension(skill)) onClose();
    };

    return h(
      Drawer,
      {
        open: true,
        onClose,
        width: 480,
        title: skill.name,
        destroyOnClose: true,
        extra: h(
          Space,
          null,
          fromSource && h(
            Tooltip,
            { title: source.type === 'git'
              ? 'Clone the repo again and take the latest SKILL.md and its files.'
              : SW.brand.text('Read the {dataset} again and take the latest SKILL.md and its files.') },
            h(Button, { onClick: update, loading: updating }, 'Update from source')
          ),
          h(Button, { danger: true, onClick: remove }, 'Remove')
        ),
      },
      skill.shadowed && h(Alert, {
        type: 'warning', showIcon: true, style: { marginBottom: 16 },
        message: SW.brand.text('{assistantName} now ships a skill with this name, so this one is '
                               + 'hidden. Rename yours to use it.'),
      }),
      h(
        'dl',
        { className: 'sw-drawer-meta' },
        h('dt', null, 'Source'),
        h('dd', null, source.type === 'git'
          ? h('a', { href: source.url, target: '_blank', rel: 'noreferrer' }, source.url)
          : source.type === 'dataset'
          ? `${source.name || source.dataset}${source.path ? ` / ${source.path}` : ''}`
          : 'Uploaded'),
        source.type === 'git' && commit && h('dt', null, 'Commit'),
        source.type === 'git' && commit && h('dd', null, h('code', null, commit.slice(0, 12))),
        h('dt', null, 'Files'),
        h('dd', null, files ? files.map((f) => f.path).join(', ') || '—' : '…')
      ),
      h(
        'div',
        { className: 'sw-drawer-section' },
        h('h4', null, 'Replaces'),
        h(Select, {
          value: skill.replaces || '',
          onChange: (v) => SW.store.setSkillReplaces(skill, v),
          style: { width: '100%' },
          options: replacesOptions(builtinSkills, builtinSections),
        }),
        h('p', { className: 'sw-caption', style: { margin: '6px 0 0' } },
          replacesCaption(skill.replaces || '', builtinSkills, builtinSections))
      ),
      h(
        'div',
        { className: 'sw-drawer-section' },
        h('h4', null, 'SKILL.md'),
        error
          ? h(Alert, { type: 'error', showIcon: true, message: error })
          : !files
          ? h(Skeleton, { active: true, paragraph: { rows: 6 } })
          : h('pre', { className: 'sw-skill-md sw-scroll' }, skillMd ? skillMd.text : '')
      )
    );
  };

  // What in a Dataset's file listing can be added: each folder holding a SKILL.md, and each zip.
  SW.skillCandidates = function skillCandidates(files) {
    const out = new Map();
    (files || []).forEach(({ path }) => {
      const parts = String(path || '').split('/');
      if (parts[parts.length - 1].toLowerCase() === 'skill.md') {
        const folder = parts.slice(0, -1).join('/');
        out.set(folder, { value: folder, label: folder ? `${folder}/` : 'The whole dataset' });
      } else if (/\.zip$/i.test(path)) {
        out.set(path, { value: path, label: path });
      }
    });
    return Array.from(out.values()).sort((a, b) => a.value.localeCompare(b.value));
  };

  // One zip goes up as it is; .md files, one or several, go up as their text.
  const uploadSource = (list) => {
    if (list.length > 1 && list.some((f) => /\.zip$/i.test(f.name))) return null;
    return list.length === 1 && /\.zip$/i.test(list[0].name) ? list[0] : (list.length ? list : null);
  };

  SW.AddSkillModal = function AddSkillModal({ open, onClose }) {
    // 'upload', 'git', or the id of a Dataset in the project.
    const [from, setFrom] = useState('upload');
    const [files, setFiles] = useState([]);
    const [url, setUrl] = useState('');
    // A Dataset's skill folders and zips, and the one the list is narrowed to ('' is all of it).
    const [found, setFound] = useState(null);
    const [picked, setPicked] = useState('');
    // What the chosen source holds, as `previewSkills` answers it.
    const [skills, setSkills] = useState(null);
    const [previewing, setPreviewing] = useState(false);
    // The folder being added, or '*' while every one is.
    const [busy, setBusy] = useState(null);
    const [error, setError] = useState('');
    const [dragging, setDragging] = useState(false);
    const fileRef = useRef(null);
    const { resourceGroups, extensions, scope } = SW.store.get();
    const datasets = (resourceGroups || {}).dataset || [];
    const have = new Set(((extensions && extensions.items) || [])
      .filter((e) => e.kind === 'skill').map((e) => e.name));

    const source = from === 'upload' ? uploadSource(files)
      : from === 'git' ? url.trim() : { dataset: from, path: picked };

    const preview = async (src) => {
      setSkills(null); setError('');
      if (!src) return;
      setPreviewing(true);
      try {
        setSkills(await SW.store.previewSkills(src));
      } catch (err) {
        setError(err.message);
      } finally {
        setPreviewing(false);
      }
    };
    const pickFrom = async (key) => {
      setFrom(key); setSkills(null); setError(''); setFound(null); setPicked('');
      if (key === 'upload') return preview(uploadSource(files));
      if (key === 'git') return url.trim() ? preview(url.trim()) : undefined;
      preview({ dataset: key, path: '' });
      try {
        const listing = await SW.api.assetFiles(key);
        setFound(SW.skillCandidates(listing.files).filter((c) => c.value !== ''));
      } catch (err) {
        setFound([]);
      }
    };
    const pickFiles = (list) => {
      setFiles(list);
      if (list.length && !uploadSource(list)) {
        setSkills(null);
        setError('Choose one .zip, or one or more .md files.');
      } else preview(uploadSource(list));
    };
    const add = async (folders) => {
      setBusy(folders.length === 1 ? folders[0] : '*');
      setError('');
      try {
        await SW.store.addSkills(source, '', folders);
      } catch (err) {
        setError(err.message);
      } finally {
        setBusy(null);
      }
    };
    const close = () => {
      setFiles([]); setUrl(''); setFound(null); setPicked(''); setSkills(null); setError('');
      onClose();
    };

    const addable = (skills || []).filter((s) => !s.refused && !have.has(s.name));
    const inCount = (skills || []).filter((s) => have.has(s.name)).length;

    const side = (key, label, child) => h(
      'button',
      { key, type: 'button', className: `sw-cat-side-btn${from === key ? ' is-active' : ''}`
          + (child ? ' is-child' : ''), onClick: () => pickFrom(key) },
      h('span', null, label)
    );

    const toolbar = from === 'upload'
      ? h(
          'div',
          {
            className: `sw-skill-drop${dragging ? ' is-dragging' : ''}`,
            onDragOver: (e) => { e.preventDefault(); setDragging(true); },
            onDragLeave: () => setDragging(false),
            onDrop: (e) => {
              e.preventDefault(); setDragging(false);
              pickFiles(Array.from((e.dataTransfer && e.dataTransfer.files) || []));
            },
          },
          h(UploadOutlined, null),
          h('span', null, files.length ? files.map((f) => f.name).join(', ')
            : 'Drop .md files or a .zip of skill folders here, or'),
          h(Button, { size: 'small', onClick: () => fileRef.current && fileRef.current.click() },
            files.length ? 'Choose others' : 'Browse'),
          h('input', {
            ref: fileRef,
            type: 'file',
            accept: '.md,.zip',
            multiple: true,
            style: { display: 'none' },
            onChange: (e) => { pickFiles(Array.from(e.target.files || [])); e.target.value = ''; },
          })
        )
      : from === 'git'
      ? h(
          'div',
          { className: 'sw-skill-git' },
          h(Input, {
            placeholder: 'https://github.com/team/skills.git',
            value: url,
            autoFocus: true,
            onChange: (e) => { setUrl(e.target.value); setSkills(null); },
            onPressEnter: () => preview(url.trim()),
          }),
          h(Button, { onClick: () => preview(url.trim()), disabled: !url.trim(), loading: previewing },
            'Find skills')
        )
      : h(Select, {
          value: picked,
          onChange: (path) => { setPicked(path); preview({ dataset: from, path }); },
          style: { width: '100%' },
          options: [{ value: '', label: SW.brand.text('Every skill folder in this {dataset}') }]
            .concat(found || []),
        });

    const note = from === 'git'
      ? 'A private repo is read with the git credential this project holds for its host.'
      : from === 'upload'
      ? 'In a zip, each folder holding a SKILL.md is a skill. A loose .md is one when its '
        + 'frontmatter has a name and a description.'
      : SW.brand.text('Each folder holding a SKILL.md is a skill. Narrow to one folder or zip above.');

    const row = (s) => {
      const isIn = have.has(s.name);
      return h(
        'div',
        { key: s.folder, className: `sw-cat-row${isIn ? ' is-in' : ''}` },
        h(
          'div',
          { className: 'sw-cat-open sw-skill-found-main' },
          h('span', { className: 'sw-cat-icon' }, SW.util.iconNodeFor('skill')),
          h(
            'span',
            { className: 'sw-cat-main' },
            h('span', { className: 'sw-cat-name-line' },
              h('span', { className: 'sw-cat-name' }, s.name || s.folder)),
            h('span', { className: 'sw-cat-desc' },
              s.description || 'No description in its frontmatter, so it would never be offered.'),
            s.refused && s.description && !isIn
              && h('span', { className: 'sw-cat-refused' }, s.refused),
            h('span', { className: 'sw-cat-meta' },
              [s.folder && s.folder !== '.' ? `${s.folder}/` : '',
               s.files.length === 1 ? '1 file' : `${s.files.length} files`].filter(Boolean).join(' · '))
          )
        ),
        isIn
          ? h(Tooltip, { title: `Already in ${scope.name}` },
              h('span', { className: 'sw-cat-in' }, h(CheckOutlined, { style: { fontSize: 10 } }),
                'In project'))
          : h(
              Tooltip,
              { title: s.refused },
              h(Button, {
                size: 'small',
                type: 'primary',
                disabled: !!s.refused || (busy !== null && busy !== s.folder),
                loading: busy === s.folder,
                icon: h(PlusOutlined, { style: { fontSize: 10 } }),
                onClick: () => add([s.folder]),
              }, 'Add')
            )
      );
    };

    return h(
      Modal,
      {
        open,
        onCancel: close,
        footer: null,
        width: 900,
        title: 'Add skills',
        className: 'sw-cat-modal',
        styles: { body: { padding: 0 } },
        destroyOnClose: true,
      },
      h(
        'div',
        { className: 'sw-cat' },
        h(
          'nav',
          { className: 'sw-cat-side' },
          h('div', { className: 'sw-group-label sw-cat-side-head' }, 'From'),
          side('upload', 'Your computer'),
          side('git', 'Git repository'),
          datasets.length > 0 &&
            h('div', { className: 'sw-group-label sw-cat-side-head sw-skill-side-sub' },
              SW.util.dataTypeLabel('dataset') || SW.brand.text('{dataset}')),
          datasets.map((d) => side(d.id, d.name, true))
        ),
        h(
          'div',
          { className: 'sw-cat-results' },
          h('div', { className: 'sw-cat-toolbar' }, toolbar),
          h('div', { className: 'sw-cat-note' }, note),
          error && h(Alert, { type: 'error', showIcon: true, message: error,
                              style: { margin: '0 16px 12px' } }),
          h(
            'div',
            { className: 'sw-cat-list sw-scroll' },
            previewing
              ? h(Skeleton, { active: true, paragraph: { rows: 4 }, style: { padding: 16 } })
              : skills
              ? (skills.length ? skills.map(row)
                : h('div', { className: 'sw-cat-note', style: { paddingTop: 16 } }, 'No skills here.'))
              : null
          ),
          h(
            'div',
            { className: 'sw-cat-foot' },
            h('span', { className: 'sw-secondary' }, skills
              ? `${skills.length} found · ${inCount} already in ${scope.name}`
              : 'Pick where the skills are.'),
            h(
              Space,
              null,
              addable.length > 1 && h(Button, {
                loading: busy === '*',
                disabled: busy !== null && busy !== '*',
                onClick: () => add(addable.map((s) => s.folder)),
              }, `Add all ${addable.length}`),
              h(Button, { onClick: close }, 'Done')
            )
          )
        )
      )
    );
  };
})();
