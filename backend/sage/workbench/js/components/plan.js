window.SW = window.SW || {};

(function () {
  const { createElement: h, useState, useEffect, Fragment } = React;
  const {
    Button, Space, Tag, Modal, Input, Tooltip, Avatar, Select, Divider, Alert, Checkbox,
  } = antd;
  const {
    CheckCircleFilled, MessageOutlined, EditOutlined, ArrowRightOutlined,
    ClockCircleOutlined, PlusOutlined, CloseOutlined,
  } = icons;

  const SECTIONS = [
    { key: 'problem', label: 'Problem & outcome', kind: 'text' },
    { key: 'users', label: 'Who uses this', kind: 'text' },
    { key: 'outcomes', label: 'What it does', kind: 'list' },
    { key: 'screens', label: 'Screens', kind: 'screens' },
    { key: 'nonGoals', label: 'Not doing', kind: 'list' },
    { key: 'acceptance', label: 'Done when', kind: 'list' },
    // The build steps, kept as markdown rather than broken into fields: this is the section
    // plan_steps.parse_steps reads out of the file, and a phased plan writes sub-headings under it
    // that no list of strings would survive. Same order as plan_doc.SECTIONS on the server.
    { key: 'plan', label: 'Plan', kind: 'markdown' },
    { key: 'openQuestions', label: 'Open questions', kind: 'questions' },
  ];

  // The card that appears in the conversation. Deliberately a summary — the
  // full artifact lives on its own page.
  SW.PlanCard = function PlanCard({ planId }) {
    const [plan, setPlan] = useState(null);

    useEffect(() => {
      SW.api.plan(planId).then(setPlan);
    }, [planId]);

    if (!plan) return h('div', { className: 'sw-plan-card' }, h(antd.Skeleton, { active: true, paragraph: { rows: 3 } }));

    const outcomes = plan.sections.outcomes || [];
    const questions = (plan.sections.openQuestions || []).filter((q) => !q.resolved);

    return h(
      'div',
      { className: 'sw-plan-card' },
      h(
        'div',
        { className: 'sw-plan-card-head' },
        h('div', { className: 'sw-plan-card-title' }, plan.title),
        h(SW.PlanStatusTag, { status: plan.status }),
        h('span', { className: 'sw-caption' }, `v${plan.version}`)
      ),
      h('div', { className: 'sw-plan-card-problem' }, plan.sections.problem),
      outcomes.length > 0 &&
        h(
          'ul',
          { className: 'sw-plan-card-list' },
          outcomes.slice(0, 3).map((item, i) => h('li', { key: i }, item)),
          outcomes.length > 3 &&
            h('li', { key: 'more', className: 'sw-caption' }, `+${outcomes.length - 3} more`)
        ),
      questions.length > 0 &&
        h(
          'div',
          { className: 'sw-plan-card-questions' },
          h(ClockCircleOutlined, null),
          `${questions.length} open ${questions.length === 1 ? 'question' : 'questions'}`
        ),
      h(
        'div',
        { className: 'sw-plan-card-actions' },
        h(
          Button,
          { type: 'primary', size: 'small', onClick: () => SW.store.openPlanArtifact(plan.id) },
          'Open plan'
        ),
        h(
          Button,
          {
            size: 'small',
            onClick: () => SW.store.draftHandoffPlan(),
          },
          'Build this'
        ),
        h(
          Button,
          {
            type: 'text',
            size: 'small',
            onClick: () => SW.router.go(`#/plan/${plan.id}?review=1`),
          },
          'Send for review'
        )
      )
    );
  };

  function CommentThread({ plan, section, onPost }) {
    const [text, setText] = useState('');
    const { userIndex } = SW.store.get();
    const comments = (plan.comments || []).filter((c) => c.section === section);

    return h(
      'div',
      { className: 'sw-comments' },
      comments.map((comment) =>
        h(
          'div',
          { key: comment.id, className: `sw-comment${comment.resolved ? ' is-resolved' : ''}` },
          h(SW.Avatar, { user: userIndex[comment.user], size: 20 }),
          h(
            'div',
            { className: 'sw-comment-main' },
            h(
              'div',
              { className: 'sw-comment-who' },
              (userIndex[comment.user] || {}).name,
              h('span', { className: 'sw-caption' }, SW.util.relativeTime(comment.at)),
              comment.resolved && h(Tag, { bordered: false, color: 'success' }, 'Resolved')
            ),
            h('div', { className: 'sw-comment-text' }, comment.text),
            !comment.resolved &&
              h(
                Button,
                {
                  type: 'link',
                  size: 'small',
                  style: { padding: 0, height: 18 },
                  onClick: () => onPost({ resolve: comment.id }),
                },
                'Resolve'
              )
          )
        )
      ),
      h(
        'div',
        { className: 'sw-comment-new' },
        h(Input, {
          size: 'small',
          placeholder: 'Add a comment…',
          value: text,
          onChange: (e) => setText(e.target.value),
          onPressEnter: () => {
            if (!text.trim()) return;
            onPost({ text: text.trim() });
            setText('');
          },
        })
      )
    );
  }

  function ReviewModal({ plan, open, onClose, onSent }) {
    const { members } = SW.store.get();
    const [reviewers, setReviewers] = useState(plan.reviewers || []);
    const [note, setNote] = useState('');

    return h(
      Modal,
      {
        open,
        onCancel: onClose,
        title: 'Send plan for review',
        okText: 'Send for review',
        okButtonProps: { disabled: reviewers.length === 0 },
        onOk: async () => {
          await SW.api.review(plan.id, { action: 'request', reviewers, note });
          onClose();
          onSent();
          antd.message.success(
            `Sent to ${reviewers.length} ${reviewers.length === 1 ? 'reviewer' : 'reviewers'}`
          );
        },
      },
      h(
        'div',
        { style: { display: 'grid', gap: 12 } },
        h(
          'div',
          null,
          h('div', { className: 'sw-field-label' }, 'Reviewers'),
          h(Select, {
            mode: 'multiple',
            style: { width: '100%' },
            value: reviewers,
            onChange: setReviewers,
            placeholder: 'Pick people who should sign off',
            options: members
              .filter((m) => m.id !== plan.author)
              .map((m) => ({ value: m.id, label: `${m.name} · ${m.title}` })),
          })
        ),
        h(
          'div',
          null,
          h('div', { className: 'sw-field-label' }, 'Note (optional)'),
          h(Input.TextArea, {
            rows: 3,
            value: note,
            onChange: (e) => setNote(e.target.value),
            placeholder: 'Anything you want them to look at closely?',
          })
        ),
        h('div', { className: 'sw-caption' }, 'Reviewers can comment on any section. You can build before they finish.')
      )
    );
  }

  // One plan document, two homes: its own page, and a sheet beside the work. `variant` says which
  // home, and it says nothing about what the sheet stands next to — Chat and Build mount the same
  // 'side' sheet. What Build adds is read off the MODE below, because that is the difference: a
  // preview on the other side of the sheet, and a builder reading it.
  SW.PlanDoc = function PlanDoc({ planId, variant = 'page', autoReview, onClose }) {
    const { userIndex, me, activeApp } = SW.store.get();
    const [plan, setPlan] = useState(null);
    const [openSection, setOpenSection] = useState(null);
    const [reviewOpen, setReviewOpen] = useState(false);
    const [editing, setEditing] = useState(null);
    const [draft, setDraft] = useState('');
    const [view, setView] = useState('Preview');
    const [markdown, setMarkdown] = useState(null);

    // Build's sheet is the builder's copy of the document: it offers the raw file behind the
    // preview, and it knows which app that preview holds. Both used to ask for a fourth variant,
    // 'ide', that nothing ever passed — so the raw file was unreachable from anywhere and the
    // app offer was never withheld. The mode is the question in both cases.
    const inBuild = SW.router.get().mode === 'build';

    const load = () => SW.api.plan(planId).then(setPlan);
    useEffect(() => {
      load();
    }, [planId]);
    useEffect(() => {
      if (autoReview && plan) setReviewOpen(true);
    }, [autoReview, Boolean(plan)]);

    // Only Build offers the raw file, so only Build ever fetches it.
    useEffect(() => {
      if (!inBuild || view !== 'Markdown') return;
      SW.api.planMarkdown(planId).then(setMarkdown);
    }, [inBuild, view, planId, plan && plan.updatedAt]);

    if (!plan) {
      return h(
        'div',
        { className: `sw-plan-page is-${variant}` },
        h(antd.Skeleton, { active: true, paragraph: { rows: 12 } })
      );
    }

    const author = userIndex[plan.author] || {};
    // `me` is null until the shell has asked who is viewing, and the plan page is now
    // reachable from a link, so it can render first.
    const meId = (me || {}).id;
    const isReviewer = (plan.reviewers || []).includes(meId);
    const approved = (plan.approvals || []).map((a) => a.user);
    const unresolved = (plan.comments || []).filter((c) => !c.resolved).length;
    // Everything the plan says above its first heading. A plan written to shape opens with one
    // sentence, which is already the title, so this renders nothing. A plan the parser found no
    // headings in lands here whole — and without this the page showed that title over eight empty
    // sections while the transcript showed the same plan in full.
    const lead = (plan.summary || '').trim();

    const post = async (section, body) => {
      if (body.resolve) {
        await SW.api.review(plan.id, { action: 'resolve', commentId: body.resolve });
      } else {
        await SW.api.review(plan.id, { action: 'comment', section, text: body.text });
      }
      load();
    };

    const saveSection = async (key) => {
      const section = SECTIONS.find((s) => s.key === key);
      const value =
        section.kind === 'list'
          ? draft.split('\n').map((l) => l.replace(/^[-•]\s*/, '')).filter(Boolean)
          : draft;
      await SW.api.patchPlan(plan.id, { sections: { ...plan.sections, [key]: value } });
      setEditing(null);
      load();
    };

    const startEdit = (key, kind, value) => {
      setEditing(key);
      setDraft(kind === 'list' ? (value || []).join('\n') : value || '');
    };

    const renderBody = (section) => {
      const value = plan.sections[section.key];
      if (editing === section.key) {
        return h(
          'div',
          { style: { display: 'grid', gap: 8 } },
          h(Input.TextArea, {
            autoSize: { minRows: 3, maxRows: 12 },
            value: draft,
            autoFocus: true,
            onChange: (e) => setDraft(e.target.value),
          }),
          h(
            Space,
            { size: 8 },
            h(Button, { type: 'primary', size: 'small', onClick: () => saveSection(section.key) }, 'Save'),
            h(Button, { size: 'small', onClick: () => setEditing(null) }, 'Cancel')
          )
        );
      }

      switch (section.kind) {
        case 'list':
          return h('ul', { className: 'sw-plan-list' }, (value || []).map((item, i) => h('li', { key: i }, item)));
        case 'screens':
          return h(
            'div',
            { className: 'sw-plan-screens' },
            (value || []).map((screen, i) =>
              h(
                'div',
                { key: i, className: 'sw-plan-screen' },
                h('div', { className: 'sw-plan-screen-name' }, screen.name),
                h('div', { className: 'sw-plan-screen-detail' }, screen.detail)
              )
            )
          );
        case 'questions':
          return h(
            'div',
            { className: 'sw-plan-questions' },
            (value || []).map((q, i) =>
              h(
                'div',
                { key: i, className: `sw-plan-question${q.resolved ? ' is-resolved' : ''}` },
                h(Checkbox, {
                  checked: Boolean(q.resolved),
                  onChange: async (e) => {
                    const next = (value || []).map((item, index) =>
                      index === i ? { ...item, resolved: e.target.checked } : item
                    );
                    await SW.api.patchPlan(plan.id, {
                      sections: { ...plan.sections, openQuestions: next },
                    });
                    load();
                  },
                }),
                h('span', null, q.text)
              )
            )
          );
        case 'markdown':
          return h('div', { className: 'sw-plan-md' }, SW.util.markdown(value || ''));
        default:
          return h('p', { className: 'sw-plan-text' }, value);
      }
    };

    const isPage = variant === 'page';

    // Whether the plan's Built App is the one already in the preview, which is the only reason to
    // withhold the offer below. It is a mode-and-app question, not a variant one: the sheet is the
    // same document in Chat and in Build, and what changes between them is what is on screen
    // beside it. Read the same way the transcript's app card reads it (`message-blocks.js`), so
    // the two agree about which app you are looking at.
    const showingApp = inBuild && activeApp && plan.appId && activeApp.id === plan.appId;

    return h(
      'div',
      { className: `sw-plan-page is-${variant} sw-scroll` },
      h(
        'div',
        { className: 'sw-plan-doc' },

        !isPage &&
          h(
            'div',
            { className: 'sw-plan-sheet-bar' },
            h(Tag, { bordered: false, className: 'sw-sens sw-blessed-tag' }, 'PLAN'),
            h('span', { className: 'sw-caption' }, 'Document · open beside your work'),
            h('span', { style: { flex: 1 } }),
            inBuild &&
              h(antd.Segmented, {
                value: view,
                onChange: setView,
                options: ['Preview', 'Markdown'],
                size: 'small',
              }),
            h(
              Tooltip,
              { title: 'Close' },
              h(
                'button',
                { className: 'sw-icon-btn is-dark-text', 'aria-label': 'Close plan', onClick: onClose },
                h(CloseOutlined, null)
              )
            )
          ),

        h(
          'div',
          { className: 'sw-plan-head' },
          h(
            'div',
            { className: 'sw-plan-head-main' },
            h('h1', { className: 'sw-plan-title' }, plan.title),
            h(
              'div',
              { className: 'sw-plan-meta' },
              h(SW.PlanStatusTag, { status: plan.status }),
              h('span', null, `v${plan.version}`),
              h('span', null, '·'),
              h(SW.Avatar, { user: author, size: 20 }),
              h('span', null, author.name),
              h('span', null, '·'),
              h('span', null, `Updated ${SW.util.relativeTime(plan.updatedAt)}`),
              // One of the plan's two back-links, and the one both entry paths now record (#54).
              // The other end is the Built App, offered by the action below. Neither implies the
              // other — a plan may carry either, both, or neither — so this link is drawn off the
              // origin alone and never off `appId`.
              //
              // Through the rail's own route grammar rather than a hardcoded `#/chat/`, so the
              // conversation opens in the mode you are reading the plan from. That matters in
              // Build's sheet: the same Thread is a Chat conversation and a Build conversation,
              // and a plan the gate wrote came from the Build half, whose turns Chat does not
              // show. On its own page there is no mode to read, so it still opens in Chat.
              plan.originThreadId &&
                h(
                  Fragment,
                  null,
                  h('span', null, '·'),
                  h(
                    Button,
                    {
                      type: 'link',
                      size: 'small',
                      style: { padding: 0, height: 'auto' },
                      onClick: () =>
                        SW.router.go(
                          SW.conversationRoute({ id: plan.originThreadId }, SW.router.get().mode)
                        ),
                    },
                    'From this conversation'
                  )
                )
            )
          ),
          h(
            Space,
            { size: 8 },
            plan.status === 'draft' &&
              h(Button, { onClick: () => setReviewOpen(true) }, 'Send for review'),
            isReviewer &&
              plan.status === 'in_review' &&
              !approved.includes(meId) &&
              h(
                Button,
                {
                  onClick: async () => {
                    await SW.api.review(plan.id, { action: 'approve', user: meId });
                    load();
                    antd.message.success('Plan approved');
                  },
                },
                'Approve'
              ),
            // Withheld only when the plan's app is the one already in the preview: you are
            // looking at it, so "open" it leads back to the page you are on. Open a plan for a
            // DIFFERENT app while in Build and the offer is real again — it switches apps.
            //
            // This asked `variant === 'ide'` until now, a fourth home for the document that was
            // never built and that nothing ever passed, so the test never matched and Build always
            // drew the dead button. The question was never which home the document is in.
            //
            // Two different offers wearing one button, and which one it is turns on whether the
            // plan already stands in a Built App. With an app there is nothing to hand off — the
            // app exists — so the offer is the way back INTO it. Without one, the offer is the
            // handoff that would make it.
            //
            // The plan's OWN Conversation, not whichever one happens to be open: handing off is
            // something a Conversation does, and the no-argument call handed off the current one,
            // so opening an old plan and pressing this built from a conversation you weren't
            // reading. Since #54 both entry paths record an origin, so the disabled case is no
            // longer "written in Build" — it is a document nobody wrote in a conversation, the
            // blank one the plan list hands you.
            !showingApp &&
              (plan.appId
                ? h(
                    Button,
                    {
                      type: 'primary',
                      icon: h(ArrowRightOutlined, null),
                      // Through the rail's own route grammar, which keeps the open conversation
                      // in the route. A bare `#/build?app=` names none, and BuildMode reads that
                      // as "a new one" and clears the transcript — so opening the app from the
                      // sheet beside a Build conversation would throw that conversation away.
                      onClick: () => SW.router.go(SW.appRoute({ id: plan.appId })),
                    },
                    'Open in Builder'
                  )
                : h(
                    Tooltip,
                    {
                      title: plan.originThreadId
                        ? null
                        : 'This plan has no conversation on record, so there is nothing to hand ' +
                          'off from. Ask for it in a conversation to build it.',
                    },
                    // A disabled button fires no mouse events, so the tooltip needs something
                    // around it that does.
                    h(
                      'span',
                      null,
                      h(
                        Button,
                        {
                          type: 'primary',
                          icon: h(ArrowRightOutlined, null),
                          disabled: !plan.originThreadId,
                          onClick: () => SW.store.draftHandoffPlan(plan.originThreadId),
                        },
                        'Build this'
                      )
                    )
                  ))
          )
        ),

        plan.status === 'in_review' &&
          h(Alert, {
            type: 'info',
            showIcon: true,
            style: { marginBottom: 16 },
            message: approved.length
              ? `${approved.length} of ${plan.reviewers.length} approved.`
              : 'Waiting on review.',
            description: h(
              Space,
              { size: 6, wrap: true },
              (plan.reviewers || []).map((id) =>
                h(
                  Tag,
                  {
                    key: id,
                    bordered: false,
                    color: approved.includes(id) ? 'success' : undefined,
                    icon: approved.includes(id) ? h(CheckCircleFilled, null) : undefined,
                  },
                  (userIndex[id] || {}).name
                )
              ),
              unresolved > 0 &&
                h('span', { className: 'sw-caption' }, `${unresolved} unresolved ${unresolved === 1 ? 'comment' : 'comments'}`)
            ),
          }),

        lead && lead !== plan.title &&
          h('div', { className: 'sw-plan-lead sw-plan-md' }, SW.util.markdown(lead)),

        inBuild && view === 'Markdown'
          ? h(
              'div',
              { className: 'sw-plan-markdown' },
              h(
                'div',
                { className: 'sw-editor-head' },
                h('code', null, markdown ? markdown.path : 'plan.md'),
                h('span', { className: 'sw-topnav-spacer' }),
                markdown &&
                  h(
                    Button,
                    { size: 'small', onClick: () => SW.util.copy(markdown.content, 'plan.md copied') },
                    'Copy'
                  )
              ),
              markdown
                ? h('pre', { className: 'sw-code' }, SW.util.highlight(markdown.content, 'markdown'))
                : h(antd.Skeleton, { active: true, paragraph: { rows: 10 } }),
              h(
                'div',
                { className: 'sw-caption', style: { marginTop: 8 } },
                'Switch back to Preview to edit. The preview writes straight to this file.'
              )
            )
          : SECTIONS.map((section) => {
              const comments = (plan.comments || []).filter((c) => c.section === section.key);
              const open = openSection === section.key;
              return h(
                'section',
                { key: section.key, className: 'sw-plan-section' },
                h(
                  'div',
                  { className: 'sw-plan-section-head' },
                  h('h2', null, section.label),
                  h(
                    Space,
                    { size: 2, className: 'sw-plan-section-tools' },
                    section.kind !== 'questions' &&
                      section.kind !== 'screens' &&
                      h(
                        Tooltip,
                        { title: 'Edit' },
                        h(Button, {
                          type: 'text',
                          size: 'small',
                          icon: h(EditOutlined, null),
                          'aria-label': `Edit ${section.label}`,
                          onClick: () => startEdit(section.key, section.kind, plan.sections[section.key]),
                        })
                      ),
                    h(
                      Tooltip,
                      { title: 'Comments' },
                      h(
                        Button,
                        {
                          type: comments.length ? 'default' : 'text',
                          size: 'small',
                          icon: h(MessageOutlined, null),
                          onClick: () => setOpenSection(open ? null : section.key),
                        },
                        comments.length || null
                      )
                    )
                  )
                ),
                renderBody(section),
                open && h(CommentThread, { plan, section: section.key, onPost: (body) => post(section.key, body) })
              );
            })
      ),

      h(ReviewModal, {
        plan,
        open: reviewOpen,
        onClose: () => setReviewOpen(false),
        onSent: load,
      })
    );
  };

  // The route survives for deep links and for anyone who wants the plan to
  // fill the window.
  SW.PlanPage = function PlanPage({ planId, autoReview }) {
    return h(SW.PlanDoc, {
      planId,
      variant: 'page',
      autoReview,
      onClose: () => SW.router.go('#/chat'),
    });
  };

  // Chat's home for the plan: the friendly editor with comments, beside the
  // conversation rather than on top of it.
  SW.PlanSheet = function PlanSheet() {
    const { planViewerId } = SW.store.get();
    if (!planViewerId) return null;
    return h(
      'aside',
      { className: 'sw-plan-sheet' },
      h(SW.PlanDoc, {
        planId: planViewerId,
        variant: 'side',
        onClose: () => SW.store.closePlanViewer(),
      })
    );
  };
})();
