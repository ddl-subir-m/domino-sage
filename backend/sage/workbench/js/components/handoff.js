window.SW = window.SW || {};

(function () {
  const { createElement: h, useState, useEffect } = React;
  const { Modal, Checkbox, Radio, Space, Tag, Input, Select, Alert, Button } = antd;
  const { FileTextOutlined } = icons;

  // Everything that crosses from Chat to Builder is written to the project as
  // a real file, so the handoff is inspectable rather than magic.
  SW.HandoffSheet = function HandoffSheet() {
    const { handoffOpen, handoffDraft, attachments, scope } = SW.store.get();
    const [include, setInclude] = useState({ plan: true, resources: true, artifacts: true, transcript: false });
    const [busy, setBusy] = useState(false);

    const close = () => SW.store.set({ handoffOpen: false });

    const go = async () => {
      setBusy(true);
      try {
        await SW.store.confirmHandoff(include);
      } catch (err) {
        antd.message.error(String((err && err.message) || err));
      } finally {
        setBusy(false);
      }
    };

    if (!handoffOpen || !handoffDraft) return null;

    const artifacts = handoffDraft.artifacts || [];
    const context = handoffDraft.context || [];
    const chips = attachments.length ? attachments : context;
    const files = [
      include.plan && '.sage/plan.md',
      '.sage/handoff.md',
      include.artifacts && artifacts.length && `examples/ (${artifacts.length})`,
      include.resources && '.sage/bindings.json',
      include.transcript && '.sage/handoff-transcript.md',
    ].filter(Boolean);

    return h(
      Modal,
      {
        open: true,
        onCancel: close,
        title: 'Build from this plan',
        width: 540,
        okText: busy ? 'Setting up…' : 'Open Builder',
        confirmLoading: busy,
        onOk: go,
      },
      h(
        'div',
        { className: 'sw-handoff' },

        scope.untitled &&
          h(Alert, {
            type: 'info',
            showIcon: true,
            style: { marginBottom: 12 },
            message: 'This app will live in this project',
            description: `Confirming names it from the plan title (“${handoffDraft.title}”). Untitled already is a saved project.`,
          }),

        h(
          'div',
          { className: 'sw-handoff-section' },
          h('div', { className: 'sw-field-label' }, 'Bring across'),
          h(
            Space,
            { direction: 'vertical', size: 6 },
            h(
              Checkbox,
              { checked: include.plan, disabled: true },
              h('span', null, h('strong', null, handoffDraft.title), ' ', h('span', { className: 'sw-caption' }, '.sage/plan.md'))
            ),
            h(
              Checkbox,
              {
                checked: include.resources,
                onChange: (e) => setInclude({ ...include, resources: e.target.checked }),
              },
              h(
                'span',
                null,
                `What is in this conversation (${chips.length})`,
                h('span', { className: 'sw-caption' }, ' · becomes what the app needs')
              )
            ),
            include.resources &&
              chips.length > 0 &&
              h(
                'div',
                { style: { paddingLeft: 24 } },
                h(
                  Space,
                  { size: 4, wrap: true },
                  chips.map((a) =>
                    h(
                      Tag,
                      { key: a.id || a.name, bordered: true, style: { display: 'inline-flex', gap: 4, alignItems: 'center' } },
                      a.resourceName || a.name
                    )
                  )
                )
              ),
            h(
              Checkbox,
              {
                checked: include.artifacts,
                disabled: artifacts.length === 0,
                onChange: (e) => setInclude({ ...include, artifacts: e.target.checked }),
              },
              `Charts and outputs from the conversation (${artifacts.length})`
            ),
            h(
              Checkbox,
              {
                checked: include.transcript,
                onChange: (e) => setInclude({ ...include, transcript: e.target.checked }),
              },
              'Full conversation transcript'
            )
          )
        ),

        h(
          'div',
          { className: 'sw-handoff-files' },
          h('div', { className: 'sw-field-label' }, 'Files written to the project'),
          files.map((file) =>
            h(
              'div',
              { key: file, className: 'sw-handoff-file' },
              h(FileTextOutlined, { style: { color: '#8F8FA3' } }),
              h('code', null, file)
            )
          ),
          h(
            'div',
            { className: 'sw-caption', style: { marginTop: 6 } },
            'Everything is a real file. Sage reads them in Builder, and so can you.'
          )
        )
      )
    );
  };

  // Sandbox → project. Same idea, different direction: make the ephemeral
  // durable without losing the thread.
  SW.GraduationModal = function GraduationModal() {
    const { graduationOpen, projects, thread, resourceGroups } = SW.store.get();
    const [mode, setMode] = useState('new');
    const [name, setName] = useState('');
    const [projectId, setProjectId] = useState(null);
    const [busy, setBusy] = useState(false);

    useEffect(() => {
      if (graduationOpen && thread) setName(thread.title || 'New project');
    }, [graduationOpen, thread && thread.id]);

    if (!graduationOpen) return null;

    const close = () => SW.store.set({ graduationOpen: false });
    const files = (resourceGroups.file || []).filter((f) => f.sandbox);
    const artifacts = (thread && thread.artifacts) || [];

    const save = async () => {
      setBusy(true);
      try {
        await SW.store.saveToProject(
          mode === 'new' ? { name: name.trim() || 'New project' } : { projectId }
        );
        close();
      } finally {
        setBusy(false);
      }
    };

    return h(
      Modal,
      {
        open: true,
        onCancel: close,
        title: 'Save this work to a project',
        okText: busy ? 'Saving…' : 'Save',
        confirmLoading: busy,
        okButtonProps: { disabled: mode === 'existing' && !projectId },
        onOk: save,
        width: 480,
      },
      h(
        'div',
        { style: { display: 'grid', gap: 14 } },
        h(
          'div',
          null,
          h('div', { className: 'sw-field-label' }, 'Moving across'),
          h(
            'ul',
            { className: 'sw-plain-list' },
            h('li', null, `This conversation${thread ? ` — "${thread.title}"` : ''}`),
            files.length > 0 && h('li', null, `${files.length} uploaded ${files.length === 1 ? 'file' : 'files'}: ${files.map((f) => f.name).join(', ')}`),
            artifacts.length > 0 && h('li', null, `${artifacts.length} ${artifacts.length === 1 ? 'chart' : 'charts'}`)
          )
        ),
        h(
          Radio.Group,
          { value: mode, onChange: (e) => setMode(e.target.value) },
          h(
            Space,
            { direction: 'vertical', size: 8 },
            h(Radio, { value: 'new' }, 'A new project'),
            h(Radio, { value: 'existing', disabled: projects.length === 0 }, 'An existing project')
          )
        ),
        mode === 'new'
          ? h(Input, {
              value: name,
              autoFocus: true,
              maxLength: 60,
              placeholder: 'Project name',
              onChange: (e) => setName(e.target.value),
              onPressEnter: save,
            })
          : h(Select, {
              style: { width: '100%' },
              value: projectId,
              placeholder: 'Pick a project',
              onChange: setProjectId,
              options: projects.map((p) => ({ value: p.id, label: p.name })),
            }),
        h(
          'div',
          { className: 'sw-caption' },
          'Your sandbox stays where it is. This copies the work somewhere it will persist and where you can add people.'
        )
      )
    );
  };
})();
