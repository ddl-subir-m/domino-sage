window.SW = window.SW || {};

(function () {
  const { createElement: h, useState, useEffect, useRef } = React;
  const { Tooltip, Dropdown, Space } = antd;
  const {
    SearchOutlined, QuestionCircleOutlined, DatabaseOutlined,
    DownOutlined, WarningOutlined,
  } = icons;

  // Project-scoped work. Everything here is read through the scope chip that
  // sits to its left.
  const MODES = [
    { id: 'chat', label: '{chat}', hint: 'Explore data and think out loud' },
    // "Turn" was the first word here until ADR-0026 gave `Turn` a noun key. It was the
    // English verb, but the check matches a whole word and cannot tell the two apart, so
    // the copy moves rather than the rule.
    { id: 'build', label: 'Build', hint: 'Go from a plan to a working app' },
    { id: 'code', label: 'Code', hint: 'Work in your own editor' },
  ];

  // Concepts that span every project, so they live in the platform bar rather
  // than under a project scope.
  // The label is a template, resolved where the row is drawn: this list is built when the file is
  // evaluated, which is before GET /api/brand has answered.
  const GLOBAL_NAV = [
    { id: 'gallery', label: '{gallery}', hint: 'Find what your organization already built', path: '#/gallery' },
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

  // The chip (ADR-0027). Row 1 and on the right, with the account controls and Manage, for the
  // reason the Manage link below states for itself: what is scoped to the DEPLOYMENT belongs away
  // from the modes that are normal work, and away from Row 2, which is scoped to one project. A
  // Problem outlives the project you are standing in and follows you into the next one.
  //
  // It draws NOTHING when there is nothing wrong, which is what earns a standing fault a permanent
  // home in a bar this crowded — and it goes silent by itself when the fault clears. There is no
  // dismiss, because dismissal is for repetition and one element that is either lit or absent does
  // not repeat. A chip somebody could hide is a dead model slot somebody can hide, and then report
  // the failed build it caused as a bug.
  //
  // Icon-only, so the count is in the tooltip and in the label a screen reader gets. It is a button
  // and nothing else: opening the drawer is the whole of what it does, and no control anywhere goes
  // grey because it is lit.
  function ProblemChip() {
    const { problems } = SW.store.get();
    const found = Array.isArray(problems) ? problems : [];
    if (!found.length) return null;

    const label = found.length === 1
      ? '1 problem needs your attention'
      : `${found.length} problems need your attention`;
    return h(
      Tooltip,
      { title: `${label}. Open to read ${found.length === 1 ? 'it' : 'them'}.` },
      h(
        'button',
        {
          // `sw-icon-btn` is the shared 30px target every other control on this row uses, which is
          // over the 24px minimum an icon-only control owes a pointer.
          className: 'sw-icon-btn sw-problem-chip',
          'aria-label': label,
          onClick: () => SW.store.openProblems(true),
        },
        h(WarningOutlined, null)
      )
    );
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
      // First in the right-hand cluster: it is absent almost always, and when it is not it is the
      // most urgent thing on the row. Ahead of Manage rather than beside the bell, because the bell
      // reports on the reader's own work and this reports on the deployment underneath it.
      h(ProblemChip, null),
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
        { title: `Search · ${SW.util.shortcut('⌘K')}` },
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
        SW.brand.text(item.label)
      )
    );
  }

  // Row 2: project-scoped navigation. Scope on the left because everything to
  // its right is scoped by it.
  function SubNav({ mode }) {
    const [pickerOpen, setPickerOpen] = useState(false);
    const state = SW.store.get();
    const { scopePickerOpen } = state;

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
      // No panel toggle here. There were two of them within about sixty pixels of each other —
      // this one and the panel's own Hide button — in different containers, drawn with the same
      // chevron, doing the same thing. The one that survives is the one that sits ON the thing it
      // acts on. Re-opening a hidden panel is the collapsed dock's own button, a few pixels below
      // where this was, plus ⌘/, which the help drawer already advertises.
    );
  }

  // One panel, so no tab bar. The dock is the frame; `SW.ResourcePanel` draws its own heading, its
  // Add button and the one control that hides it. `dockTab` is still the state key and still holds
  // `'resources'` or `null`, because it is a remembered preference already written into people's
  // records (#150) — what is gone is the second value it could hold.
  function Dock() {
    const { dockTab } = SW.store.get();

    if (!dockTab) {
      return h(
        'aside',
        { className: 'sw-dock is-collapsed' },
        h(
          Tooltip,
          { title: `Show resources · ${SW.util.shortcut('⌘/')}`, placement: 'left' },
          h(
            'button',
            {
              className: 'sw-dock-rail-btn',
              'aria-label': 'Show resources',
              onClick: () => SW.store.openDock(),
            },
            h(DatabaseOutlined, null)
          )
        )
      );
    }

    return h(
      'aside',
      { className: 'sw-dock is-expanded' },
      h(SW.ResourcePanel, null)
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
      h(SW.ProblemsDrawer, null),
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
              'What is in the conversation'
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
        h('dt', null, h('kbd', null, SW.util.shortcut('⌘K'))),
        h('dd', null, 'Search everything'),
        h('dt', null, h('kbd', null, SW.util.shortcut('⌘P'))),
        h('dd', null, 'Switch project'),
        // The two panels, and the mnemonic they are picked for: `/` leans right and opens the
        // panel on the right, `\` leans left and opens the one on the left. Chrome claims
        // neither, on either platform.
        h('dt', null, h('kbd', null, SW.util.shortcut('⌘/'))),
        h('dd', null, 'Toggle the side panel'),
        h('dt', null, h('kbd', null, SW.util.shortcut('⌘\\'))),
        // "Toggle your conversations", not "the Rail". ADR-0026 says a name owes a key the moment
        // a marked position says it, and this diff is what makes `Rail` a name — but there is no
        // `rail` key in the pack, and an unmarked string is one the lint cannot catch. The row
        // above dodges its own noun the same way: "the side panel", never "the Dock".
        h('dd', null, 'Toggle your conversations'),
        h('dt', null, h('kbd', null, SW.util.shortcut('⌘⏎'))),
        h('dd', null, 'Send a message'),
        // Left in Mac notation on purpose. Chrome owns Ctrl+Shift+N for a new incognito window,
        // so translating this one would print a Windows label for a shortcut that may never
        // reach the page. Nobody has checked on live Windows Chrome yet, and a label is a
        // promise — so this row stays honest about the platform it was verified on.
        h('dt', null, h('kbd', null, '⌘⇧N')),
        h('dd', null, 'New conversation'),
        h('dt', null, h('kbd', null, 'esc')),
        h('dd', null, 'Close what is open')
      )
    );
  };
})();
