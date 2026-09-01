window.SW = window.SW || {};

(function () {
  const { createElement: h, useState } = React;
  const { Button, Table, Tooltip, Tag, Space, Input } = antd;
  const {
    CopyOutlined, RightOutlined, DownOutlined, PushpinOutlined, ReloadOutlined,
    ExportOutlined, DownloadOutlined, ThunderboltOutlined,
  } = icons;

  function CodeBlock({ code, language }) {
    const [expanded, setExpanded] = useState(false);
    const lines = String(code).split('\n');
    const long = lines.length > 20;
    const shown = long && !expanded ? lines.slice(0, 20).join('\n') : code;

    return h(
      'div',
      { className: 'sw-code-wrap' },
      h('pre', { className: 'sw-code' }, SW.util.highlight(shown, language)),
      h(
        Tooltip,
        { title: 'Copy code' },
        h(Button, {
          size: 'small',
          className: 'sw-code-copy',
          icon: h(CopyOutlined, null),
          'aria-label': 'Copy code',
          onClick: () => SW.util.copy(code, 'Code copied'),
        })
      ),
      long &&
        h(
          Button,
          { type: 'link', size: 'small', onClick: () => setExpanded(!expanded) },
          expanded ? 'Show less' : `Show all ${lines.length} lines`
        )
    );
  }

  // Sub-second calls are the common case for a write, and `(0.042).toFixed(1)` is "0.0s" — the
  // same misleading zero this is here to stop showing. Milliseconds below a second, seconds above.
  function runDuration(ms) {
    if (typeof ms !== 'number' || !isFinite(ms) || ms < 0) return '';
    return ms < 950 ? ` · ${Math.round(ms)}ms` : ` · ${(ms / 1000).toFixed(1)}s`;
  }

  function SandboxRun({ block }) {
    const [open, setOpen] = useState(false);
    // Some calls name nothing we can show — a tool that takes no arguments, or a card replayed
    // from a transcript recorded before Sage read that tool's input. Those rows say what ran and
    // stop there: the chevron used to open onto an empty grey box, which reads as detail that
    // failed to load rather than a step with nothing to look at.
    const hasDetail = !!String(block.code || '').trim() || !!block.stdout;
    return h(
      'div',
      { className: 'sw-sandbox-run' },
      h(
        hasDetail ? 'button' : 'div',
        {
          className: `sw-sandbox-toggle${hasDetail ? '' : ' sw-sandbox-toggle-static'}`,
          onClick: hasDetail ? () => setOpen(!open) : undefined,
        },
        // The spacer keeps every label on the same left edge, so a row without a chevron reads as
        // one of the stack and not as a card that lost its icon.
        hasDetail
          ? h(open ? DownOutlined : RightOutlined, { style: { fontSize: 9 } })
          : h('span', { className: 'sw-sandbox-nochevron', 'aria-hidden': true }),
        h('span', null, `${block.label || 'Ran Python'}${runDuration(block.durationMs)}`),
        block.packages && h('span', { style: { color: '#8F8FA3' } }, `· ${block.packages}`)
      ),
      hasDetail && open &&
        h(
          'div',
          { className: 'sw-sandbox-detail' },
          h(CodeBlock, { code: block.code, language: 'python' }),
          block.stdout &&
            h(
              'div',
              null,
              h('div', { className: 'sw-stdout-label' }, 'stdout'),
              h('pre', { className: 'sw-code' }, block.stdout)
            )
        )
    );
  }

  function ChartBlock({ chartId, onAddToPlan }) {
    const { charts } = SW.store.get();
    const spec = charts[chartId];
    if (!spec) return null;
    return h(
      'div',
      { className: 'sw-block-card' },
      h(
        'div',
        { className: 'sw-block-head' },
        h(
          'div',
          { style: { flex: 1, minWidth: 0 } },
          h('div', { className: 'sw-block-title' }, spec.title),
          spec.subtitle && h('div', { className: 'sw-block-sub' }, spec.subtitle)
        ),
        h(
          Space,
          { size: 2 },
          h(
            Tooltip,
            { title: 'Open full size' },
            h(Button, {
              type: 'text',
              size: 'small',
              icon: h(ExportOutlined, null),
              'aria-label': 'Open chart',
              onClick: () => antd.message.info('Full-size charts are not wired up in this prototype.'),
            })
          ),
          h(
            Tooltip,
            { title: 'Export as PNG' },
            h(Button, {
              type: 'text',
              size: 'small',
              icon: h(DownloadOutlined, null),
              'aria-label': 'Export chart',
              onClick: () => antd.message.success('Chart exported to examples/'),
            })
          ),
          onAddToPlan &&
            h(
              Tooltip,
              { title: 'Add to plan' },
              h(Button, {
                type: 'text',
                size: 'small',
                icon: h(PushpinOutlined, null),
                'aria-label': 'Add chart to plan',
                onClick: onAddToPlan,
              })
            )
        )
      ),
      h('div', { className: 'sw-block-body' }, h(SW.Chart, { options: spec.options, height: 260 }))
    );
  }

  function TableBlock({ block }) {
    const [showAll, setShowAll] = useState(false);
    const columns = block.columns.map((name, index) => ({
      title: name,
      dataIndex: index,
      key: name,
      ellipsis: true,
      align: index === 0 ? 'left' : 'right',
      render: (value) => {
        if (value === 'Breach') return h(Tag, { color: 'error', bordered: false }, 'Breach');
        if (value === 'Watch') return h(Tag, { color: 'warning', bordered: false }, 'Watch');
        if (value === 'OK') return h(Tag, { bordered: false }, 'OK');
        if (value === 'Awaiting review') return h(Tag, { color: 'blue', bordered: false }, value);
        if (value === 'Auto-cleared') return h(Tag, { color: 'success', bordered: false }, value);
        return value;
      },
    }));
    const all = block.rows.map((row, index) => ({ key: index, ...row }));
    const rows = showAll ? all : all.slice(0, 10);

    return h(
      'div',
      { className: 'sw-block-card' },
      block.title &&
        h('div', { className: 'sw-block-head' }, h('div', { className: 'sw-block-title' }, block.title)),
      h(Table, { size: 'small', pagination: false, dataSource: rows, columns, scroll: { x: true } }),
      all.length > 10 &&
        h(
          'div',
          { style: { padding: '8px 16px' } },
          h(
            Button,
            { type: 'link', size: 'small', style: { padding: 0 }, onClick: () => setShowAll(!showAll) },
            showAll ? 'Show fewer' : `Show all ${all.length} rows`
          )
        )
    );
  }

  // Two ways this card arrives, and they are not the same moment. The classifier notices an app
  // taking shape in a conversation about something else, so it opens tentatively. An explicit
  // "build me a webapp" was already a decision — answering that with "this is starting to look
  // like an app" reads as though nobody was listening.
  function PlanSuggestion({ block }) {
    const asked = (block || {}).reason === 'explicit';
    return h(
      'div',
      { className: 'sw-suggestion' },
      h(
        'div',
        { className: 'sw-suggestion-title' },
        h(ThunderboltOutlined, { style: { color: '#543FDE' } }),
        asked ? 'Let’s build that in Build.' : 'This is starting to look like an app.'
      ),
      h(
        'div',
        { className: 'sw-suggestion-detail' },
        asked
          ? 'Chat answers questions; Build writes the app. I can turn this conversation into a '
            + 'plan to start from.'
          : 'I can write a plan so you can review it, share it, and build from it.'
      ),
      h(
        Space,
        { size: 8 },
        h(
          Button,
          { type: 'primary', size: 'small', onClick: () => SW.store.draftHandoffPlan() },
          'Write a plan'
        ),
        h(
          Button,
          { size: 'small', onClick: () => SW.store.dismissPlanSuggestion() },
          'Not now'
        )
      )
    );
  }

  // Who replaced this plan, in the words the person picked. A plan records the Conversation that
  // produced it (#54), so the newer one can be named rather than pointed at — and a Conversation
  // the rail has not loaded, or one since deleted, still leaves a sentence that reads.
  function supersededBy(superseded) {
    const { threads } = SW.store.get();
    const by = (threads || []).find((t) => t.id === superseded.conversation);
    return by && by.title
      // The Conversation's title is the person's own word, so it fills a slot rather than resolving.
      ? SW.brand.text('“{title}” planned this {builtApp} again.', { title: by.title })
      : SW.brand.text('Another conversation planned this {builtApp} again.');
  }

  // "the plan, 2 charts and 1 thing this conversation had in context" — what a handoff carried,
  // in the order the sheet used to ask about it. The plan is unconditional because a handoff
  // without one is not this flow; everything after it is the viewer's saved answer (#58).
  function carried(crossed) {
    const charts = (crossed.charts || []).length;
    const context = (crossed.context || []).length;
    const parts = ['the plan'];
    if (charts) parts.push(`${charts} ${charts === 1 ? 'chart' : 'charts'}`);
    if (context) parts.push(`${context} ${context === 1 ? 'thing' : 'things'} this conversation `
                            + 'had in context');
    if (crossed.transcript) parts.push('the full transcript');
    if (parts.length === 1) return 'the plan, and nothing else';
    return `${parts.slice(0, -1).join(', ')} and ${parts[parts.length - 1]}`;
  }

  // The receipt for a handoff (#60). #58 took the four questions off the sheet, so nobody watches
  // the crossing happen any more — and everything that crosses is written to the Project as a real
  // file precisely so it CAN be inspected. This is where a person is told which files those were.
  //
  // It extends the card the handoff already ends on rather than adding a block beside it: one
  // handoff, one card. What the app is called and whether it was new come off the row the confirm
  // wrote; a later Change replaces what crossed and leaves those alone (ADR-0008).
  //
  // Markup, not a component: the expanded/collapsed answer belongs to the card, which is the thing
  // that survives a re-render, the same way the superseded lines below are the card's own.
  function crossingReceipt({ crossed, open, onToggle }) {
    const named = crossed.appName || crossed.appId || SW.brand.text('the {builtApp}');
    return h(
      'div',
      { className: 'sw-crossing' },
      h(
        'div',
        { className: 'sw-crossing-line' },
        'The plan crossed into ',
        h('strong', null, `“${named}”`),
        // No article engine: "a new {builtApp}" is safe because the article sits before `new`, but
        // the other branch had the article against the noun, so it takes the plural instead.
        crossed.newApp
          ? SW.brand.text(' — a new {builtApp}.')
          : SW.brand.text(' — one of the {builtAppPlural} you already had.'),
        ' It carried ',
        carried(crossed),
        '.'
      ),
      h(
        Button,
        { type: 'link', size: 'small', className: 'sw-crossing-toggle', onClick: onToggle },
        open ? 'Hide what crossed' : 'What crossed'
      ),
      open &&
        h(
          'div',
          { className: 'sw-crossing-detail' },
          (crossed.charts || []).length > 0 &&
            h(
              'div',
              { className: 'sw-crossing-group' },
              h('div', { className: 'sw-field-label' }, 'Charts'),
              (crossed.charts || []).map((chart) =>
                h(
                  'div',
                  { key: chart.path || chart.title, className: 'sw-crossing-item' },
                  chart.title || chart.path,
                  chart.path && h('code', null, chart.path)
                )
              )
            ),
          (crossed.context || []).length > 0 &&
            h(
              'div',
              { className: 'sw-crossing-group' },
              h('div', { className: 'sw-field-label' }, 'In context'),
              (crossed.context || []).map((name) =>
                h('div', { key: name, className: 'sw-crossing-item' }, name)
              )
            ),
          (crossed.files || []).length > 0 &&
            h(
              'div',
              { className: 'sw-crossing-group' },
              // Not "written into the app": `examples/` is the Project's, and the point of naming
              // paths at all is that a person can go and open exactly what is named.
              h('div', { className: 'sw-field-label' }, 'Files written to the project'),
              (crossed.files || []).map((file) =>
                h('div', { key: file, className: 'sw-crossing-item' }, h('code', null, file))
              )
            )
        )
    );
  }

  // The plan's own words, never a summary this file invents: a second description of the plan is
  // one more thing for the real one to disagree with.
  //
  // The FIRST paragraph that is not a heading — which is the opening sentence for a plan written
  // the usual way, and the first section's opening line for one that starts straight into its
  // headings. A plan with nothing but headings falls back to the heading text, because a row that
  // says nothing is worse than a row that says less than it wanted to.
  const PITCH_MAX = 120;
  function planPitch(plan) {
    const paras = String(plan || '').split(/\n\n+/).map((para) => para.trim()).filter(Boolean);
    const first = paras.find((para) => !/^#{1,6}\s/.test(para))
      || (paras[0] || '').replace(/^#{1,6}\s+/, '');
    const line = first.split('\n')[0].replace(/\s+/g, ' ').trim();
    return line.length > PITCH_MAX ? `${line.slice(0, PITCH_MAX - 1).trimEnd()}…` : line;
  }

  function BuildPlanCard({ block }) {
    const { buildRunning } = SW.store.get();
    const [answers, setAnswers] = useState('');
    const [editing, setEditing] = useState(false);
    const [showCrossing, setShowCrossing] = useState(false);
    const [changing, setChanging] = useState(false);
    // An edit belongs to the plan it was typed against. This card is keyed by its message id and
    // that id counts messages (`bp_<n>`), so a rebuilt history can hand one instance a different
    // plan — and a draft seeded once by `useState` would leave the card drawing the plan it first
    // saw. Worse, the approve below reads `draft !== plan` as "the person edited this", so the
    // stale text would go up as an override and quietly replace the plan that actually arrived.
    // Keeping the base alongside the text means a plan that moves on drops the edit rather than
    // overriding it, and there is no state left to fall out of step.
    const [edit, setEdit] = useState(null);
    const plan = block.plan || '';
    const draft = edit && edit.base === plan ? edit.text : plan;
    const setDraft = (text) => setEdit({ base: plan, text });
    const asks = /^#{1,6}\s*open questions\b/mi.test(plan);
    const pending = block.pending && !buildRunning;
    // A newer plan from another Conversation took this app's live plan.md (#59). The card said
    // nothing before, so it went on offering "Approve & build" for a plan the app no longer held.
    // Nothing was deleted, so what it owes the person is the fact and a way back in.
    const superseded = block.superseded;
    // Only a plan that arrived through a handoff has a crossing to report. A plan the Build gate
    // wrote crossed nothing, so it keeps the card it always had.
    const crossed = block.crossed;
    // Handed down, never read from the preference here. The conversation-view preference has
    // exactly one reader and it is the store (#56): it decides what a Conversation's messages ARE,
    // once, and every component downstream draws what it was given. A second reader is a second
    // place for the two views to disagree, and #61 has to be able to delete an arm by deleting one
    // branch. `test_only_the_store_branches_on_the_preference` reads this file to hold that, so the
    // preference is named in prose here rather than spelled the way the code would spell it.
    //
    // And the fold is a PROMISE that the plan is one click away, so a card with no document to open
    // does not get to make it. An architecture has none — it is written down nowhere but this card
    // (`store.js`: "Empty for an architecture, which has no document") — so folding one would file
    // its only copy behind a button that does not exist. Same for a plan too old to have an id.
    const folded = !!block.folded && !!block.planId;
    // `plan`, not `draft`: editing is unreachable folded, so there is no edit for this to be
    // holding, and reaching for `draft` here would only suggest there might be.
    const pitch = folded ? planPitch(plan) : '';

    return h(
      'div',
      { className: 'sw-plan-card' },
      h(
        'div',
        { className: 'sw-plan-card-head' },
        h(
          'div',
          { className: folded ? 'sw-plan-card-label' : 'sw-plan-card-title' },
          superseded
            ? 'Superseded by a newer plan'
            : block.kind === 'architecture'
              ? 'Architecture'
              // Folded there is no plan on the screen to review, so the instruction would be
              // telling the reader to do something the card no longer lets them do. A label is
              // what the row wants, and it is the grammar the Build run row beside it already uses.
              : (folded ? 'Plan' : 'Review the plan before building')
        ),
        // Unified puts Chat and Build in one transcript, so a plan at full height buries the turns
        // either side of it. The pitch goes IN the head rather than under it, because a row is what
        // the fold is for — label, what it is, way in — and a second line is the height coming back.
        // A plan with no words at all has nothing to pitch, and an empty element is a gap in the
        // row rather than a sentence in it. The label and the way in carry the row alone.
        pitch && h('div', { className: 'sw-plan-card-pitch' }, pitch)
      ),
      // Editing is only reachable unfolded — the button for it is dropped below — so `editing` is
      // never true here. The body is still gated on the fold rather than on that, because the fold
      // is the reason it is gone.
      !folded &&
        (editing
          ? h(Input.TextArea, {
              value: draft,
              autoSize: { minRows: 8, maxRows: 20 },
              onChange: (e) => setDraft(e.target.value),
            })
          // `draft`, not `block.plan`. The button that leaves edit mode is labelled "Preview", so
          // rendering the original showed a person their own edits vanishing. `approveBuild` was
          // sending `draft` all along — only the screen disagreed.
          : h('div', { className: 'sw-plan-card-problem sw-plan-md' }, SW.util.markdown(draft))),
      crossed &&
        crossingReceipt({
          crossed,
          open: showCrossing,
          onToggle: () => setShowCrossing(!showCrossing),
        }),
      // What Undo did NOT do. Removing the Built App is a deliberate action of its own, never a
      // side effect of deciding not to build — so an app this handoff minted is still there, and
      // saying nothing would leave the person hunting for it.
      crossed &&
        block.cancelled &&
        h(
          'div',
          { className: 'sw-caption', style: { marginTop: 8 } },
          `“${crossed.appName || crossed.appId}” stays, along with everything that crossed into `
          + 'it. The plan is archived, not deleted.'
        ),
      crossed &&
        h(SW.CrossingSheet, {
          crossed,
          planId: block.planId,
          open: changing,
          onClose: () => setChanging(false),
        }),
      superseded &&
        h(
          'div',
          { className: 'sw-caption', style: { marginTop: 8 } },
          `${supersededBy(superseded)} This plan is kept, with its comments and every version.`
        ),
      superseded &&
        h(
          'div',
          { className: 'sw-plan-card-actions', style: { marginTop: 10 } },
          // No primary here on purpose: the plan that is live now has the card below, and that is
          // where the one action worth pressing lives. These two are ways back in, not decisions.
          block.planId &&
            h(
              Button,
              { size: 'small', onClick: () => SW.store.openPlanArtifact(block.planId) },
              'Reopen this plan'
            ),
          superseded.by &&
            h(
              Button,
              { size: 'small', onClick: () => SW.store.openPlanArtifact(superseded.by) },
              'Open the newer plan'
            )
        ),
      // A settled plan draws no actions at full height because the plan is already on the screen.
      // Folded it is not, so without this the row would be a pitch and nowhere to go. Superseded
      // has its own two ways back in above and does not want a third.
      folded &&
        !pending &&
        !superseded &&
        h(
          'div',
          { className: 'sw-plan-card-actions', style: { marginTop: 10 } },
          h(
            Button,
            { size: 'small', onClick: () => SW.store.openPlanArtifact(block.planId) },
            'Open plan'
          )
        ),
      pending &&
        !folded &&
        h(Input.TextArea, {
          value: answers,
          rows: 2,
          placeholder: asks
            ? 'Answer the open questions or add notes (optional)'
            : 'Add a note for the build (optional)',
          onChange: (e) => setAnswers(e.target.value),
          style: { marginTop: 8 },
        }),
      pending &&
        h(
          'div',
          { className: 'sw-plan-card-actions', style: { marginTop: 10 } },
          h(
            Button,
            {
              type: 'primary',
              size: 'small',
              onClick: () =>
                SW.store.approveBuild(answers, draft !== plan ? draft : undefined,
                                      block.planId),
            },
            block.kind === 'architecture'
              ? 'Build this'
              : (block.steps ? `Approve & build (${block.steps} phases)` : 'Approve & build')
          ),
          // Editing needs the body it edits. Folded there is none, so the plan document — which
          // has the sections, the open questions and the comments too — is the one place to change
          // a plan rather than the second.
          !folded &&
            h(
              Button,
              { size: 'small', onClick: () => setEditing(!editing) },
              editing ? 'Preview' : (block.kind === 'architecture' ? 'Edit design' : 'Edit plan')
            ),
          // The card is the summary; the document is where the sections, the open questions and
          // the comments are. An architecture has no document, so it gets no way in.
          block.planId &&
            h(
              Button,
              { size: 'small', onClick: () => SW.store.openPlanArtifact(block.planId) },
              'Open plan'
            ),
          // Change belongs to the crossing, not to the plan, so it sits after the plan's own
          // controls (#60). It redoes what crosses and nothing else: the app was chosen once, on
          // the sheet, and stays chosen (ADR-0008).
          crossed &&
            h(
              Button,
              { size: 'small', onClick: () => setChanging(true) },
              'Change what crosses'
            ),
          h(
            Button,
            { type: 'text', size: 'small', onClick: () => SW.store.cancelBuildPlan(block.planId) },
            // Same button, same path: Undo IS the cancel. A handoff gets the word that says what
            // it undoes — "I am not building this" — and a Cancel beside it would be this button
            // twice.
            crossed ? 'Undo' : (block.kind === 'architecture' ? 'Dismiss' : 'Cancel')
          )
        )
    );
  }

  function GraduationNudge({ onSave }) {
    const { thread, resourceGroups } = SW.store.get();
    const files = (resourceGroups.file || []).filter((f) => f.sandbox);
    const artifacts = (thread && thread.artifacts) || [];
    const parts = [];
    if (files.length) parts.push(files.map((f) => f.name).join(', '));
    if (artifacts.length) parts.push(`${artifacts.length} ${artifacts.length === 1 ? 'chart' : 'charts'}`);

    return h(
      'div',
      { className: 'sw-nudge' },
      h('span', { className: 'sw-scope-dot is-hollow', style: { marginTop: 5 } }),
      h(
        'div',
        { className: 'sw-nudge-main' },
        h('div', null, "You're in ", h('strong', null, 'Personal sandbox'), '. ',
          parts.length
            ? `${parts.join(' and ')} will be cleared when you leave.`
            : 'Anything you create here is cleared when you leave.'),
        h(
          'div',
          { style: { marginTop: 8 } },
          h(Space, { size: 8 },
            h(Button, { type: 'primary', size: 'small', onClick: onSave }, 'Save to a project'),
            h(Button, { type: 'text', size: 'small', onClick: () => SW.store.dismissNudge() }, 'Dismiss'))
        )
      )
    );
  }

  // A build that stopped saying anything and was given up on (#39). The same card shape as the two
  // offers below it, because it is the same kind of moment: something needs a decision, and the
  // person is the only one who can make it. The message carries what happened and what was kept —
  // the files are still there — so all this adds is the way to ask again. A turn with nothing of
  // the person's to replay (an approve, a phase) arrives with no prompt and is the message alone.
  function BuildStalled({ block }) {
    const [busy, setBusy] = useState(false);
    const retry = () => {
      setBusy(true);
      Promise.resolve(SW.store.retryStalledBuild(block.prompt))
        .catch((err) => antd.message.error(String(err.message || err)))
        .finally(() => setBusy(false));
    };
    return h(
      'div',
      { className: 'sw-nudge' },
      h('span', { className: 'sw-scope-dot is-hollow', style: { marginTop: 5 } }),
      h(
        'div',
        { className: 'sw-nudge-main' },
        h('div', null, block.message),
        block.live && block.prompt
          ? h(
              'div',
              { style: { marginTop: 8 } },
              h(Button, {
                type: 'primary',
                size: 'small',
                loading: busy,
                disabled: busy,
                onClick: retry,
              }, 'Try again')
            )
          : null
      )
    );
  }

  // The turn asked to start over (#36). The gate stops before any inference and hands the decision
  // back, so this card is the decision: it says what a reset does and does not take, and gives the
  // one-click way to do it. "Reset and build this" exists because "clear everything and build X from
  // @data" is ONE request — a reset alone answers half of it and leaves the user retyping the rest.
  //
  // Without `live` the buttons are gone and only the sentence is left: a replayed offer is a record
  // of a past decision, and an old message must not be able to reset the app on a page load nobody
  // connected it to.
  function ResetOffer({ block }) {
    const [busy, setBusy] = useState('');
    const run = (key, fn) => () => {
      setBusy(key);
      Promise.resolve(fn())
        .catch((err) => antd.message.error(String(err.message || err)))
        .finally(() => setBusy(''));
    };

    return h(
      'div',
      { className: 'sw-nudge' },
      h('span', { className: 'sw-scope-dot is-hollow', style: { marginTop: 5 } }),
      h(
        'div',
        { className: 'sw-nudge-main' },
        h('div', null, block.message),
        block.live && block.prompt
          ? h(
              'div',
              { style: { marginTop: 8 } },
              h(Space, { size: 8, wrap: true },
                h(Button, {
                  type: 'primary',
                  size: 'small',
                  loading: busy === 'both',
                  disabled: !!busy,
                  onClick: run('both', () => SW.store.resetAndBuild(block.prompt)),
                }, 'Reset and build this'),
                h(Button, {
                  size: 'small',
                  loading: busy === 'reset',
                  disabled: !!busy,
                  onClick: run('reset', () => SW.store.resetApp()),
                }, 'Just reset'),
                h(Button, {
                  type: 'text',
                  size: 'small',
                  loading: busy === 'build',
                  disabled: !!busy,
                  onClick: run('build', () => SW.store.buildWithoutReset(block.prompt)),
                }, 'Build without resetting'))
            )
          : null
      )
    );
  }

  // Somebody else pushed changes to this app before the turn started (#78). The gate stops before
  // any inference and hands the decision back, so this card IS the decision — and the file list is
  // what makes it one: "somebody changed this app" is only actionable once you can see what they
  // changed and recognise whether it touches what you were about to do.
  //
  // Two answers, both real. Pulling builds on their work; keeping building merges later, which is
  // what the save path does anyway. Same `live` rule as the reset offer: a replayed offer is a
  // record of a decision already made, and an old message must not pull the repo on a page load.
  function IncomingChanges({ block }) {
    const [busy, setBusy] = useState('');
    const run = (key, fn) => () => {
      setBusy(key);
      Promise.resolve(fn())
        .catch((err) => antd.message.error(String(err.message || err)))
        .finally(() => setBusy(''));
    };
    const hidden = (block.count || 0) - (block.files || []).length;

    return h(
      'div',
      { className: 'sw-nudge' },
      h('span', { className: 'sw-scope-dot is-hollow', style: { marginTop: 5 } }),
      h(
        'div',
        { className: 'sw-nudge-main' },
        h('div', null, block.message),
        (block.files || []).length
          ? h(
              'div',
              { className: 'sw-incoming-files' },
              (block.files || []).map((f) => h('div', { className: 'sw-incoming-file', key: f }, f)),
              hidden > 0
                ? h('div', { className: 'sw-caption' },
                    `and ${hidden} more file${hidden === 1 ? '' : 's'}`)
                : null
            )
          : null,
        block.live && block.prompt
          ? h(
              'div',
              { style: { marginTop: 8 } },
              h(Space, { size: 8, wrap: true },
                h(Button, {
                  type: 'primary',
                  size: 'small',
                  loading: busy === 'pull',
                  disabled: !!busy,
                  onClick: run('pull', () => SW.store.pullAndBuild(block.prompt)),
                }, 'Pull and build this'),
                h(Button, {
                  type: 'text',
                  size: 'small',
                  loading: busy === 'keep',
                  disabled: !!busy,
                  onClick: run('keep', () => SW.store.buildWithIncoming(block.prompt)),
                }, 'Keep building'))
            )
          : null
      )
    );
  }

  // A change that happened, attached to the app it happened to. This is where
  // Review and Publish belong: a turn can change two apps, and the preview can
  // only show one of them, so the entry — not the panel — is what makes every
  // change reviewable. Nothing a turn did is left for the user to go and find.
  //
  // Emitted by the build turn, server-side and blind to the conversation view (#83), so this
  // renders under BOTH: Build shows it at the end of the turn that changed the app, and Chat's
  // merged read folds a run's cards into its collapsed row. The folding is the unified arm's work
  // and #61 may take it away; this card is not part of it.
  //
  // Two facts, two ages. The NAME comes out of the block, because what an app was called is a
  // then-fact and a run from six weeks ago should name it the way it was named then. Whether it is
  // published is a now-question, so that is read off the rail's list — which the store loads once
  // for the Project, so a long merged transcript costs one read and not one per row.
  function AppChange({ block }) {
    const { activeApp, apps } = SW.store.get();
    const live = (apps || []).find((a) => a.id === block.appId) || null;
    const name = block.name || (live && live.name) || 'this app';
    // Chat has no preview panel, so the app can only be "in the preview" while
    // Build is the mode on screen — otherwise the card offers to take you there.
    const showing =
      SW.router.get().mode === 'build' && activeApp && activeApp.id === block.appId;
    const status = !live ? 'draft' : live.building ? 'building' : live.published ? 'running' : 'draft';

    return h(
      'div',
      { className: `sw-appchange${showing ? ' is-showing' : ''}` },
      h(
        'div',
        { className: 'sw-appchange-head' },
        h(SW.StatusDot, { status }),
        h('span', { className: 'sw-appchange-name' }, name),
        showing && h('span', { className: 'sw-appchange-here' }, 'in the preview')
      ),
      block.summary && h('div', { className: 'sw-appchange-summary' }, block.summary),
      h(
        'div',
        { className: 'sw-appchange-foot' },
        // An app that was never published keeps this control rather than losing it to a URL: it
        // has nowhere else to be looked at, so the preview is the only door (ADR-0008). Dropped
        // only when Build is already showing the app, because a button that navigates to where
        // you already are is the dead end this card replaces.
        !showing &&
          h(
            Button,
            {
              // The rail's own grammar, not a second one that looks like it: `?app=` is the single
              // lever that moves preview, code and composer target together, and one writer of the
              // string is what keeps this card, the rail row and the route agreeing (#83).
              size: 'small',
              onClick: () => SW.router.go(SW.appRoute({ id: block.appId })),
            },
            'Open in preview'
          ),
        // Silent rather than wrong while the rail's answer is not in yet, and for an app that has
        // since left the Project: "Not published yet" is a claim, and this is the one place that
        // has no business guessing it.
        live &&
          h(
            'span',
            { className: 'sw-caption' },
            // An app published before the stamp existed has no date, and so does every app in
            // every Project on the release that added one. "Published · " with nothing after it
            // reads as a date that failed to load; "Published" is the whole of what is known.
            !live.published
              ? 'Not published yet'
              : live.publishedAt
                ? `Published · ${SW.util.relativeTime(live.publishedAt)}`
                : 'Published'
          )
      )
    );
  }

  // Chat's fold of one build run (#56, unified conversation view only — #61 takes this and its
  // `build_run` case away if split wins; `AppChange` above stays either way).
  //
  // Chat has no preview pane, so twenty raw implementation turns would bury the questions around
  // them. The run collapses to one row and opens when it is asked to. Its face is the run's
  // `app_change` cards — one per distinct app the run changed — because that IS the app card, and
  // building the row on a second source of app facts is what would strand the card in this branch.
  function BuildRun({ block }) {
    const [open, setOpen] = useState(false);
    const apps = block.apps || [];
    const turns = block.messages || [];

    return h(
      'div',
      { className: 'sw-buildrun' },
      h(
        'div',
        { className: 'sw-buildrun-head' },
        h('span', { className: 'sw-buildrun-label' }, 'Build run'),
        h('span', { className: 'sw-buildrun-prompt' }, block.prompt),
        // A run whose turns all folded away has nothing to open, and a control that opens nothing
        // is the dead end the rest of this card exists to avoid.
        turns.length > 0 &&
          h(
            Button,
            { type: 'link', size: 'small', onClick: () => setOpen(!open) },
            open ? 'Hide the turns' : `Show the ${turns.length} turn${turns.length === 1 ? '' : 's'}`
          )
      ),
      apps.map((app) => h(AppChange, { key: app.appId, block: app })),
      open &&
        h(
          'div',
          { className: 'sw-buildrun-turns' },
          turns.map((message) => h(SW.Message, { key: message.id, message }))
        )
    );
  }

  function FileCard({ block }) {
    const href = `./api/project/file/raw?path=${encodeURIComponent(block.path || '')}`;
    return h(
      'a',
      { className: 'sw-block-card', href, style: { display: 'block', padding: '10px 14px' } },
      block.name || block.path
    );
  }

  function ImageBlock({ block }) {
    return h(
      'div',
      { className: 'sw-block-card' },
      block.title &&
        h('div', { className: 'sw-block-head' }, h('div', { className: 'sw-block-title' }, block.title)),
      h(
        'div',
        { className: 'sw-block-body' },
        h('img', { src: block.src, alt: block.title || '', style: { maxWidth: '100%', display: 'block' } })
      )
    );
  }

  SW.MessageBlock = function MessageBlock({ block, onSave }) {
    switch (block.type) {
      case 'text':
        // A caret while the text is still arriving. Without it a model that pauses mid-sentence
        // looks like a model that finished a short answer, and the reader gives up on it.
        return h('div', { className: `sw-msg-text${block.streaming ? ' is-streaming' : ''}` },
                 SW.util.markdown(block.value));
      case 'code':
        return h(CodeBlock, { code: block.value, language: block.language });
      case 'sandbox_run':
        return h(SandboxRun, { block });
      case 'chart':
        return h(ChartBlock, {
          chartId: block.chartId,
          onAddToPlan: SW.store.get().activePlanId
            ? () => antd.message.success('Added to the plan as supporting evidence')
            : null,
        });
      case 'image':
        return h(ImageBlock, { block });
      case 'file':
        return h(FileCard, { block });
      case 'table':
        return h(TableBlock, { block });
      case 'choice':
        return h(SW.ChoiceCard, {
          prompt: block.prompt,
          options: block.options,
          onChoose: (option) => SW.store.chooseOption(option),
        });
      case 'resource_result':
        return h(SW.ResourceResultCard, {
          resourceId: block.resourceId,
          reason: block.reason,
          alternatives: block.alternatives,
        });
      case 'app_change':
        return h(AppChange, { block });
      case 'build_run':
        return h(BuildRun, { block });
      case 'plan_card':
        return h(SW.PlanCard, { planId: block.planId });
      case 'build_plan':
        return h(BuildPlanCard, { block });
      case 'status':
        return h(
          'div',
          { className: `sw-status-line${block.ok === false ? ' is-err' : ''}` },
          block.value
        );
      case 'reset_offer':
        return h(ResetOffer, { block });
      case 'incoming_changes':
        return h(IncomingChanges, { block });
      case 'build_stalled':
        return h(BuildStalled, { block });
      case 'plan_suggestion':
        return h(PlanSuggestion, { block });
      case 'graduation_nudge':
        return h(GraduationNudge, { onSave });
      default:
        return null;
    }
  };

  // Pinning has to land somewhere real. A plan takes it; an app that was never
  // written down keeps it as a decision instead; a conversation about nothing in
  // particular has nowhere to put it, so it does not offer.
  function pinTargetFor(message) {
    const { activePlanId, activeApp, thread, touched } = SW.store.get();
    if (activePlanId) {
      return { title: 'Add to plan', onClick: () => antd.message.success('Added to the plan') };
    }
    // Chat has no preview to borrow a target from, so the conversation's own tag
    // stands in — but only while it points at one app. Two tags and the question
    // "a decision about what" has no answer this button can give.
    const tags = touched || [];
    const target =
      activeApp ||
      (tags.length === 1 ? { id: tags[0].appId, name: tags[0].appName } : null);
    if (!target) return null;
    const said = message.blocks
      .filter((b) => b.type === 'text')
      .map((b) => b.value.replace(/\*\*/g, ''))
      .join(' ')
      .trim();
    return {
      title: `Record as a decision on ${target.name}`,
      onClick: async () => {
        await SW.api.addDecision(target.id, {
          text: said.length > 160 ? `${said.slice(0, 160)}…` : said,
          conversationId: thread ? thread.id : null,
        });
        antd.message.success(`Recorded on ${target.name}. New conversations will read it.`);
      },
    };
  }

  SW.Message = function Message({ message, onSave }) {
    const { me } = SW.store.get();
    const isUser = message.role === 'user';
    const isSystem = message.role === 'system';
    const pinTarget = isUser ? null : pinTargetFor(message);

    if (isSystem) {
      return h(
        'div',
        { className: 'sw-msg' },
        h(
          'div',
          { className: 'sw-msg-body' },
          message.blocks.map((block, i) => h(SW.MessageBlock, { key: i, block, onSave }))
        )
      );
    }

    return h(
      'div',
      { className: `sw-msg ${isUser ? 'sw-msg-user' : 'sw-msg-assistant'}` },
      h(
        'div',
        { className: 'sw-msg-body' },
        h(
          'div',
          { className: 'sw-msg-who' },
          isUser
            ? h(SW.Avatar, { user: me, size: 20 })
            : h('span', { style: { fontSize: 14 } }, '✦'),
          isUser ? 'You' : SW.brand.assistant()
        ),

        (message.attachments || []).length > 0 &&
          h(
            'div',
            { className: 'sw-msg-attachments' },
            message.attachments.map((att) =>
              h(
                Tag,
                { key: att.resourceId, bordered: true, style: { display: 'inline-flex', gap: 6, alignItems: 'center' } },
                h('span', null, SW.util.iconFor(att.kind || 'file')),
                att.name
              )
            )
          ),

        h(
          'div',
          { className: 'sw-msg-blocks' },
          message.blocks.map((block, i) => h(SW.MessageBlock, { key: i, block, onSave }))
        ),

        !isUser &&
          h(
            'div',
            { className: 'sw-msg-actions' },
            h(
              Tooltip,
              { title: 'Copy' },
              h(Button, {
                type: 'text',
                size: 'small',
                icon: h(CopyOutlined, null),
                'aria-label': 'Copy message',
                onClick: () =>
                  SW.util.copy(
                    message.blocks.filter((b) => b.type === 'text').map((b) => b.value).join('\n\n')
                  ),
              })
            ),
            pinTarget &&
              h(
                Tooltip,
                { title: pinTarget.title },
                h(Button, {
                  type: 'text',
                  size: 'small',
                  icon: h(PushpinOutlined, null),
                  'aria-label': pinTarget.title,
                  onClick: pinTarget.onClick,
                })
              ),
            h(
              Tooltip,
              { title: 'Retry' },
              h(Button, {
                type: 'text',
                size: 'small',
                icon: h(ReloadOutlined, null),
                'aria-label': 'Retry',
                onClick: () => antd.message.info('Retry is not wired up in this prototype.'),
              })
            )
          )
      )
    );
  };

  SW.TypingIndicator = function TypingIndicator({ label }) {
    return h(
      'div',
      { className: 'sw-msg' },
      h(
        'div',
        { className: 'sw-msg-body' },
        h('div', { className: 'sw-msg-who' }, h('span', { style: { fontSize: 14 } }, '✦'), SW.brand.assistant()),
        h(
          'div',
          { className: 'sw-typing' },
          h('span', { className: 'sw-typing-dots' }, h('i'), h('i'), h('i')),
          h('span', null, label)
        )
      )
    );
  };
})();
