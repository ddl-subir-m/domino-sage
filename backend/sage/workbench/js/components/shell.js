window.SW = window.SW || {};

(function () {
  const { createElement: h, useState, useEffect, useRef } = React;
  const { Tooltip, Dropdown, Button, Space, Tag } = antd;
  const {
    SearchOutlined, QuestionCircleOutlined, AppstoreOutlined, DatabaseOutlined,
    HistoryOutlined, DoubleRightOutlined, DoubleLeftOutlined, DownOutlined,
  } = icons;

  // Project-scoped work. Everything here is read through the scope chip that
  // sits to its left.
  const MODES = [
    { id: 'chat', label: 'Chat', hint: 'Explore data and think out loud' },
    { id: 'build', label: 'Build', hint: 'Turn a plan into a working app' },
    { id: 'code', label: 'Code', hint: 'Work in your own editor' },
  ];

  // Concepts that span every project, so they live in the platform bar rather
  // than under a project scope.
  // The label is a template, resolved where the row is drawn: this list is built when the file is
  // evaluated, which is before GET /api/brand has answered.
  const GLOBAL_NAV = [
    { id: 'gallery', label: '{gallery}', hint: 'Find what your organization already built', path: '#/gallery' },
  ];

  // The panel is the project's working set, so it is named after the project
  // rather than after the abstract category of thing it contains.
  const DOCK_TABS = [
    { id: 'resources', label: 'Project resources', icon: DatabaseOutlined },
    { id: 'activity', label: 'Activity', icon: HistoryOutlined },
  ];

  // Switching mode should move you sideways, not send you back to the start.
  // Both modes are two views of one conversation, so the conversation is what
  // travels. Build adds the app it has in the preview.
  function modePath(id, state) {
    const { thread, activeApp } = state;
    if (id === 'chat') return thread ? `#/chat/${thread.id}` : '#/chat';
    if (id === 'build') {
      const app = activeApp ? `?app=${activeApp.id}` : '';
      return thread ? `#/build/${thread.id}${app}` : `#/build${app}`;
    }
    return `#/${id}`;
  }

  // Row 1: Domino platform chrome plus the global concepts. Deliberately
  // unchanged from the rest of the product so the workspace reads as part of
  // Domino, not a separate tool.
  function TopNav({ route }) {
    const { me, brand = {}, manageUrl } = SW.store.get();

    const product = SW.brand.product();
    // The other products this bar can switch to, from the pack (#115). Empty is the answer for a
    // partner who has only one product AND the answer before GET /api/brand has come back, and
    // both want the same thing: no switcher at all. A control offering a choice that does not
    // exist reads as broken, and a disabled one would need explaining.
    const peers = Array.isArray(brand.peerProducts) ? brand.peerProducts : [];
    const productMenu = {
      items: [{ key: 'workbench', label: product }].concat(
        peers.map((peer) => ({ key: peer.key, label: peer.label }))
      ),
      onClick: ({ key }) => {
        const peer = peers.find((item) => item.key === key);
        // The label is the partner's word, not ours, so it is dropped in beside the sentence
        // rather than through the token table — the same rule as a Resource the user named.
        if (peer) {
          antd.message.info(
            `${peer.label} ${SW.brand.text('is another {platformName} product. Only {productName} is built out here.')}`
          );
        }
      },
    };

    const userMenu = {
      items: [
        { key: 'account', label: 'Account settings' },
        { key: 'org', label: 'Organization' },
        { type: 'divider' },
        { key: 'signout', label: 'Sign out' },
      ],
      onClick: ({ key }) => {
        // What is the viewer's rather than the Project's belongs where a person already looks for
        // their own things, so Account settings is the door onto the preferences (#52). The other
        // two entries are still Domino screens this prototype does not stand in for.
        if (key === 'account') SW.store.set({ settingsOpen: true });
        else antd.message.info('Account screens are not part of this prototype.');
      },
    };

    return h(
      'div',
      { className: 'sw-topnav' },
      h('img', {
        src: brand.logoUrl || './img/domino-logo.svg',
        alt: brand.logoAlt || 'Domino',
        className: 'sw-logo',
      }),
      peers.length
        ? h(
            Dropdown,
            { menu: productMenu, trigger: ['click'] },
            h(
              'button',
              { className: 'sw-topnav-product' },
              product,
              h(DownOutlined, { style: { fontSize: 9 } })
            )
          )
        // Nowhere to go, so the product is a label and looks like one. The chip's fill and its
        // pointer are dropped inline because they live on a class and on its `:hover`, and an
        // inline declaration is what outranks a hover rule.
        : h(
            'span',
            {
              className: 'sw-topnav-product',
              style: { background: 'none', cursor: 'default' },
            },
            product
          ),
      h('span', { className: 'sw-topnav-sep' }),
      h(
        'nav',
        { className: 'sw-global-nav', 'aria-label': 'Everything across your organization' },
        GLOBAL_NAV.map((item) =>
          h(
            Tooltip,
            { key: item.id, title: item.hint, mouseEnterDelay: 0.5 },
            h(
              'button',
              {
                className: `sw-topnav-link${route.mode === item.id ? ' is-active' : ''}`,
                onClick: () => SW.router.go(item.path),
              },
              SW.brand.text(item.label)
            )
          )
        )
      ),
      h('span', { className: 'sw-topnav-spacer' }),
      // Manage is a Domino App of its own, not a mode: it reads across every project and gives an
      // admin more controls than a practitioner, neither of which fits under this bar's project
      // scope. So it sits with the account controls on the right — away from the modes that are
      // normal work — and leaves rather than routes. A new tab because leaving is a detour: the
      // build you were reading about is still here when you come back.
      // Hidden with no URL, which is a local run: a Manage that is not deployed is not a dead link.
      // The path arrives with no host and is resolved here, against the origin this page came from
      // — see SW.util.mainHostUrl. An override arrives absolute and passes straight through.
      manageUrl &&
        h(
          Tooltip,
          { title: 'Cost and app health across every project. Opens in a new tab.' },
          h(
            'a',
            {
              className: 'sw-topnav-link',
              href: SW.util.mainHostUrl(manageUrl),
              target: '_blank',
              rel: 'noreferrer',
            },
            'Manage'
          )
        ),
      h(
        Tooltip,
        { title: 'Search · ⌘K' },
        h(
          'button',
          {
            className: 'sw-icon-btn',
            'aria-label': 'Search',
            onClick: () => SW.store.set({ paletteOpen: true }),
          },
          h(SearchOutlined, null)
        )
      ),
      h(SW.NotificationBell, null),
      h(
        Tooltip,
        { title: 'Help' },
        h(
          'button',
          { className: 'sw-icon-btn', 'aria-label': 'Help', onClick: () => SW.store.set({ helpOpen: true }) },
          h(QuestionCircleOutlined, null)
        )
      ),
      h(
        Dropdown,
        { menu: userMenu, trigger: ['click'], placement: 'bottomRight' },
        h(
          'button',
          { className: 'sw-topnav-user', 'aria-label': 'Account' },
          h(SW.Avatar, { user: me, size: 26 })
        )
      )
    );
  }

  // Clicking a mode replaces the whole main area, and the pointer never leaves
  // the button, so nothing fires the mouseleave that would dismiss the hint —
  // it sat over the header of whichever mode you had just opened. Once you have
  // clicked, hints stay shut until you actually move away and come back.
  function ModeTab({ item, active, onGo }) {
    const [open, setOpen] = useState(false);
    const clicked = useRef(false);

    return h(
      Tooltip,
      {
        title: item.hint,
        mouseEnterDelay: 0.7,
        open,
        onOpenChange: (next) => {
          if (next && clicked.current) return;
          setOpen(next);
        },
      },
      h(
        'button',
        {
          role: 'tab',
          'aria-selected': active,
          className: `sw-mode${active ? ' is-active' : ''}`,
          onMouseLeave: () => {
            clicked.current = false;
          },
          onClick: () => {
            clicked.current = true;
            setOpen(false);
            onGo();
          },
        },
        item.label
      )
    );
  }

  // Row 2: project-scoped navigation. Scope on the left because everything to
  // its right is scoped by it.
  function SubNav({ mode }) {
    const [pickerOpen, setPickerOpen] = useState(false);
    const state = SW.store.get();
    const { scopePickerOpen, dockTab } = state;

    useEffect(() => {
      if (scopePickerOpen) {
        setPickerOpen(true);
        SW.store.set({ scopePickerOpen: false });
      }
    }, [scopePickerOpen]);

    return h(
      'div',
      { className: 'sw-subnav' },
      h(SW.ScopePicker, { open: pickerOpen, onOpenChange: setPickerOpen }),
      h('span', { className: 'sw-subnav-divider' }),
      h(
        'nav',
        { className: 'sw-modes', role: 'tablist' },
        MODES.map((item) =>
          h(ModeTab, {
            key: item.id,
            item,
            active: mode === item.id,
            onGo: () => SW.router.go(item.path || modePath(item.id, state)),
          })
        )
      ),
      h('span', { className: 'sw-topnav-spacer' }),
      h(SW.PresenceStack, { onInvite: () => SW.store.set({ inviteOpen: true }) }),
      h('span', { className: 'sw-subnav-divider' }),
      h(
        Tooltip,
        { title: dockTab ? 'Hide the side panel' : 'Show resources · ⌘/' },
        h(
          'button',
          {
            className: 'sw-icon-btn is-dark-text',
            'aria-label': 'Toggle side panel',
            onClick: () => SW.store.toggleDock('resources'),
          },
          h(dockTab ? DoubleRightOutlined : DoubleLeftOutlined, null)
        )
      )
    );
  }

  function Dock() {
    const { dockTab } = SW.store.get();

    if (!dockTab) {
      return h(
        'aside',
        { className: 'sw-dock is-collapsed' },
        DOCK_TABS.map((tab) =>
          h(
            Tooltip,
            { key: tab.id, title: tab.label, placement: 'left' },
            h(
              'button',
              {
                className: 'sw-dock-rail-btn',
                'aria-label': tab.label,
                onClick: () => SW.store.toggleDock(tab.id),
              },
              h(tab.icon, null)
            )
          )
        )
      );
    }

    return h(
      'aside',
      { className: 'sw-dock is-expanded' },
      h(
        'div',
        { className: 'sw-dock-tabs' },
        DOCK_TABS.map((tab) =>
          h(
            'button',
            {
              key: tab.id,
              className: `sw-dock-tab${dockTab === tab.id ? ' is-active' : ''}`,
              onClick: () => SW.store.set({ dockTab: tab.id }),
            },
            tab.label
          )
        ),
        h('span', { className: 'sw-topnav-spacer' }),
        h(
          Tooltip,
          { title: 'Hide panel' },
          h(
            'button',
            {
              className: 'sw-icon-btn is-dark-text',
              'aria-label': 'Hide panel',
              onClick: () => SW.store.set({ dockTab: null, panelFilter: null }),
            },
            h(DoubleRightOutlined, null)
          )
        )
      ),
      h(
        'div',
        { className: 'sw-dock-body' },
        dockTab === 'resources'
          ? h(SW.ResourcePanel, null)
          : h(
              'div',
              { className: 'sw-dock-activity sw-scroll' },
              h(SW.ActivityFeed, null)
            )
      )
    );
  }

  SW.Shell = function Shell({ mode, route, children }) {
    return h(
      'div',
      { className: 'sw-app' },
      h(TopNav, { route }),
      h(SubNav, { mode }),
      h(
        'div',
        { className: 'sw-body' },
        h('main', { className: 'sw-main' }, children),
        h(Dock, null)
      ),
      h(SW.ResourceCatalog, null),
      h(SW.ResourceDrawer, null),
      h(SW.HandoffSheet, null),
      h(SW.GraduationModal, null),
      h(SW.InviteModal, null),
      h(SW.CommandPalette, null),
      h(SW.ModelAssignmentsDrawer, null),
      h(SW.SettingsDrawer, null),
      h(SW.HelpDrawer, null)
    );
  };

  // Preferences describe how this person works, not how the Project is set up, so a collaborator
  // changing theirs never changes yours and the answer follows you into every Project (#52).
  //
  // What a handoff CARRIES lives here (#58): it was four checkboxes the sheet rebuilt from the same
  // defaults on every build, so the same person answered the same questions every time. Where a
  // handoff LANDS does not and never will, because preselecting an app nobody chose is how a build
  // silently overwrites an existing one (#73, ADR-0008) — the sheet still asks that, every time.
  SW.SettingsDrawer = function SettingsDrawer() {
    const { settingsOpen } = SW.store.get();
    const [conversationView, setConversationView] = useState('split');
    const [crossings, setCrossings] = useState({
      handoffResources: true,
      handoffArtifacts: true,
      handoffTranscript: false,
    });

    // Read on open rather than once at mount, so a choice made in another tab is not overwritten
    // by a stale copy of this one.
    useEffect(() => {
      if (!settingsOpen) return;
      setConversationView(SW.prefs.get('conversationView'));
      setCrossings({
        handoffResources: SW.prefs.get('handoffResources'),
        handoffArtifacts: SW.prefs.get('handoffArtifacts'),
        handoffTranscript: SW.prefs.get('handoffTranscript'),
      });
    }, [settingsOpen]);

    // A browser that refuses storage takes the click and forgets it. Saying so is better than a
    // control that looks settled and resets on the next load. One writer for every control here,
    // because that is true of all of them.
    const save = (name, value) => {
      if (!SW.prefs.set(name, value)) {
        antd.message.warning('This browser is not storing the choice, so it will not be here next time.');
      }
    };

    const choose = (value) => {
      setConversationView(value);
      save('conversationView', value);
    };

    const carry = (name) => (e) => {
      setCrossings({ ...crossings, [name]: e.target.checked });
      save(name, e.target.checked);
    };

    return h(
      antd.Drawer,
      {
        open: Boolean(settingsOpen),
        onClose: () => SW.store.set({ settingsOpen: false }),
        title: 'Account settings',
        width: 360,
      },
      h(
        'div',
        { className: 'sw-setting' },
        h('div', { className: 'sw-setting-label' }, 'Conversation view'),
        h(
          'p',
          { className: 'sw-setting-hint' },
          SW.brand.text(
            'Split keeps Chat and Build as separate halves of a conversation, the way '
            + '{productName} works today. Unified shows one transcript in both.'
          )
        ),
        h(antd.Radio.Group, {
          // antd renders the group as a plain div, so without the role the label is not announced
          // and the buttons read as two loose radios belonging to nothing.
          role: 'radiogroup',
          'aria-label': 'Conversation view',
          value: conversationView,
          onChange: (e) => choose(e.target.value),
          optionType: 'button',
          options: [
            { label: 'Split', value: 'split' },
            { label: 'Unified', value: 'unified' },
          ],
        })
      ),
      h(
        'div',
        { className: 'sw-setting' },
        h('div', { className: 'sw-setting-label' }, 'What a build carries across'),
        h(
          'p',
          { className: 'sw-setting-hint' },
          'When you build an app from a conversation, this is what crosses with the plan. Building ',
          'still asks which app to build into.'
        ),
        h(
          Space,
          {
            direction: 'vertical',
            size: 6,
            // antd renders Space as a plain div, so without these the three boxes read as loose
            // checkboxes belonging to nothing — the same gap the Radio.Group above has to fill.
            role: 'group',
            'aria-label': 'What a build carries across',
          },
          h(
            antd.Checkbox,
            { checked: crossings.handoffResources, onChange: carry('handoffResources') },
            h(
              'span',
              null,
              'What is in the conversation',
              h('span', { className: 'sw-caption' }, ' · becomes what the app needs')
            )
          ),
          h(
            antd.Checkbox,
            { checked: crossings.handoffArtifacts, onChange: carry('handoffArtifacts') },
            'Charts and outputs from the conversation'
          ),
          h(
            antd.Checkbox,
            { checked: crossings.handoffTranscript, onChange: carry('handoffTranscript') },
            'The full conversation transcript'
          )
        )
      ),
      h(
        'p',
        { className: 'sw-setting-scope' },
        'These settings are yours. They follow you into every Project, and they do not change ',
        'what anyone else sees.'
      )
    );
  };

  SW.HelpDrawer = function HelpDrawer() {
    const { helpOpen } = SW.store.get();
    return h(
      antd.Drawer,
      {
        open: Boolean(helpOpen),
        onClose: () => SW.store.set({ helpOpen: false }),
        title: 'Keyboard shortcuts',
        width: 360,
      },
      h(
        'dl',
        { className: 'sw-drawer-meta' },
        h('dt', null, h('kbd', null, '⌘K')),
        h('dd', null, 'Search everything'),
        h('dt', null, h('kbd', null, '⌘P')),
        h('dd', null, 'Switch project'),
        h('dt', null, h('kbd', null, '⌘/')),
        h('dd', null, 'Toggle the side panel'),
        h('dt', null, h('kbd', null, '⌘⏎')),
        h('dd', null, 'Send a message'),
        h('dt', null, h('kbd', null, '⌘⇧N')),
        h('dd', null, 'New conversation'),
        h('dt', null, h('kbd', null, 'esc')),
        h('dd', null, 'Close what is open')
      )
    );
  };
})();
