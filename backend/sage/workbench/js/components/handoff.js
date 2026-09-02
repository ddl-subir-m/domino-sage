window.SW = window.SW || {};

(function () {
  const { createElement: h, useState, useEffect } = React;
  const { Modal, Radio, Space, Input, Select, Alert, Button, Checkbox } = antd;
  const { FileTextOutlined } = icons;

  // The date on an app row. A Project holds many Built Apps and two dashboards read the same in a
  // list, so what separates them is which one is still alive. Through `relativeTime` like every
  // other date in the workbench, which is where the "relative inside 7 days" rule lives.
  function lastBuilt(app) {
    if (!app.builtAt) return app.built ? 'Built' : 'Not built yet';
    return `Last built ${SW.util.relativeTime(app.builtAt)}`;
  }

  // The three answers the sheet used to ask for on every handoff, read from where the viewer left
  // them (#58). The plan is not among them and never becomes a preference: a handoff without one
  // is not this flow. Neither is the target app — the sheet still asks that, every time.
  function crossings() {
    return {
      plan: true,
      resources: SW.prefs.get('handoffResources'),
      artifacts: SW.prefs.get('handoffArtifacts'),
      transcript: SW.prefs.get('handoffTranscript'),
    };
  }

  // Everything that crosses from Chat to Builder is written to the project as
  // a real file, so the handoff is inspectable rather than magic.
  SW.HandoffSheet = function HandoffSheet() {
    const { handoffOpen, handoffDraft } = SW.store.get();
    // What crosses is the viewer's saved answer now, not four checkboxes rebuilt from the same
    // hardcoded defaults every time this opens (#58). Read while the sheet is open rather than
    // held in state: nothing here writes it any more, so state would only be a stale copy of what
    // Account settings already holds.
    const include = handoffOpen ? crossings() : null;
    // Empty means New app, and it starts empty every time the sheet opens. Nothing preselects an
    // existing app, because building over an app nobody picked is a silent overwrite (#73).
    const [appId, setAppId] = useState('');
    const [busy, setBusy] = useState(false);

    useEffect(() => {
      if (handoffOpen) setAppId('');
    }, [handoffOpen]);

    const close = () => SW.store.set({ handoffOpen: false });

    const go = async () => {
      setBusy(true);
      try {
        await SW.store.confirmHandoff(include, { appId });
      } catch (err) {
        antd.message.error(String((err && err.message) || err));
      } finally {
        setBusy(false);
      }
    };

    if (!handoffOpen || !handoffDraft) return null;

    const artifacts = handoffDraft.artifacts || [];
    const apps = handoffDraft.apps || [];
    const target = apps.find((a) => a.id === appId);
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

        // The sheet's one question, and the reason it survives a ticket that shrank everything
        // else here to a saved answer (#58). A Project holds many Built Apps, so the target is
        // chosen per handoff: New app is the default and no existing app is ever preselected,
        // because building over an app the person did not pick is a silent overwrite
        // (docs/workbench/handoff.md §4, ADR-0008). Shown with nothing to choose between too, so
        // the first handoff says where its app goes and the second one's list is a list the person
        // has seen before.
        h(
          'div',
          { className: 'sw-handoff-section' },
          h('div', { className: 'sw-field-label' }, 'Build into'),
          h(
            Radio.Group,
            { value: appId, onChange: (e) => setAppId(e.target.value) },
            h(
              Space,
              { direction: 'vertical', size: 6 },
              h(
                Radio,
                { value: '' },
                h(
                  'span',
                  null,
                  'A new app',
                  ' ',
                  h('span', { className: 'sw-caption' }, `named “${handoffDraft.title}”`)
                )
              ),
              apps.map((app) =>
                h(
                  Radio,
                  { key: app.id, value: app.id },
                  h(
                    'span',
                    null,
                    app.name || app.id,
                    ' ',
                    h('span', { className: 'sw-caption' }, lastBuilt(app))
                  )
                )
              )
            )
          ),
          target &&
            h(Alert, {
              type: 'warning',
              showIcon: true,
              style: { marginTop: 10 },
              message: `This replaces the plan in “${target.name || target.id}”`,
              description: SW.brand.text(
                'Its code stays until you approve the plan and build. The other {builtAppPlural} '
                  + 'in this project are untouched either way.'
              ),
            })
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
          )
        )
      )
    );
  };

  // The three answers #58 moved into preferences, asked once about a crossing that already
  // happened — Change, on the plan card (#60). Same wording as Account settings, because they are
  // the same three answers and a person who has seen one should recognise the other.
  //
  // There is no Built App in it, and there will not be one. Which app a handoff lands in is a
  // per-handoff decision the sheet above asks every time and never remembers (ADR-0008), so this
  // neither re-targets nor offers to remember a target. What it does offer is keeping THESE
  // answers, which is the one thing about a crossing that is a standing preference.
  SW.CrossingSheet = function CrossingSheet({ crossed, planId, open, onClose }) {
    // Seeded from what actually crossed rather than from the preferences, because the person is
    // changing this crossing: a preference edited in another tab since would otherwise show up
    // here as an answer they never gave.
    const [edited, setEdited] = useState(null);
    const [remember, setRemember] = useState(false);
    const [busy, setBusy] = useState(false);

    if (!open) return null;

    const answers = edited || {
      resources: !!(crossed || {}).resources,
      artifacts: !!(crossed || {}).artifacts,
      transcript: !!(crossed || {}).transcript,
    };
    const carry = (name) => (e) => setEdited({ ...answers, [name]: e.target.checked });

    // The card stays mounted, so an abandoned edit would still be sitting here the next time this
    // opens — claiming to show what crossed while showing an answer nobody gave.
    const close = () => {
      setEdited(null);
      setRemember(false);
      onClose();
    };

    const go = async () => {
      setBusy(true);
      try {
        await SW.store.recrossHandoff(answers, planId);
        if (remember) {
          SW.prefs.set('handoffResources', answers.resources);
          SW.prefs.set('handoffArtifacts', answers.artifacts);
          SW.prefs.set('handoffTranscript', answers.transcript);
        }
        close();
      } catch (err) {
        antd.message.error(String((err && err.message) || err));
      } finally {
        setBusy(false);
      }
    };

    return h(
      Modal,
      {
        open: true,
        onCancel: close,
        title: 'Change what crosses',
        width: 460,
        okText: busy ? 'Redoing…' : 'Redo the crossing',
        confirmLoading: busy,
        onOk: go,
      },
      h(
        'div',
        { className: 'sw-handoff' },
        h(
          Space,
          { direction: 'vertical', size: 8, role: 'group', 'aria-label': 'What crosses' },
          h(
            Checkbox,
            { name: 'resources', checked: answers.resources, onChange: carry('resources') },
            'What is in the conversation'
          ),
          h(
            Checkbox,
            { name: 'artifacts', checked: answers.artifacts, onChange: carry('artifacts') },
            'Charts and outputs from the conversation'
          ),
          h(
            Checkbox,
            { name: 'transcript', checked: answers.transcript, onChange: carry('transcript') },
            'The full conversation transcript'
          )
        ),
        h(
          Checkbox,
          { name: 'remember', checked: remember, onChange: (e) => setRemember(e.target.checked) },
          h(
            'span',
            null,
            'Remember these answers',
            h('span', { className: 'sw-caption' }, ' · for every handoff after this one')
          )
        )
      )
    );
  };

  // Save this conversation into a Project of its own. Same idea as the handoff above,
  // different direction — and it keeps the thread.
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
      } catch (err) {
        // Without this the modal stays open saying nothing, and Enter retries into the same
        // silence. Same shape as HandoffSheet.go above.
        antd.message.error(String((err && err.message) || err));
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
            })
      )
    );
  };
})();
