window.SW = window.SW || {};

// One rail, one root object, both modes. A project is a single file tree that
// publishes several apps, so which apps a conversation touched is a property of
// the conversation rather than a place to file it. Grouping the list by app
// would claim a conversation belongs to exactly one, which is the thing that
// turned out not to be true.
//
// The rail is identical in Chat and Build on purpose: switching mode should not
// mean relearning the furniture. Build differs only in what it shows beside the
// conversation, never in how you find one.
(function () {
  const { createElement: h, useState, Fragment } = React;
  const { Button, Tooltip, Input, Dropdown, Modal } = antd;
  const {
    PlusOutlined, SearchOutlined, MoreOutlined, PushpinOutlined,
    DeleteOutlined, EditOutlined, CloseOutlined,
  } = icons;

  // Which app Build has in the preview is a view parameter, so it survives
  // moving between conversations and does not change when you pick one.
  SW.conversationRoute = function conversationRoute(thread, mode) {
    if (mode !== 'build') return `#/chat/${thread.id}`;
    const { activeApp } = SW.store.get();
    return `#/build/${thread.id}${activeApp ? `?app=${activeApp.id}` : ''}`;
  };

  SW.openConversation = function openConversation(thread, mode) {
    return SW.router.go(SW.conversationRoute(thread, mode));
  };

  function conversationMenu(thread) {
    return {
      items: [
        { key: 'pin', label: thread.pinned ? 'Unpin' : 'Pin to top', icon: h(PushpinOutlined, null) },
        { key: 'rename', label: 'Rename', icon: h(EditOutlined, null) },
        { type: 'divider' },
        { key: 'delete', label: 'Delete', danger: true, icon: h(DeleteOutlined, null) },
      ],
      onClick: async ({ key, domEvent }) => {
        domEvent.stopPropagation();
        if (key === 'pin') {
          await SW.api.patchThread(thread.id, { pinned: !thread.pinned });
          SW.store.reloadThreads();
        }
        if (key === 'rename') {
          let value = thread.title;
          Modal.confirm({
            title: 'Rename conversation',
            content: h(Input, {
              defaultValue: thread.title,
              onChange: (e) => {
                value = e.target.value;
              },
            }),
            okText: 'Rename',
            onOk: async () => {
              await SW.api.patchThread(thread.id, { title: value });
              SW.store.reloadThreads();
            },
          });
        }
        if (key === 'delete') {
          Modal.confirm({
            title: 'Delete this conversation?',
            content: 'The apps it changed stay exactly as they are.',
            okText: 'Delete',
            okButtonProps: { danger: true },
            onOk: async () => {
              await SW.api.deleteThread(thread.id);
              if (SW.store.get().thread && SW.store.get().thread.id === thread.id) {
                SW.store.clearConversation();
              }
              SW.store.reloadThreads();
            },
          });
        }
      },
    };
  }

  // The tags are the whole navigation story for a flat list: they say what a
  // conversation changed, and clicking one narrows the list to that app. Earned
  // by changes only, so a tag is a fact rather than a topic.
  function AppTags({ touched, onFilter }) {
    if (!touched || touched.length === 0) return null;
    return h(
      'span',
      { className: 'sw-conv-tags' },
      touched.map((tag) =>
        h(
          Tooltip,
          {
            key: tag.appId,
            title: `${tag.kind === 'built' ? 'Built' : 'Changed'} ${tag.appName} — click to show only this app`,
          },
          h(
            'button',
            {
              className: 'sw-conv-tag',
              onClick: (e) => {
                e.stopPropagation();
                onFilter(tag.appId);
              },
            },
            tag.appName
          )
        )
      )
    );
  }

  SW.ConversationRow = function ConversationRow({ thread, active, mode, onFilter }) {
    return h(
      'div',
      {
        className: `sw-thread${active ? ' is-active' : ''}`,
        onClick: () => SW.openConversation(thread, mode),
        role: 'button',
      },
      h(
        'div',
        { className: 'sw-thread-main' },
        // The title gets two lines and no icons in it: the Pinned group already
        // says which ones are pinned, so a pin here is decoration that eats the
        // words you are scanning for.
        h('div', { className: 'sw-thread-title' }, thread.title),
        h(
          'div',
          { className: 'sw-thread-meta' },
          SW.util.relativeTime(thread.updatedAt),
          thread.planId && h('span', { className: 'sw-thread-flag' }, 'plan')
        ),
        h(AppTags, { touched: thread.touched, onFilter })
      ),
      h(
        Dropdown,
        { menu: conversationMenu(thread), trigger: ['click'], placement: 'bottomRight' },
        h('button', {
          className: 'sw-thread-more',
          'aria-label': 'Conversation actions',
          onClick: (e) => e.stopPropagation(),
        }, h(MoreOutlined, null))
      )
    );
  };

  SW.ConversationRail = function ConversationRail({ mode }) {
    const { threads, thread, scope, railHidden, railAppFilter } = SW.store.get();
    const [query, setQuery] = useState('');

    if (railHidden) {
      return h(
        'div',
        { className: 'sw-rail is-hidden' },
        h(
          Tooltip,
          { title: 'Show conversations', placement: 'right' },
          h(
            'button',
            {
              className: 'sw-icon-btn is-dark-text',
              'aria-label': 'Show conversations',
              onClick: () => SW.store.toggleRail(),
            },
            h(icons.MenuUnfoldOutlined, null)
          )
        )
      );
    }

    // One box, two things to match: what you said, and what you changed. The
    // second is why a flat history stays navigable.
    const needle = query.trim().toLowerCase();
    const filtered = threads.filter((t) => {
      if (railAppFilter && !(t.touched || []).some((x) => x.appId === railAppFilter)) return false;
      if (!needle) return true;
      return (
        t.title.toLowerCase().includes(needle) ||
        (t.touched || []).some((x) => x.appName.toLowerCase().includes(needle))
      );
    });

    const groups = SW.util.groupThreads(filtered);
    const filterName =
      railAppFilter &&
      ((threads
        .flatMap((t) => t.touched || [])
        .find((x) => x.appId === railAppFilter) || {}).appName ||
        'an app');

    return h(
      Fragment,
      null,
      h(
        'div',
        { className: 'sw-rail-head' },
        h(
          Button,
          {
            type: 'primary',
            icon: h(PlusOutlined, null),
            block: true,
            // A new conversation in Build keeps the app in the preview: you are
            // starting a new line of work on the thing you are looking at.
            onClick: () => {
              const { activeApp } = SW.store.get();
              SW.router.go(
                mode === 'build' && activeApp ? `#/build?app=${activeApp.id}` : `#/${mode}`
              );
            },
          },
          'New conversation'
        ),
        h(
          Tooltip,
          { title: 'Hide conversations' },
          h(
            'button',
            {
              className: 'sw-icon-btn is-dark-text',
              'aria-label': 'Hide conversations',
              onClick: () => SW.store.toggleRail(),
            },
            h(icons.MenuFoldOutlined, null)
          )
        )
      ),
      h(
        'div',
        { className: 'sw-rail-search' },
        h(Input, {
          size: 'small',
          prefix: h(SearchOutlined, { style: { color: '#8F8FA3' } }),
          placeholder: 'Search conversations and apps',
          value: query,
          allowClear: true,
          onChange: (e) => setQuery(e.target.value),
        })
      ),
      railAppFilter &&
        h(
          'div',
          { className: 'sw-rail-filter' },
          h('span', { className: 'sw-caption' }, 'Only '),
          h('strong', null, filterName),
          h(
            'button',
            {
              className: 'sw-rail-filter-clear',
              'aria-label': 'Show all conversations',
              onClick: () => SW.store.set({ railAppFilter: null }),
            },
            h(CloseOutlined, { style: { fontSize: 10 } })
          )
        ),
      h(
        'div',
        { className: 'sw-rail-list sw-scroll' },
        groups.length === 0
          ? h(
              'div',
              { className: 'sw-rail-empty sw-secondary' },
              railAppFilter
                ? `No conversations have changed ${filterName} yet.`
                : query
                ? `No conversations match "${query}".`
                : 'No conversations yet.'
            )
          : groups.map((group) =>
              h(
                Fragment,
                { key: group.key },
                h('div', { className: 'sw-rail-group sw-group-label' }, group.label),
                group.items.map((item) =>
                  h(SW.ConversationRow, {
                    key: item.id,
                    thread: item,
                    active: thread && thread.id === item.id,
                    mode,
                    onFilter: (appId) => SW.store.set({ railAppFilter: appId }),
                  })
                )
              )
            ),
        scope.ephemeral ||
          h(
            'div',
            { className: 'sw-rail-note' },
            'Tags name the apps a conversation changed. Click one to see everything that touched it.'
          )
      )
    );
  };
})();
