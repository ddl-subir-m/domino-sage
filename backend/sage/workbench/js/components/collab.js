window.SW = window.SW || {};

(function () {
  const { createElement: h, useState, useEffect } = React;
  const {
    Modal, Input, Select, Button, Tooltip, Popover, Empty, Badge,
  } = antd;
  const { UserAddOutlined, BellOutlined } = icons;

  // Everyone on the Project is a Collaborator, and Domino says so — there is no presence here to
  // draw. An earlier version filtered this stack on a `presence` field, which the server has never
  // sent and nothing implements, so the stack was always empty. Faking it would put a green dot
  // beside somebody who is not there.
  SW.CollaboratorStack = function CollaboratorStack({ onOpen }) {
    const { members } = SW.store.get();
    const shown = members.slice(0, 4);

    return h(
      'div',
      { className: 'sw-presence' },
      shown.map((member) =>
        h(
          Tooltip,
          { key: member.id, title: member.role ? `${member.name} · ${member.role}` : member.name },
          h(
            'span',
            { className: 'sw-presence-slot' },
            h(SW.Avatar, { user: member, size: 24 })
          )
        )
      ),
      members.length > shown.length
        ? h('span', { className: 'sw-presence-more' }, `+${members.length - shown.length}`)
        : null,
      h(
        Tooltip,
        { title: SW.brand.text('Add people to this {project}') },
        h(Button, {
          size: 'small',
          type: 'text',
          icon: h(UserAddOutlined, null),
          'aria-label': SW.brand.text('Add people to this {project}'),
          onClick: onOpen,
        })
      )
    );
  };

  // The one Domino role that cannot open an App published from the Project. A role name does not
  // carry that fact, and a creator adding somebody to show them the thing they built would find out
  // only when the colleague reported a wall. Compared case-folded: Domino writes this value in one
  // case and reads it back in another.
  const CANNOT_OPEN_APPS = 'projectimporter';

  SW.PeopleModal = function PeopleModal() {
    const {
      peopleOpen, members, directory, ownerId, selfId, membersConnected, membersError,
      membersLoading, scope,
    } = SW.store.get();
    const [picked, setPicked] = useState([]);
    const [busy, setBusy] = useState(false);
    const [removing, setRemoving] = useState('');

    // Cleared on close, because the modal is mounted for the life of the Shell and only returns
    // null below — React keeps its state across an open and a close. Without this, a creator who
    // picks two people and presses Escape reopens on "Add 2 people", one click from adding people
    // they walked away from; and an id that has since been added elsewhere renders as raw hex,
    // because it is no longer among the options the Select can label.
    useEffect(() => {
      if (!peopleOpen) { setPicked([]); setRemoving(''); }
    }, [peopleOpen]);

    if (!peopleOpen) return null;
    const close = () => SW.store.set({ peopleOpen: false });

    const memberIds = new Set(members.map((m) => m.id));
    const candidates = (directory || []).filter((u) => !memberIds.has(u.id));

    // Adding is immediate — there is no invitation and no acceptance step, so the button says what
    // it does. Nothing is rolled back on a partial failure: the people who were added are added,
    // and the ones who were not stay picked so the retry is one click.
    const add = async () => {
      setBusy(true);
      try {
        const out = await SW.api.addCollaborators(picked);
        await SW.store.reloadMembers();
        setPicked(out.failed.map((f) => f.id));
        report(out);
      } catch (e) {
        antd.message.error(e.message);
      } finally {
        setBusy(false);
      }
    };

    // Precise about both halves. "Some could not be added" would leave the creator to work out who,
    // and the reason is the platform's own sentence rather than one of ours.
    const report = (out) => {
      const added = out.added.length;
      if (added) {
        antd.message.success(
          added === 1 ? 'Added 1 person.' : `Added ${added} people.`
        );
      }
      out.failed.forEach((f) => {
        const person = (directory || []).find((u) => u.id === f.id);
        antd.message.error(`Could not add ${(person && person.name) || f.id} — ${f.reason}`);
      });
    };

    const remove = async (member) => {
      setRemoving(member.id);
      try {
        await SW.api.removeCollaborator(member.id);
        await SW.store.reloadMembers();
        antd.message.success(`Removed ${member.name}.`);
      } catch (e) {
        antd.message.error(e.message);
      } finally {
        setRemoving('');
      }
    };

    // Why a row cannot be removed, said on the row. A 403 after the click would tell the creator
    // the same thing one action too late, and only if they clicked.
    // Both sides guard against the empty id, because both are "" when the read that would have
    // supplied them did not answer, and "" === "" would put the reason on a row it is not true of.
    const undeletable = (member) => {
      if (member.id && member.id === ownerId) return SW.brand.text('{project} owner');
      if (member.id && member.id === selfId) return 'You';
      return '';
    };

    const rows = h(
      'div',
      { className: 'sw-people-list' },
      members.map((member) => {
        const why = undeletable(member);
        return h(
          'div',
          { key: member.id, className: 'sw-people-row' },
          h(SW.Avatar, { user: member, size: 28 }),
          h(
            'div',
            { className: 'sw-people-main' },
            h('div', { className: 'sw-people-name' }, member.name),
            h('div', { className: 'sw-caption' }, member.title),
            (member.role || '').toLowerCase() === CANNOT_OPEN_APPS
              ? h('div', { className: 'sw-caption' },
                  SW.brand.text('Cannot open published {builtAppPlural}.'))
              : null
          ),
          // The raw platform value, never a word of Sage's: the roles differ in ways the Workbench
          // cannot restate, and renaming them here would invent a vocabulary Domino does not have.
          member.role ? h('span', { className: 'sw-people-role' }, member.role) : null,
          why
            ? h(Tooltip, { title: why }, h('span', { className: 'sw-caption' }, why))
            : h(
                Button,
                {
                  size: 'small',
                  type: 'text',
                  danger: true,
                  loading: removing === member.id,
                  onClick: () =>
                    Modal.confirm({
                      ...SW.PeopleModal.removalConfirm(member),
                      okText: 'Remove',
                      okButtonProps: { danger: true },
                      onOk: () => remove(member),
                    }),
                },
                'Remove'
              )
        );
      })
    );

    // Three states, never conflated. Not connected is not "nobody to add", and a read that failed
    // is neither — it is the only one of the three with something to try again.
    //
    // The failure is checked BEFORE the connection, because a read that failed says nothing about
    // whether Sage is on the platform: when our own server does not answer, `connected` is false
    // for want of an answer rather than because we know the answer is no. Testing it first would
    // turn "we could not look" into the flat claim that there is nothing to look at.
    let body;
    if (membersLoading) {
      body = h('div', { className: 'sw-caption' }, 'Reading…');
    } else if (membersError) {
      body = h(
        'div',
        { className: 'sw-people-error' },
        h(antd.Alert, {
          type: 'warning',
          showIcon: true,
          message: SW.brand.text('{assistantName} could not read who is on this {project}.'),
          // The platform's own sentence, passed through rather than restated.
          description: membersError,
        }),
        h(Button, { onClick: () => SW.store.reloadMembers() }, 'Try again')
      );
    } else if (!membersConnected) {
      body = h(Empty, {
        image: Empty.PRESENTED_IMAGE_SIMPLE,
        description: h('span', { className: 'sw-secondary' }, SW.brand.text(
          '{assistantName} is not running against {platformName}, so it cannot see who else works '
          + 'here.'
        )),
      });
    } else {
      body = h(
        'div',
        { style: { display: 'grid', gap: 14 } },
        h(
          'div',
          null,
          h('div', { className: 'sw-field-label' }, 'Add people'),
          h(Select, {
            mode: 'multiple',
            style: { width: '100%' },
            value: picked,
            onChange: setPicked,
            optionFilterProp: 'title',
            placeholder: candidates.length
              ? 'Type a name'
              : SW.brand.text('Everybody {platformName} lists is already here'),
            disabled: !candidates.length,
            options: candidates.map((m) => ({
              value: m.id,
              title: `${m.name} ${m.title}`,
              label: m.title ? `${m.name} · ${m.title}` : m.name,
            })),
          }),
          // No role picker. The roles differ in ways a creator cannot judge from the Workbench,
          // and one of them silently cannot open the App they are being added to see.
          h('div', { className: 'sw-caption' }, SW.brand.text(
            'Everyone is added as a contributor, and can open this {project} and any {builtApp} '
            + 'published from it right away.'
          ))
        ),
        h(
          'div',
          null,
          h('div', { className: 'sw-field-label' }, SW.brand.text('On this {project}')),
          rows
        )
      );
    }

    return h(
      Modal,
      {
        open: true,
        onCancel: close,
        title: SW.brand.text('People on {name}', { name: scope.name }),
        okText: picked.length > 1 ? `Add ${picked.length} people` : 'Add',
        confirmLoading: busy,
        okButtonProps: { disabled: picked.length === 0 },
        onOk: add,
      },
      body
    );
  };

  // Named rather than written inline in the handler, because it is the copy of a destructive
  // confirm and it has to be readable without clicking Remove to see it. One act, two effects, both
  // said: under GRANT_BASED visibility taking somebody off the Project is the same act that shuts
  // them out of the App published from it, and a creator may only have the first in mind.
  SW.PeopleModal.removalConfirm = (member) => ({
    title: SW.brand.text('Remove {name} from this {project}?', { name: member.name }),
    content: SW.brand.text(
      'They will lose access to this {project} and to any {builtApp} published from it.'
    ),
  });

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
