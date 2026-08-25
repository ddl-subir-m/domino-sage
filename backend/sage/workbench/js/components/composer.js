window.SW = window.SW || {};

(function () {
  const { createElement: h, useState, useRef } = React;
  const { Input, Button, Dropdown, Tag, Tooltip, Space } = antd;
  const { PlusOutlined, ArrowUpOutlined, DownOutlined, CloseOutlined } = icons;

  // Options read as capabilities, not model aliases. Expanding a row reveals
  // the model name for the people who care.
  const MODELS = [
    { id: 'auto', label: 'Auto (recommended)', detail: 'Sage picks per task', sovereign: false },
    { id: 'reasoning', label: 'Best for reasoning', detail: 'Claude Sonnet 4.5', sovereign: false },
    { id: 'fastest', label: 'Fastest', detail: 'GPT-5.2', sovereign: false },
    { id: 'sovereign', label: 'Runs in your environment', detail: 'Llama 3.3 70B', sovereign: true },
  ];

  // Everything the user could @-mention, with what is already in context first
  // so the common case — "the thing we were just working with" — is one key away.
  function mentionCandidates(resourceGroups, attachedIds, query) {
    const lowered = query.trim().toLowerCase();
    const all = [];
    Object.entries(resourceGroups || {}).forEach(([kind, list]) => {
      // Plans travel with the conversation already; they are opened, not mentioned.
      if (kind === 'plan') return;
      list.forEach((r) => all.push(r));
    });
    return all
      .filter((r) => !lowered || r.name.toLowerCase().includes(lowered))
      .sort((a, b) => {
        const rank = (r) => (attachedIds.has(r.id) ? 0 : 1);
        if (rank(a) !== rank(b)) return rank(a) - rank(b);
        return a.name.localeCompare(b.name);
      })
      .slice(0, 8);
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

  SW.Composer = function Composer({
    placeholder,
    onSend,
    showPhase,
    autoFocus,
    disabled,
    compact,
  }) {
    const { model, phase, attachments, scope, resourceIndex, resourceGroups } = SW.store.get();
    const [text, setText] = useState('');
    const [dragOver, setDragOver] = useState(false);
    const [mention, setMention] = useState(null);
    const [cursor, setCursor] = useState(0);
    const [sendHint, setSendHint] = useState(false);
    const fileRef = useRef(null);

    const lock = SW.util.modelLockFor(attachments);
    const available = MODELS.filter((m) => !lock || m.sovereign || m.id === 'auto');
    const activeModel = MODELS.find((m) => m.id === model) || MODELS[0];

    const attachedIds = new Set(attachments.map((a) => a.resourceId));
    const suggestions = mention ? mentionCandidates(resourceGroups, attachedIds, mention.query) : [];

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

    const changeText = (value, caret) => {
      setText(value);
      const found = mentionAt(value, caret === undefined ? value.length : caret);
      setMention(found);
      setCursor(0);
    };

    // The mention resolves into a chip rather than into text: context is a
    // thing you can see and remove, not a string in the message.
    const pickMention = async (resource) => {
      const rest = text.slice(mention.start).replace(/^@\S*/, '');
      setText(text.slice(0, mention.start) + rest);
      setMention(null);
      await SW.store.addToContext(resource, { quiet: true });
    };

    const pickFile = () => fileRef.current && fileRef.current.click();

    const handleFiles = async (files) => {
      for (const file of Array.from(files || [])) {
        await SW.store.uploadFile(file);
      }
    };

    const attachMenu = {
      items: [
        { key: 'upload', label: 'Upload a file' },
        { key: 'project', label: `See what's in ${scope.name}` },
        { key: 'browse', label: 'Browse Domino…' },
        { key: 'url', label: 'Add a URL' },
      ],
      onClick: ({ key }) => {
        if (key === 'upload') pickFile();
        if (key === 'project') SW.store.openDock('resources');
        if (key === 'browse') SW.store.openCatalog();
        if (key === 'url') antd.message.info('Adding a URL is not wired up in this prototype.');
      },
    };

    const modelMenu = {
      items: available.map((option) => ({
        key: option.id,
        label: h(
          'div',
          { style: { minWidth: 200 } },
          h('div', { className: 'sw-model-option-name' }, option.label),
          h('div', { className: 'sw-model-option-detail' }, option.detail)
        ),
      })),
      onClick: ({ key }) => SW.store.set({ model: key }),
    };

    const phaseMenu = {
      items: [
        { key: 'planning', label: 'Planning' },
        { key: 'building', label: 'Building' },
      ],
      onClick: ({ key }) => SW.store.set({ phase: key }),
    };

    return h(
      'div',
      { className: 'sw-composer-inner' },

      lock &&
        h(
          'div',
          { style: { marginBottom: 8 } },
          h(antd.Alert, {
            type: 'warning',
            showIcon: true,
            banner: true,
            style: { borderRadius: 4 },
            message: lock.message,
          })
        ),

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
                    ? `Sage added this — ${att.rationale || 'picked for you.'}`
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
              h('div', { className: 'sw-mention-head sw-group-label' }, `In ${scope.name}`),
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
            onChange: (e) => changeText(e.target.value, e.target.selectionStart),
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
          h(
            Dropdown,
            { menu: modelMenu, trigger: ['click'], placement: 'topLeft' },
            h(
              Button,
              { size: 'small' },
              h(Space, { size: 4 }, activeModel.label === 'Auto (recommended)' ? 'Auto' : activeModel.label,
                h(DownOutlined, { style: { fontSize: 9 } }))
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

          showPhase
            ? h(
                Dropdown,
                { menu: phaseMenu, trigger: ['click'], placement: 'topRight' },
                h(
                  'button',
                  { className: 'sw-phase-pill' },
                  h('span', { className: 'sw-dot sw-dot-building' }),
                  `Auto · ${phase}`,
                  h(DownOutlined, { style: { fontSize: 9 } })
                )
              )
            : h(
                Tooltip,
                { title: 'Phases apply in Builder' },
                h(
                  'button',
                  { className: 'sw-phase-pill', disabled: true },
                  h('span', { className: 'sw-dot sw-dot-draft' }),
                  'Auto · planning'
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
      ),

      scope.ephemeral &&
        h(
          'div',
          { className: 'sw-composer-hint' },
          'Personal sandbox — files are cleared when you leave.'
        )
    );
  };
})();
