window.SW = window.SW || {};

(function () {
  const { createElement: h, useState, useEffect } = React;
  const { Modal, Checkbox, Radio, Space, Tag, Input, Select, Alert, Button } = antd;
  const { FileTextOutlined } = icons;

  // Everything that crosses from Chat to Builder is written to the project as
  // a real file, so the handoff is inspectable rather than magic.
  SW.HandoffSheet = function HandoffSheet() {
    const { handoffPlanId, attachments, thread, scope } = SW.store.get();
    const [plan, setPlan] = useState(null);
    const [target, setTarget] = useState('new');
    const [kind, setKind] = useState('app');
    const [include, setInclude] = useState({ plan: true, resources: true, artifacts: true, transcript: false });
    const [busy, setBusy] = useState(false);
    const [apps, setApps] = useState([]);
    const [existingApp, setExistingApp] = useState(null);

    useEffect(() => {
      if (!handoffPlanId) return;
      SW.api.plan(handoffPlanId).then((p) => {
        setPlan(p);
        setTarget(p.appId ? 'existing' : 'new');
        setExistingApp(p.appId || null);
      });
      SW.api.apps().then(setApps);
    }, [handoffPlanId]);

    const close = () => SW.store.set({ handoffPlanId: null });

    const go = async () => {
      setBusy(true);
      try {
        const result = await SW.api.handoff({
          planId: plan.id,
          target,
          appId: target === 'existing' ? existingApp : undefined,
          kind,
          include,
          threadId: thread ? thread.id : undefined,
        });
        close();
        // The server just tagged this conversation with the app it made; keep
        // the open copy in step so the rail shows the tag straight away. The
        // plan's status moved too, so the panel has to be re-read or it keeps
        // calling a built plan a draft.
        if (thread) {
          const created = await SW.api.thread(thread.id);
          SW.store.set({ thread: created, touched: created.touched || [] });
          SW.store.reloadThreads();
        }
        SW.store.reloadScopeData();
        // Land in Build on this same conversation, with the new app in the
        // preview. The conversation is what carries across; the app is what it
        // is now pointed at.
        SW.router.go(
          thread ? `#/build/${thread.id}?app=${result.appId}` : `#/build?app=${result.appId}`
        );
      } finally {
        setBusy(false);
      }
    };

    if (!handoffPlanId) return null;

    const artifacts = (thread && thread.artifacts) || [];
    const files = [
      include.plan && 'plan.md',
      include.artifacts && artifacts.length && `examples/ (${artifacts.length})`,
      include.transcript && 'context.md',
      'README.md',
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
        okButtonProps: { disabled: !plan },
        onOk: go,
      },
      plan &&
        h(
          'div',
          { className: 'sw-handoff' },

          scope.ephemeral &&
            h(Alert, {
              type: 'warning',
              showIcon: true,
              style: { marginBottom: 12 },
              message: 'You are in the personal sandbox',
              description: 'Save this work to a project first so the app has somewhere durable to live.',
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
                h('span', null, h('strong', null, plan.title), ' ', h('span', { className: 'sw-caption' }, `plan v${plan.version}`))
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
                  `What is in this conversation (${attachments.length})`,
                  h('span', { className: 'sw-caption' }, ' · becomes what the app needs')
                )
              ),
              include.resources &&
                h(
                  'div',
                  { style: { paddingLeft: 24 } },
                  h(
                    Space,
                    { size: 4, wrap: true },
                    attachments.map((a) =>
                      h(
                        Tag,
                        { key: a.id, bordered: true, style: { display: 'inline-flex', gap: 4, alignItems: 'center' } },
                        SW.util.iconFor(a.resourceKind),
                        a.resourceName
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
            { className: 'sw-handoff-section' },
            h('div', { className: 'sw-field-label' }, 'Build into'),
            h(
              Radio.Group,
              { value: target, onChange: (e) => setTarget(e.target.value) },
              h(
                Space,
                { direction: 'vertical', size: 6 },
                h(Radio, { value: 'new' }, 'A new app'),
                h(
                  Radio,
                  { value: 'existing', disabled: apps.length === 0 },
                  'An existing app'
                )
              )
            ),
            target === 'existing' &&
              h(Select, {
                style: { width: '100%', marginTop: 8 },
                value: existingApp,
                onChange: setExistingApp,
                placeholder: 'Pick an app',
                options: apps.map((a) => ({ value: a.id, label: a.name })),
              }),
            target === 'new' &&
              h(
                Radio.Group,
                {
                  value: kind,
                  onChange: (e) => setKind(e.target.value),
                  optionType: 'button',
                  buttonStyle: 'solid',
                  size: 'small',
                  style: { marginTop: 8 },
                },
                h(Radio.Button, { value: 'app' }, 'App'),
                h(Radio.Button, { value: 'agent' }, 'Agent')
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
