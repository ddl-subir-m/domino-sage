window.SW = window.SW || {};

(function () {
  const { createElement: h, useState, useEffect } = React;
  const {
    Modal, Input, Select, Button, Tooltip, Popover, Empty, Badge,
  } = antd;
  const { UserAddOutlined, BellOutlined } = icons;

  const ROLES = [
    { value: 'editor', label: 'Editor', detail: 'Can build, publish, and change resources' },
    { value: 'reviewer', label: 'Reviewer', detail: 'Can comment and approve plans' },
    { value: 'viewer', label: 'Viewer', detail: 'Can open apps and read plans' },
  ];

  const PRESENCE_LABEL = {
    active: 'In this project now',
    viewing: 'Viewing an app',
    idle: 'Idle',
    offline: null,
  };

  SW.PresenceStack = function PresenceStack({ onInvite }) {
    const { members } = SW.store.get();

    const here = members.filter((m) => m.presence && m.presence !== 'offline');
    const shown = here.slice(0, 4);

    return h(
      'div',
      { className: 'sw-presence' },
      shown.map((member) =>
        h(
          Tooltip,
          {
            key: member.id,
            title: `${member.name} · ${PRESENCE_LABEL[member.presence] || member.title}`,
          },
          h(
            'span',
            { className: `sw-presence-slot is-${member.presence}` },
            h(SW.Avatar, { user: member, size: 24 })
          )
        )
      ),
      here.length > shown.length &&
        h('span', { className: 'sw-presence-more' }, `+${here.length - shown.length}`),
      h(
        Tooltip,
        { title: 'Invite people' },
        h(Button, {
          size: 'small',
          type: 'text',
          icon: h(UserAddOutlined, null),
          'aria-label': 'Invite people',
          onClick: onInvite,
        })
      )
    );
  };

  SW.InviteModal = function InviteModal() {
    const { inviteOpen, members, directory, scope } = SW.store.get();
    const [picked, setPicked] = useState([]);
    const [role, setRole] = useState('editor');
    const [note, setNote] = useState('');
    const [busy, setBusy] = useState(false);

    if (!inviteOpen) return null;
    const close = () => SW.store.set({ inviteOpen: false });

    const memberIds = new Set(members.map((m) => m.id));
    const candidates = (directory || []).filter((u) => !memberIds.has(u.id));

    const send = async () => {
      setBusy(true);
      try {
        await SW.api.invite({
          projectId: scope.id,
          userIds: picked.filter((id) => id.startsWith('u_')),
          role,
          note,
        });
        await SW.store.reloadScopeData();
        antd.message.success(
          `Invited ${picked.length} ${picked.length === 1 ? 'person' : 'people'} to ${scope.name}`
        );
        close();
        setPicked([]);
      } finally {
        setBusy(false);
      }
    };

    return h(
      Modal,
      {
        open: true,
        onCancel: close,
        title: `Invite people to ${scope.name}`,
        okText: 'Send invites',
        confirmLoading: busy,
        okButtonProps: { disabled: picked.length === 0 },
        onOk: send,
      },
      h(
        'div',
        { style: { display: 'grid', gap: 14 } },
        h(
          'div',
          null,
          h('div', { className: 'sw-field-label' }, 'People'),
          h(Select, {
            mode: 'multiple',
            style: { width: '100%' },
            value: picked,
            onChange: setPicked,
            optionFilterProp: 'title',
            placeholder: 'Type a name',
            options: candidates.map((m) => ({
              value: m.id,
              title: `${m.name} ${m.title}`,
              label: `${m.name} · ${m.title}`,
            })),
          })
        ),
        h(
          'div',
          null,
          h('div', { className: 'sw-field-label' }, 'Role'),
          h(Select, {
            style: { width: '100%' },
            value: role,
            onChange: setRole,
            options: ROLES.map((r) => ({
              value: r.value,
              label: h(
                'div',
                null,
                h('div', null, r.label),
                h('div', { className: 'sw-caption' }, r.detail)
              ),
            })),
          })
        ),
        h(
          'div',
          null,
          h('div', { className: 'sw-field-label' }, 'Note (optional)'),
          h(Input.TextArea, {
            rows: 2,
            value: note,
            onChange: (e) => setNote(e.target.value),
            placeholder: 'What to look at (optional)',
          })
        )
      )
    );
  };

  SW.NotificationBell = function NotificationBell() {
    const { notifications } = SW.store.get();
    const [open, setOpen] = useState(false);
    const unread = notifications.filter((n) => !n.read).length;

    const routeFor = (item) => {
      if (item.planId) return `#/plan/${item.planId}`;
      if (item.appId) return `#/gallery/${item.appId}`;
      if (item.threadId) return `#/chat/${item.threadId}`;
      return null;
    };

    const openItem = (item) => {
      setOpen(false);
      SW.api.readNotification(item.id).then(() => SW.store.reloadNotifications());
      const route = routeFor(item);
      if (route) SW.router.go(route);
    };

    const content = h(
      'div',
      { className: 'sw-notif-pop' },
      h(
        'div',
        { className: 'sw-notif-head' },
        h('span', { className: 'sw-group-label' }, 'Notifications'),
        unread > 0 &&
          h(
            Button,
            {
              type: 'link',
              size: 'small',
              style: { padding: 0 },
              onClick: async () => {
                await SW.api.readAllNotifications();
                SW.store.reloadNotifications();
              },
            },
            'Mark all read'
          )
      ),
      notifications.length === 0
        ? h(Empty, {
            image: Empty.PRESENTED_IMAGE_SIMPLE,
            description: h('span', { className: 'sw-secondary' }, 'Nothing new'),
          })
        : h(
            'div',
            { className: 'sw-notif-list' },
            notifications.map((item) =>
              h(
                'button',
                {
                  key: item.id,
                  className: `sw-notif-item${item.read ? '' : ' is-unread'}`,
                  onClick: () => openItem(item),
                },
                h('span', { className: 'sw-notif-dot' }),
                h(
                  'span',
                  { className: 'sw-notif-main' },
                  h('span', { className: 'sw-notif-text' }, item.title),
                  h('span', { className: 'sw-notif-detail' }, item.detail),
                  h('span', { className: 'sw-caption' }, SW.util.relativeTime(item.at))
                )
              )
            )
          )
    );

    return h(
      Popover,
      { open, onOpenChange: setOpen, content, trigger: 'click', placement: 'bottomRight', arrow: false },
      h(
        Tooltip,
        { title: open ? '' : 'Notifications', mouseEnterDelay: 0.6 },
        h(
          'button',
          { className: 'sw-icon-btn', 'aria-label': 'Notifications' },
          h(Badge, { count: unread, size: 'small', offset: [2, -2] }, h(BellOutlined, null))
        )
      )
    );
  };

  SW.ActivityFeed = function ActivityFeed({ limit }) {
    const { userIndex } = SW.store.get();
    const [items, setItems] = useState([]);

    useEffect(() => {
      SW.api.activity().then(setItems);
    }, []);

    const shown = limit ? items.slice(0, limit) : items;
    if (!shown.length) return null;

    return h(
      'div',
      { className: 'sw-activity' },
      shown.map((item) => {
        const actor = userIndex[item.actor] || { name: 'Someone' };
        return h(
          'div',
          { key: item.id, className: 'sw-activity-row' },
          h(SW.Avatar, { user: actor, size: 20 }),
          h(
            'div',
            { className: 'sw-activity-main' },
            h(
              'div',
              { className: 'sw-activity-text' },
              h('strong', null, actor.name),
              ` ${item.verb} `,
              h('span', { className: 'sw-activity-object' }, item.object)
            ),
            h('div', { className: 'sw-caption' }, SW.util.relativeTime(item.at))
          )
        );
      })
    );
  };
})();
