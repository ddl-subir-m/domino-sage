window.SW = window.SW || {};

(function () {
  const { createElement: h, useState, useRef, useEffect } = React;
  const { Input, Button, Dropdown, Tag, Tooltip, Space } = antd;
  const { PlusOutlined, ArrowUpOutlined, DownOutlined, CloseOutlined } = icons;

  function BUILD_MODES() {
    const who = SW.brand.assistant();
    return [
      { id: 'auto', label: 'Auto', detail: `${who} picks plan or build per turn`, key: '1' },
      { id: 'ask', label: 'Ask', detail: 'Answers questions, never changes files', key: '2' },
      { id: 'plan', label: 'Plan', detail: 'Writes a plan and waits for approval', key: '3' },
      { id: 'implement', label: 'Implement', detail: 'Builds without a plan gate', key: '4' },
    ];
  }
  const BUILD_MODE_LABEL = { auto: 'Auto', ask: 'Ask · read-only', plan: 'Plan', implement: 'Implement' };
  const PROJECT_MENTION_KINDS = ['dataset', 'datasource', 'model_llm', 'model_predictive'];

  function chatAliases(resourceGroups) {
    return (resourceGroups.model_llm || []).filter((r) => {
      const caps = r.capabilities || [];
      return !(caps.includes('embeddings') && !caps.includes('chat'));
    });
  }

  function effortLabel(value) {
    if (!value) return 'Default';
    return value.charAt(0).toUpperCase() + value.slice(1);
  }

  // Session context, then this Thread's artifacts, then project Resources, then files.
  function mentionCandidates(attachments, resourceGroups, query, artifacts) {
    const lowered = query.trim().toLowerCase();
    const matches = (r) => {
      if (!lowered) return true;
      const name = (r.name || '').toLowerCase();
      const base = String((r.path || '')).split('/').pop().toLowerCase();
      return name.includes(lowered) || base.includes(lowered);
    };
    const seen = new Set();
    const take = (row) => {
      if (!row || !row.id || seen.has(row.id) || !matches(row)) return null;
      seen.add(row.id);
      return row;
    };

    const context = [];
    (attachments || []).forEach((att) => {
      const row = take({
        id: att.resourceId || att.id,
        name: att.resourceName,
        kind: att.resourceKind || 'file',
        path: att.path,
        bindingKey: att.bindingKey,
      });
      if (row) context.push(row);
    });

    const produced = [];
    (artifacts || []).forEach((a) => {
      const path = a.path || '';
      const row = take({
        id: a.id || (path ? `artifact:${path}` : ''),
        name: a.name || path.split('/').pop(),
        kind: 'artifact',
        path,
      });
      if (row) produced.push(row);
    });

    const project = [];
    const pins = [];
    (resourceGroups.pin || []).forEach((r) => {
      const row = take(r);
      if (row) pins.push(row);
    });
    PROJECT_MENTION_KINDS.forEach((kind) => {
      (resourceGroups[kind] || []).forEach((r) => {
        const row = take(r);
        if (row) project.push(row);
      });
    });

    const files = [];
    (resourceGroups.file || []).forEach((r) => {
      if (SW.util.isHiddenFromExplorer(r.path || r.name)) return;
      const row = take(r);
      if (row) files.push(row);
    });

    return context.concat(produced, pins, project, files).slice(0, 8);
  }

  // Where the caret sits inside an unfinished @mention, if it does.
  function mentionAt(value, caret) {
    const upto = value.slice(0, caret);
    const at = upto.lastIndexOf('@');
    if (at === -1) return null;
    if (at > 0 && /[\w@]/.test(upto[at - 1])) return null;
    const token = upto.slice(at + 1);
    if (/\s/.test(token)) return null;
    return { start: at, query: token };
  }

  // Prefer the file's basename so "@data.csv" matches the path OpenCode reads.
  function mentionToken(resource) {
    const fromPath = String((resource && resource.path) || '').split('/').pop();
    const fromName = String((resource && resource.name) || '').split('/').pop();
    const token = (fromPath || fromName || 'resource').replace(/\s+/g, '_').replace(/^@+/, '');
    return '@' + token;
  }

  SW.Composer = function Composer({
    placeholder,
    onSend,
    showMode,
    autoFocus,
    disabled,
    compact,
  }) {
    const {
      model, reasoningEffort, attachments, scope, resourceIndex, resourceGroups,
      buildMode, buildTurnMode, buildRunning, catalogAsk, gatewayAliases, thread,
    } = SW.store.get();
    const [text, setText] = useState('');
    const [dragOver, setDragOver] = useState(false);
    const [mention, setMention] = useState(null);
    const [cursor, setCursor] = useState(0);
    const [sendHint, setSendHint] = useState(false);
    const [modeOpen, setModeOpen] = useState(false);
    const fileRef = useRef(null);

    const aliases = chatAliases({
      model_llm: (gatewayAliases && gatewayAliases.length) ? gatewayAliases : resourceGroups.model_llm,
    });
    const askAlias = catalogAsk || '';
    const effectiveModel = (model && model !== 'auto') ? model : askAlias;
    const activeAlias = aliases.find((a) => a.alias === effectiveModel);
    const modelLabel = activeAlias ? (activeAlias.name || activeAlias.alias) : (effectiveModel || 'Ask');
    const efforts = (activeAlias && activeAlias.reasoning_efforts) || [];

    const attachedIds = new Set(attachments.map((a) => a.resourceId));
    const suggestions = mention
      ? mentionCandidates(attachments, resourceGroups, mention.query, thread && thread.artifacts)
      : [];
    const buildModes = BUILD_MODES();
    const activeBuildMode = buildModes.find((m) => m.id === buildMode) || buildModes[0];
    const modeQueued = showMode && buildRunning && buildTurnMode && buildTurnMode !== buildMode;

    useEffect(() => {
      if (!modeOpen || !showMode) return undefined;
      const onKey = (e) => {
        if (e.key < '1' || e.key > '4') return;
        e.preventDefault();
        SW.store.setBuildMode(BUILD_MODES()[+e.key - 1].id);
        setModeOpen(false);
      };
      document.addEventListener('keydown', onKey);
      return () => document.removeEventListener('keydown', onKey);
    }, [modeOpen, showMode]);

    const send = () => {
      const value = text.trim();
      if (!value || disabled) return;
      setText('');
      setMention(null);
      // Sending disables the button under the pointer, so nothing ever fires
      // the mouseleave that would dismiss its hint.
      setSendHint(false);
      onSend(value);
    };

    const changeText = (value, caret, inputType) => {
      setText(value);
      // Backspacing over a finished mention re-opens the picker on every keystroke: the caret lands
      // just after "@BigQuery_Dem", which still matches the token. The user is deleting and gets a
      // menu — and the open menu then takes the next Enter for row selection instead of send. A
      // deletion may still NARROW a menu that is already open, so the guard is conditional on state.
      if (String(inputType || '').startsWith('delete') && !mention) return;
      const found = mentionAt(value, caret === undefined ? value.length : caret);
      setMention(found);
      setCursor(0);
    };

    // Keep @name in the box (and the sent prompt) and add the chip. Context
    // without the token meant OpenCode saw "what's in this" with no file name.
    const pickMention = async (resource) => {
      if (!mention) return;
      const token = mentionToken(resource);
      const after = text.slice(mention.start).replace(/^@\S*/, '');
      const pad = after === '' || /^\s/.test(after) ? '' : ' ';
      setText(text.slice(0, mention.start) + token + pad + after);
      setMention(null);
      await SW.store.addToContext(resource, { quiet: true });
    };

    const pickFile = () => fileRef.current && fileRef.current.click();

    const handleFiles = async (files) => {
      for (const file of Array.from(files || [])) {
        await SW.store.uploadFile(file);
      }
    };

    // Reset app is last and set apart on purpose: it is the one item here that throws work away, and
    // it should not sit where a hand reaching for Upload can find it (#36). Builder only — Chat has
    // no app code to put back. Disabled mid-build says why, because a reset would pull files out
    // from under the running turn (the server refuses it with the same sentence).
    // It confirms, and says in the same breath what it does NOT take — the fear when you click this
    // is losing the attachments and the conversation, and both survive.
    const confirmReset = () => {
      antd.Modal.confirm({
        title: 'Reset the app to the starter template?',
        content: 'The code you have built is removed and can’t be recovered. Your attached files, '
          + 'Resources, and this conversation stay.',
        okText: 'Reset app',
        okButtonProps: { danger: true },
        cancelText: 'Cancel',
        onOk: () =>
          SW.store.resetApp().catch((err) => {
            antd.message.error(String(err.message || err));
            throw err;
          }),
      });
    };

    const attachMenu = {
      items: [
        { key: 'upload', label: 'Upload a file' },
        { key: 'browse', label: 'Browse Domino…' },
        ...(showMode
          ? [
              { type: 'divider' },
              {
                key: 'reset',
                label: buildRunning ? 'Reset app — wait for this build to finish' : 'Reset app',
                danger: true,
                disabled: buildRunning,
              },
            ]
          : []),
      ],
      onClick: ({ key }) => {
        if (key === 'upload') pickFile();
        if (key === 'browse') SW.store.openCatalog();
        if (key === 'reset') confirmReset();
      },
    };

    const modelMenu = {
      selectedKeys: effectiveModel ? [effectiveModel] : [],
      items: aliases.map((option) => ({
        key: option.alias,
        label: h(
          'div',
          { style: { minWidth: 200 } },
          h('div', { className: 'sw-model-option-name' }, option.name || option.alias),
          h('div', { className: 'sw-model-option-detail' }, option.alias)
        ),
      })),
      onClick: ({ key }) => {
        const next = aliases.find((a) => a.alias === key);
        const keep = next && (next.reasoning_efforts || []).includes(reasoningEffort)
          ? reasoningEffort
          : null;
        SW.store.setChatModel(key, keep);
      },
    };

    const effortMenu = {
      items: [
        { key: 'default', label: 'Default' },
        ...efforts.map((value) => ({ key: value, label: effortLabel(value) })),
      ],
      onClick: ({ key }) => {
        SW.store.setChatModel(model, key === 'default' ? null : key);
      },
    };

    const modeMenu = {
      selectedKeys: [activeBuildMode.id],
      items: BUILD_MODES().map((option) => ({
        key: option.id,
        label: h(
          'div',
          { style: { minWidth: 220, display: 'flex', alignItems: 'flex-start', gap: 12 } },
          h(
            'div',
            { style: { flex: 1 } },
            h('div', { className: 'sw-model-option-name' }, option.label),
            h('div', { className: 'sw-model-option-detail' }, option.detail)
          ),
          h('span', { className: 'sw-caption' }, option.key)
        ),
      })),
      onClick: ({ key }) => {
        SW.store.setBuildMode(key);
        setModeOpen(false);
      },
    };

    return h(
      'div',
      { className: 'sw-composer-inner' },

      h(
        'div',
        {
          className: `sw-composer${dragOver ? ' is-dragover' : ''}`,
          onDragOver: (e) => {
            e.preventDefault();
            setDragOver(true);
          },
          onDragLeave: () => setDragOver(false),
          onDrop: (e) => {
            e.preventDefault();
            setDragOver(false);
            const resourceId = e.dataTransfer.getData('text/sw-resource');
            if (resourceId && resourceIndex[resourceId]) {
              SW.store.addToContext(resourceIndex[resourceId], { quiet: true });
            } else if (e.dataTransfer.files && e.dataTransfer.files.length) {
              handleFiles(e.dataTransfer.files);
            }
          },
        },

        // Chips are the conversation's context, so they stay put between turns.
        // Closing one takes it out of context for everything that follows.
        attachments.length > 0 &&
          h(
            'div',
            { className: 'sw-composer-chips' },
            attachments.map((att) =>
              h(
                Tooltip,
                {
                  key: att.id,
                  // The chips are the only place conversation context is shown
                  // now, so the reason Sage reached for something has to live
                  // here rather than in a panel zone.
                  title: att.addedBy === 'sage'
                    ? `${SW.brand.assistant()} added this — ${att.rationale || 'picked for you.'}`
                    : 'You added this to the conversation.',
                },
                h(
                  Tag,
                  {
                    bordered: true,
                    closable: true,
                    closeIcon: h(CloseOutlined, { style: { fontSize: 10 } }),
                    onClose: (e) => {
                      e.preventDefault();
                      SW.store.detach(att);
                    },
                    className: 'sw-chip',
                  },
                  h('span', null, SW.util.iconFor(att.resourceKind)),
                  att.resourceName
                )
              )
            )
          ),

        h(
          'div',
          { className: 'sw-composer-input' },
          mention &&
            suggestions.length > 0 &&
            h(
              'div',
              { className: 'sw-mention-pop' },
              h(
                'div',
                { className: 'sw-mention-head sw-group-label' },
                suggestions[0] && attachedIds.has(suggestions[0].id)
                  ? 'In context'
                  : suggestions[0] && suggestions[0].kind === 'artifact'
                    ? 'In this thread'
                    : `In ${scope.name}`
              ),
              suggestions.map((resource, index) =>
                h(
                  'button',
                  {
                    key: resource.id,
                    className: `sw-mention-item${index === cursor ? ' is-active' : ''}`,
                    onMouseEnter: () => setCursor(index),
                    onMouseDown: (e) => e.preventDefault(),
                    onClick: () => pickMention(resource),
                  },
                  h('span', { className: 'sw-res-icon' }, SW.util.iconFor(resource.kind)),
                  h('span', { className: 'sw-mention-name' }, resource.name),
                  attachedIds.has(resource.id)
                    ? h('span', { className: 'sw-incontext-tag' }, 'in context')
                    : h('span', { className: 'sw-caption' }, SW.util.labelFor(resource.kind))
                )
              )
            ),
          h(Input.TextArea, {
            value: text,
            autoFocus,
            disabled,
            placeholder: placeholder || 'Describe your app, or a change to make… use @ to bring in a resource',
            autoSize: { minRows: compact ? 1 : 2, maxRows: 8 },
            onChange: (e) =>
              changeText(e.target.value, e.target.selectionStart, e.nativeEvent && e.nativeEvent.inputType),
            onKeyDown: (e) => {
              if (!mention || suggestions.length === 0) return;
              if (e.key === 'ArrowDown') {
                e.preventDefault();
                setCursor((c) => (c + 1) % suggestions.length);
              } else if (e.key === 'ArrowUp') {
                e.preventDefault();
                setCursor((c) => (c - 1 + suggestions.length) % suggestions.length);
              } else if (e.key === 'Tab') {
                e.preventDefault();
                pickMention(suggestions[cursor]);
              } else if (e.key === 'Escape') {
                e.preventDefault();
                setMention(null);
              }
            },
            onPressEnter: (e) => {
              if (mention && suggestions.length > 0 && !e.shiftKey) {
                e.preventDefault();
                pickMention(suggestions[cursor]);
                return;
              }
              if (e.metaKey || e.ctrlKey || !e.shiftKey) {
                e.preventDefault();
                send();
              }
            },
          })
        ),

        h(
          'div',
          { className: 'sw-composer-bar' },
          !showMode &&
            h(
              Dropdown,
              { menu: modelMenu, trigger: ['click'], placement: 'topLeft' },
              h(
                Button,
                { size: 'small' },
                h(Space, { size: 4 }, modelLabel, h(DownOutlined, { style: { fontSize: 9 } }))
              )
            ),
          !showMode &&
            efforts.length > 0 &&
            h(
              Dropdown,
              { menu: effortMenu, trigger: ['click'], placement: 'topLeft' },
              h(
                Button,
                { size: 'small' },
                h(Space, { size: 4 }, effortLabel(reasoningEffort), h(DownOutlined, { style: { fontSize: 9 } }))
              )
            ),
          h(
            Dropdown,
            { menu: attachMenu, trigger: ['click'], placement: 'topLeft' },
            h(
              Tooltip,
              { title: 'Attach a file or resource' },
              h(Button, { size: 'small', icon: h(PlusOutlined, null), 'aria-label': 'Attach' })
            )
          ),
          h('span', { className: 'sw-composer-bar-spacer' }),

          showMode &&
            h(
              Dropdown,
              {
                menu: modeMenu,
                trigger: ['click'],
                placement: 'topRight',
                open: modeOpen,
                onOpenChange: setModeOpen,
              },
              h(
                'button',
                {
                  className: `sw-phase-pill${activeBuildMode.id === 'ask' ? ' is-ask' : ''}`,
                  type: 'button',
                  'aria-label': 'Build mode',
                  title: modeQueued
                    ? `This turn is still ${BUILD_MODE_LABEL[buildTurnMode] || buildTurnMode}. Your pick applies to the next message.`
                    : (activeBuildMode.id === 'ask'
                      ? 'Ask mode answers questions and never changes files'
                      : 'Mode'),
                },
                  h('span', {
                    className:
                      'sw-dot ' +
                      (activeBuildMode.id === 'ask'
                        ? 'sw-dot-ask'
                        : activeBuildMode.id === 'implement'
                        ? 'sw-dot-building'
                        : 'sw-dot-draft'),
                  }),
                  BUILD_MODE_LABEL[activeBuildMode.id],
                  h(DownOutlined, { style: { fontSize: 9 } })
                )
            ),

          h(
            Tooltip,
            {
              title: 'Send · ⌘⏎',
              // A hint for a button that cannot be pressed is noise, and it is
              // also how the hint got stuck: sending empties the box under the
              // pointer, so the mouseleave that would close it never comes.
              open: sendHint && Boolean(text.trim()) && !disabled,
              onOpenChange: setSendHint,
            },
            h(Button, {
              type: 'primary',
              shape: 'circle',
              size: 'small',
              disabled: !text.trim() || disabled,
              icon: h(ArrowUpOutlined, null),
              onClick: send,
              'aria-label': 'Send message',
            })
          )
        ),

        h('input', {
          ref: fileRef,
          type: 'file',
          multiple: true,
          style: { display: 'none' },
          onChange: (e) => {
            handleFiles(e.target.files);
            e.target.value = '';
          },
        })
      )
    );
  };
})();
