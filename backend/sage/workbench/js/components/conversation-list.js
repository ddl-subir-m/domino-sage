window.SW = window.SW || {};

// One rail, one root object, both modes. A project is a single file tree that
// publishes several apps, so which apps a conversation touched is a property of
// the conversation rather than a place to file it. Grouping the list by app
// would claim a conversation belongs to exactly one, which is the thing that
// turned out not to be true.
//
// This is the rail in BOTH modes (#82). It was Chat's alone, and Build listed Built Apps instead,
// so crossing into Build took your history off screen while the transcript beside it claimed to be
// one Conversation. Which Built App you are looking at is the Build header's job now. `mode` is
// only what decides where a row goes — never what a row says.
(function () {
  const { createElement: h, useState, Fragment } = React;
  const { Button, Tooltip, Input, Dropdown, Modal } = antd;
  const {
    PlusOutlined, SearchOutlined, MoreOutlined, PushpinOutlined,
    DeleteOutlined, EditOutlined, CloseOutlined,
  } = icons;

  // A Build link naming no app resolves the Conversation's own app (ADR-0009). This used to stamp
  // the selected one in, so that case never arose from a click — every rail link named whatever
  // was in the preview, and opening a Conversation left Build looking at another one's work.
  //
  // `?app=` is still readable grammar: a shared link carries it, and the URL follows a selection
  // moved in another tab (#100). It is only no longer written here.
  SW.conversationRoute = function conversationRoute(thread, mode) {
    return mode === 'build' ? `#/build/${thread.id}` : `#/chat/${thread.id}`;
  };

  SW.openConversation = function openConversation(thread, mode) {
    // On the click, not a beat later. The row the rail is holding already carries the answer —
    // `boundAppId` is the newest bound handoff, composed by the server — so the preview, the Build
    // header and the panel's app section move with the transcript instead of describing the app
    // you came from until a resolve lands. A Conversation that bound nothing selects nothing, and
    // what is on screen is left alone rather than blanked.
    //
    // The resolve below this still runs, and still answers for a Built App started inside Build
    // that no handoff ever named. It is the correction now, not the common path.
    if (mode === 'build' && thread.boundAppId) SW.store.selectApp(thread.boundAppId);
    return SW.router.go(SW.conversationRoute(thread, mode));
  };

  function conversationMenu(thread) {
    return {
      items: [
        { key: 'pin', label: thread.pinned ? 'Unpin' : 'Pin to top', icon: h(PushpinOutlined, null) },
        { key: 'rename', label: 'Rename', icon: h(EditOutlined, null) },
        { key: 'build', label: 'Open in Build' },
        { type: 'divider' },
        { key: 'delete', label: 'Delete', danger: true, icon: h(DeleteOutlined, null) },
      ],
      onClick: async ({ key, domEvent }) => {
        domEvent.stopPropagation();
        if (key === 'pin') {
          await SW.api.patchThread(thread.id, { pinned: !thread.pinned });
          SW.store.reloadThreads();
        }
        if (key === 'build') {
          await SW.store.draftHandoffPlan(thread.id);
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

  // The row for a conversation that does not exist yet. Same markup as a real row and the same
  // selected state, because it is answering the same question — which conversation am I looking
  // at — for the one case where the answer is not in the list.
  //
  // What it leaves out is what it does not have: no tags, because it has changed nothing; no
  // actions menu, because there is nothing yet to pin, rename or delete; no click, because it is
  // already where you are. The meta line carries the next step instead of a timestamp, which is
  // the honest version of "this is not saved" — clicking any other row discards it.
  //
  // `is-pending` is only there to take the pointer cursor back off: every other row in the rail
  // goes somewhere when clicked, and this is the one that cannot.
  function PendingConversationRow() {
    return h(
      'div',
      { className: 'sw-thread is-active is-pending' },
      h(
        'div',
        { className: 'sw-thread-main' },
        h('div', { className: 'sw-thread-title' }, 'New conversation'),
        h('div', { className: 'sw-thread-meta' }, 'Send a message to start it')
      )
    );
  }

  SW.ConversationRail = function ConversationRail({ mode }) {
    const { threads, thread, railHidden, railAppFilter, pendingConversation, apps } = SW.store.get();
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
      // The open conversation is exempt, for the reason the pending row is: the rail's job is to
      // say which conversation you are looking at, and a filter that hides it empties the list
      // under a transcript that is still on screen. Picking an app in the Build header would
      // otherwise do exactly that to the conversation you were mid-way through.
      const standingIn = thread && thread.id === t.id;
      if (railAppFilter && !standingIn && !(t.touched || []).some((x) => x.appId === railAppFilter)) return false;
      if (!needle) return true;
      return (
        t.title.toLowerCase().includes(needle) ||
        (t.touched || []).some((x) => x.appName.toLowerCase().includes(needle))
      );
    });

    const groups = SW.util.groupThreads(filtered);
    // The Project's app list first, the tags second. The tag scan alone held while a chip was the
    // only writer — the app was in some thread's tags by definition — but the Build header can now
    // filter to an app no conversation has changed, and that read "Only an app".
    const filterName =
      railAppFilter &&
      ((apps.find((a) => a.id === railAppFilter) || {}).name ||
        (threads
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
            //
            // Clear before navigating. A conversation can start without the route
            // ever naming it (attaching a Resource opens one, and so does typing
            // in Build), so the hash may already be the one we are going to.
            // Navigation alone would then change nothing, and the button would
            // look dead — which is exactly how it looked. `newConversation` is
            // that clear plus the rail row that shows the press landed.
            onClick: () => {
              const { activeApp } = SW.store.get();
              SW.store.newConversation();
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
        // Above the groups and outside them: it belongs to no day, and it is not history
        // until it has been said. `thread` is the guard — the first message opens a real
        // conversation, and that row is the one to be selected from then on.
        //
        // It goes away while the list is being searched or filtered, because those ask a
        // question about history and this row is not in it. Leaving it drew a conversation
        // sitting directly above "No conversations match", which is the rail contradicting
        // itself about what it just found.
        pendingConversation && !thread && !needle && !railAppFilter &&
          h(PendingConversationRow, { key: 'pending' }),
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
            )
      )
    );
  };
})();
