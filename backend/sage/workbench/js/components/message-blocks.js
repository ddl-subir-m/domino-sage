window.SW = window.SW || {};

(function () {
  const { createElement: h, useState, useEffect, useRef } = React;
  const { Button, Table, Tooltip, Tag, Space, Input, Spin } = antd;
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

  // Only a wait worth reading. `_tool_duration_ms` times the tool alone — the model's argument
  // streaming is deliberately not in it — so a read, a write or a glob is a local file operation
  // and always lands in the tens of milliseconds. That number told nobody anything and put a
  // precise-looking figure on the step of the card least worth attention. What is left is the wait
  // a person actually sits through: an install, a typecheck, a sub-task.
  function runDuration(ms) {
    if (typeof ms !== 'number' || !isFinite(ms) || ms < 950) return '';
    return ` · ${(ms / 1000).toFixed(1)}s`;
  }

  function SandboxRun({ block }) {
    const [open, setOpen] = useState(false);
    // What this tool was called with, when the read that drew the card left it out — the Build
    // history drawer's read does (`api.appHistory`), because those arguments are six bytes in
    // seven of the log and this fold sits two clicks below the list that was paying for them.
    //
    // ONE state, not three. `null` is "nobody has asked yet", `{ detail }` is the answer and
    // `{ failed: true }` is the read that did not arrive, so "still reading" is derived rather
    // than stored — three booleans can disagree with each other and this cannot. It is also the
    // shape `appHistory` itself uses in the store, for the same reason: a read that failed is not
    // an answer that came back empty.
    const [row, setRow] = useState(null);
    const deferred = block.detailRow !== undefined && block.detailRow !== null
      && !String(block.code || '').trim();
    const failed = !!(row && row.failed);
    const reading = deferred && open && row === null;
    const code = row && !row.failed ? row.detail : block.code;

    // ONE reader, and the condition IS the trigger: open, deferred, and holding nothing. Same
    // shape and same reason as the drawer's own read two files over.
    //
    // Not in the click handler. A card can be open without anyone having clicked it, and — the
    // sharper reason — a Try again that fetched for itself would fetch twice for one click: it has
    // to put the state back to "holding nothing" to show a spinner, and that is exactly the
    // condition this fires on. So the button below only clears, and the read happens here.
    useEffect(() => {
      if (!(deferred && open && row === null)) return;
      SW.api.historyRowDetail(block.detailRow).then(
        (detail) => setRow({ detail }),
        () => setRow({ failed: true })
      );
    }, [deferred, open, row === null, block.detailRow]);

    // Some calls name nothing we can show — a tool that takes no arguments, or a card replayed
    // from a transcript recorded before Sage read that tool's input. Those rows say what ran and
    // stop there: the chevron used to open onto an empty grey box, which reads as detail that
    // failed to load rather than a step with nothing to look at.
    //
    // A deferred row is the opposite case and has to count as detail, or the one card whose
    // contents are worth a fetch would be the one card with nothing to click.
    const hasDetail = !!String(code || '').trim() || !!block.stdout || deferred;
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
          reading && h('div', { className: 'sw-sandbox-reading' }, 'Reading what this ran…'),
          // Said as a fact about the READ, and offering the way back — the rule the drawer's own
          // failed state states (#90). The card stays open behind it, so the step it names is
          // still on screen while the retry is offered.
          failed &&
            h(
              'div',
              { className: 'sw-sandbox-reading' },
              "Couldn't read what this ran. ",
              h(Button, { type: 'link', size: 'small', onClick: () => setRow(null) },
                'Try again')
            ),
          !reading && !failed && !!String(code || '').trim() &&
            h(CodeBlock, { code, language: 'python' }),
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
              onClick: () => antd.message.info("Opening a chart full size isn't available yet."),
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
              onClick: () => antd.message.info("Downloading a chart isn't available yet."),
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

  function PageBlock({ block }) {
    const src = `./api/project/file/raw?path=${encodeURIComponent(block.path)}`;
    // `sandbox` WITHOUT `allow-same-origin`: the page runs on an opaque origin, so a script in a
    // file the model wrote cannot reach the Builder's cookie, its storage or its DOM. Scripts are
    // allowed because a dashboard that draws its charts in JS is blank without them, and a blank
    // box is the failure this replaces.
    return h(
      'div',
      { className: 'sw-block-card' },
      h(
        'div',
        { className: 'sw-block-head' },
        h('div', { className: 'sw-block-title' }, block.title || block.path),
        h('a', { className: 'sw-block-sub', href: src, target: '_blank', rel: 'noreferrer' },
          'Open in a tab')
      ),
      h('div', { className: 'sw-block-body' },
        h('iframe', { className: 'sw-block-page', src, sandbox: 'allow-scripts', loading: 'lazy',
                      title: block.title || 'Page' }))
    );
  }

  // Names when the wrapper had them, otherwise the width of a positional body. Inventing "0",
  // "1" as a header is worse than no header (`orient="values"`), but antd given zero column
  // defs paints one empty row per record — the filename-titled blank grid a warehouse table
  // reloaded as. The values are already in `rows`; they need a `dataIndex` that can see them.
  function tablePaintShape(block) {
    const names = (block.columns || []).map((c) => (
      c && typeof c === 'object' ? (c.name || c.title || c.field || '') : (c ?? '')
    ));
    const width = Math.max(
      names.length,
      ...((block.rows || []).map((r) => (Array.isArray(r) ? r.length : 0))),
    );
    return { names, width };
  }

  // What a table says when its Project does not keep data rows (ADR-0045): the columns, how many
  // rows were read, and when. A receipt rather than a grid — the rows are not late, they are not
  // coming, and "No data" over a correct title would send someone looking for a fault.
  function tableReceiptLines(block) {
    const lines = [];
    if (block.columns.length) lines.push(block.columns.join(', '));
    // `longDate`, not `relativeTime`: this stamp is the age of the DATA, and the reasons it must
    // not drift with the clock or with the viewer's zone are written where that helper is.
    const counted = `${SW.util.number(block.rowCount)} ${block.rowCount === 1 ? 'row' : 'rows'}`;
    // "the first", where the read stopped at a LIMIT with more behind it. The plain count over a
    // capped read claims the whole table, and the assistant beside this card was told otherwise —
    // its receipt says "the first 500 rows (there are more)".
    const count = block.truncated ? `the first ${counted}` : counted;
    const when = SW.util.longDate(block.readAt);
    lines.push(when ? `${count}, read ${when}` : `${count} read`);
    // Named as the switch is labelled, and where it is: a person reading this card is one setting
    // away from the rows, and "Kept rows" is what the decision is called rather than what the
    // control says. Not said over a frame that was empty — nothing was withheld from that one, and
    // an offer to turn rows on would not bring any back.
    //
    // Nor said once the setting IS on. `keptRows` on the block is read off the FILE and records
    // what that file holds, which is the right thing for it to record: an Artifact written before
    // the switch was flipped still holds no rows, and repairing it later would be writing rows
    // nobody asked anyone to write. But the instruction is in the present tense and about the
    // Project, so after the flip it tells somebody to switch on a thing they already switched on.
    const on = ((SW.store.get() || {}).keptRows || {}).on;
    if (block.rowCount > 0 && !on) {
      lines.push(SW.brand.text(
        'Rows aren\'t saved in this {project}. To keep them, turn on "Keep data rows" '
        + 'under Add people.'));
    }
    return lines;
  }

  // What a press put on screen, said in the same words the stamp above it uses and with the same
  // absolute date, so the two dates read as two facts rather than as one that changed. The reason
  // rides with it: these rows are not in the file, and the next person to open this Conversation
  // sees the shape again (ADR-0045).
  function freshRowsLine(fresh) {
    const n = SW.util.number(fresh.rowCount);
    const counted = `${fresh.truncated ? 'the first ' : ''}${n} ${fresh.rowCount === 1 ? 'row' : 'rows'}`;
    const when = SW.util.longDate(fresh.readAt);
    return SW.brand.text(
      `On screen: ${counted}${when ? `, read ${when}` : ''}. These rows aren't saved in this ` +
      '{project}.');
  }

  // A table whose rows this Project does not keep, told apart from one the recovery ladder could
  // not read. Both arrive with no rows; only this one knows how many there were, and the other
  // needs the older card underneath — the one that offers the file rather than naming a count it
  // never had.
  const isTableReceipt = (block) =>
    block.keptRows === false && !block.rows.length && block.rowCount != null;

  function TableBlock({ block }) {
    const [showAll, setShowAll] = useState(false);
    // Today's rows, and they live here and nowhere else (ADR-0045). Nothing writes them back to the
    // Artifact, so a reload is the thin card again — which is why the stamp above them goes on
    // saying when the FILE was read rather than when this ran. State, not a fetch on render: a read
    // that happened on open would put a warehouse query behind every scroll (#256).
    const [again, setAgain] = useState(null);
    const [refused, setRefused] = useState('');
    const [reading, setReading] = useState(false);

    // Blocks are rendered with the index as the key, so React reuses this instance when a message's
    // block list grows or reorders — a chart landing beside the table, an artifact refresh. Without
    // this, table A's rows stay on screen under table B's title and B's stamp, which is the one
    // thing this card must never do. Keyed on the file, because that is what the card IS.
    useEffect(() => { setAgain(null); setRefused(''); }, [block.path]);

    async function readAgain() {
      const thread = ((SW.store.get() || {}).thread || {}).id || '';
      setReading(true);
      setRefused('');
      try {
        const out = await SW.api.readAgain(thread, block.source);
        if (out && out.refused) { setAgain(null); setRefused(out.refused); }
        else { setAgain(out); }
      } catch {
        // The sentence a person is owed, never the transport's. A failed press leaves the card
        // exactly as it was, which is the shape it can always show.
        setRefused(SW.brand.text(
          '{assistantName} could not read that just now. Try again in a moment.'));
      } finally {
        setReading(false);
      }
    }

    if (isTableReceipt(block)) {
      return h(
        'div',
        { className: 'sw-block-card' },
        block.title &&
          h('div', { className: 'sw-block-head' },
            h('div', { className: 'sw-block-title' }, block.title)),
        h('div', { className: 'sw-block-body' },
          ...tableReceiptLines(block).map((line) =>
            h('div', { className: 'sw-block-sub' }, line)),
          refused && h('div', { className: 'sw-block-sub' }, refused),
          // No Binding, no button (#258). A table the Chat agent composed is not a read — its
          // source would hand back the file rather than the table — and a button that cannot
          // answer is worse on that card than no button at all.
          block.source && h(
            Button,
            { type: 'link', size: 'small', style: { padding: 0 }, loading: reading,
              onClick: readAgain },
            'Read again'
          )),
        again && h('div', { className: 'sw-block-body' },
          h('div', { className: 'sw-block-sub' }, freshRowsLine(again))),
        ...(again ? tableGrid({ ...block, columns: again.columns || [], rows: again.rows || [] },
                              showAll, setShowAll) : [])
      );
    }
    // With neither columns nor rows, antd paints a bordered box under the title and nothing
    // else — it reads as a rendering fault, and it names neither what is missing nor anything
    // to do about it. Two of these arrived under a correct "Adverse Events Summary" title. The
    // file the turn wrote is the only thing that can settle which it is: a frame that really
    // was empty, or a wrapper `blocksForArtifacts` could not read. So offer the file.
    if (!block.columns.length && !block.rows.length) {
      return h(
        'div',
        { className: 'sw-block-card' },
        block.title &&
          h('div', { className: 'sw-block-head' },
            h('div', { className: 'sw-block-title' }, block.title)),
        h(
          'div',
          { className: 'sw-block-body' },
          h('div', { className: 'sw-block-sub' }, 'This table came through with no rows.'),
          block.path &&
            h(
              'a',
              { className: 'sw-block-sub',
                href: `./api/project/file/raw?path=${encodeURIComponent(block.path)}` },
              'Open the file'
            )
        )
      );
    }
    return h(
      'div',
      { className: 'sw-block-card' },
      block.title &&
        h('div', { className: 'sw-block-head' }, h('div', { className: 'sw-block-title' }, block.title)),
      ...tableGrid(block, showAll, setShowAll)
    );
  }

  // The grid itself, as plain nodes rather than a component. Two cards draw it — the one whose
  // Project keeps its rows, and a receipt after somebody presses **Read again** (#256) — and a
  // nested component here would be an element the card returns rather than the table it drew.
  function tableGrid(block, showAll, setShowAll) {
    const { names, width } = tablePaintShape(block);
    const columns = Array.from({ length: width }, (_, index) => ({
      title: names[index] || '',
      dataIndex: index,
      key: names[index] || String(index),
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
    return [
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
        ),
    ];
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
        asked ? "Let's open this in Build." : 'This is starting to look like an app.'
      ),
      h(
        'div',
        { className: 'sw-suggestion-detail' },
        asked
          ? 'I can turn this conversation into a plan to start from.'
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
          { size: 'small', onClick: () => SW.store.dismissPlanSuggestion({ answerHere: asked }) },
          // Same handler, two labels, because the two arms do two different things. The classifier
          // card sits under a turn that already answered, so declining it really is `Not now` —
          // nothing runs, and nothing is owed. The explicit card was raised INSTEAD of a turn, so
          // declining it runs the question here; `Not now` reads as later, promises nothing, and
          // the answer that then arrives is a surprise nobody was waiting for.
          asked ? 'Answer it here' : 'Not now'
        )
      )
    );
  }

  // The only way out of a Conversation the gateway keeps refusing (ADR-0022). Two rungs: the first
  // keeps a summary of what was said, and when that is refused too — likely, since Sage's own
  // answers are in the transcript and the summary is built from them — the second keeps nothing.
  function RecallOffer({ block }) {
    const complete = (block || {}).scope === 'empty';
    const policyChange = (block || {}).reason === 'native-policy-change';
    // One card, two sessions. Build's Recall lives per (Conversation, app) and Chat's per Thread,
    // so the button has to know which transcript drew it.
    //
    // The detail line differs too, because the PROMISE differs. Chat's first clear carries a
    // written summary of the conversation into the fresh session (`recall.seed`); Build has no
    // such thing and needs none — the agent opens the app's own directory, so the files and the
    // plan come back by being read. Saying "a short summary of what was said" on this side would
    // describe a mechanism that is not there, and the two halves would be promising the same
    // words for different reasons.
    const build = (block || {}).surface === 'build';
    const clear = (scope) => (build ? SW.store.clearBuildRecall(scope) : SW.store.clearRecall(scope));
    const dismiss = () => (build
      ? SW.store.dismissBuildRecallOffer((block || {}).offerKey)
      : SW.store.dismissRecallOffer((block || {}).offerKey));
    return h(
      'div',
      { className: 'sw-suggestion' },
      h(
        'div',
        { className: 'sw-suggestion-title' },
        h(ThunderboltOutlined, { style: { color: '#543FDE' } }),
        policyChange ? 'This conversation needs a fresh model context.'
          : complete ? "It's still being refused." : 'This conversation keeps being refused.'
      ),
      h(
        'div',
        { className: 'sw-suggestion-detail' },
        // eslint-disable-next-line no-nested-ternary
        policyChange
          ? (build
            ? 'The access rules changed. Clear recall to continue. Your app, plan and transcript stay.'
            : 'The access rules changed. Clear recall to continue with a summary. Your transcript stays.')
          : complete
          ? (build
            ? 'Starting over was not enough, so what the gateway matched came back into the new '
              + 'session. Clearing Recall again leaves the model nothing it has been told here. '
              + 'Your app, its plan and this transcript all stay.'
            : 'The summary carried over must hold the value too. Clearing Recall completely leaves '
              + 'the model nothing from this conversation. Your transcript stays.')
          : (build
            ? 'The gateway has refused the same way twice, so what it matched is in this '
              + "conversation's Recall. Clearing Recall starts the model over: your app, its plan "
              + 'and this transcript stay, and the agent reads them back.'
            : 'The gateway has refused the same way twice, so what it matched is in this '
              + "conversation's Recall. Clearing Recall starts the model over: your transcript "
              + 'stays, and the model keeps a short summary of what was said.')
      ),
      h(
        Space,
        { size: 8 },
        h(
          Button,
          {
            type: 'primary',
            size: 'small',
            onClick: () => clear(complete ? 'empty' : 'summary'),
          },
          complete ? 'Clear recall completely' : 'Clear recall'
        ),
        // No dismiss. Declining is not a preference about a want, the way it is on a Build offer —
        // it is a judgment made before trying anything else, and this is the only exit. Hiding it
        // after one "not now" rebuilds the dead end. The offer cannot nag: it appears only on a
        // turn that has already failed.
        h(Button, { size: 'small', onClick: dismiss }, 'Not now')
      )
    );
  }

  // The transcript would otherwise lie about why the model suddenly forgot what was discussed
  // above this line. Recall and the transcript are different things and this is where they part.
  function RecallCleared({ block }) {
    const complete = (block || {}).scope === 'empty';
    return h(
      'div',
      { className: 'sw-recall-cleared' },
      complete
        ? 'Recall cleared completely. The model has nothing from above.'
        : 'Recall cleared. The model starts over here, with a summary of what was said above.'
    );
  }

  // The same seam as `RecallCleared` and the opposite cause: nobody asked for this one. The session
  // holding the conversation was already gone when this turn ran, so the model was handed a summary
  // of the transcript instead of the conversation itself (ADR-0060, #427). Without this line the
  // transcript claims the model remembered every word above it, which is the same lie the clear
  // divider exists to prevent.
  //
  // "Summary" is said plainly because that is the part the person can act on: the model has the
  // gist and may be missing the one detail they are about to build a follow-up on.
  function RecallRebuilt() {
    return h(
      'div',
      { className: 'sw-recall-cleared' },
      'Memory rebuilt. The model lost this conversation and was given a summary of the messages ' +
        'above, so some details may be missing. If something above matters, say it again.'
    );
  }

  // The receipt for a withhold (ADR-0022), and the only thing on screen that says the click landed.
  // A divider in the same family as `RecallCleared`, and deliberately quieter than it: clearing
  // Recall throws work away and this throws nothing away at all.
  //
  // "Nothing was changed or deleted" is the load-bearing half. Sage STOPS SENDING content; it never
  // alters it, and ADR-0022 refuses redaction in as many words. A person who read this as an edit
  // would go looking for a file that has been rewritten, and would not find one.
  function RecallWithheld({ block }) {
    const labels = (block || {}).labels || [];
    const many = labels.length > 1;
    return h(
      'div',
      { className: 'sw-recall-cleared' },
      `No longer sending ${labels.join(', ') || 'that content'}. Nothing was changed or deleted — `
      + `this conversation stops sending ${many ? 'them' : 'it'}. `
      // Only when their own question was what went. Retyping it is the obvious next move and the
      // one that fails without a word: the same text hashes to the same key, so it is stopped
      // before it is sent and nothing appears to say why. Said here because there is nowhere else
      // — no card is drawn for a turn that was never refused. On a withheld FILE this is worse
      // than silence, since it would set a person rewriting words that were fine all along.
      + (block.prompt
        ? 'Ask again in different words — Sage stops the same ones before they are sent. '
        : '')
      + 'A new conversation starts fresh.'
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
      ? SW.brand.text('"{title}" planned this {builtApp} again.', { title: by.title })
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
                            + 'was using');
    if (crossed.transcript) parts.push('the full transcript');
    if (parts.length === 1) return 'the plan, and nothing else';
    return `${parts.slice(0, -1).join(', ')} and ${parts[parts.length - 1]}`;
  }

  const BINDINGS_FILE = '.sage/bindings.json';

  function retainedBindingFiles(crossed) {
    const retained = crossed.retainedBindings || [];
    const legacy = !crossed.resources && (crossed.files || []).includes(BINDINGS_FILE)
      ? [BINDINGS_FILE]
      : [];
    // Older receipts were already written with the binding inside `files`. The Conversation
    // history is the receipt, so the card repairs that old shape while leaving the row untouched.
    return Array.from(new Set([...retained, ...legacy]));
  }

  function writtenCrossingFiles(crossed) {
    const retained = new Set(retainedBindingFiles(crossed));
    return (crossed.files || []).filter((file) => !retained.has(file));
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
    const files = writtenCrossingFiles(crossed);
    const retained = retainedBindingFiles(crossed);
    return h(
      'div',
      { className: 'sw-crossing' },
      h(
        'div',
        { className: 'sw-crossing-line' },
        'The plan is in ',
        h('strong', null, `"${named}"`),
        // No article engine: "a new {builtApp}" is safe because the article sits before `new`, but
        // the other branch had the article against the noun, so it takes the plural instead.
        crossed.newApp
          ? SW.brand.text(' — a new {builtApp}.')
          : SW.brand.text(' — one of the {builtAppPlural} you already had.'),
        ' It included ',
        carried(crossed),
        '.'
      ),
      h(
        Button,
        { type: 'link', size: 'small', className: 'sw-crossing-toggle', onClick: onToggle },
        open ? 'Hide details' : 'What was included'
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
                  // A span, not a bare string: the title has to be an element for CSS to hold it
                  // on one line, and `title` is what a reader hovers when a long one is elided.
                  h(
                    'span',
                    {
                      className: 'sw-crossing-name',
                      title: chart.title || chart.path,
                    },
                    chart.title || chart.path
                  ),
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
          files.length > 0 &&
            h(
              'div',
              { className: 'sw-crossing-group' },
              // Not "written into the app": `examples/` is the Project's, and the point of naming
              // paths at all is that a person can go and open exactly what is named.
              h('div', { className: 'sw-field-label' }, 'Files written to the project'),
              files.map((file) =>
                h('div', { key: file, className: 'sw-crossing-item' }, h('code', null, file))
              )
            ),
          retained.length > 0 &&
            h(
              'div',
              { className: 'sw-crossing-group' },
              h('div', { className: 'sw-field-label' }, 'Already in the app'),
              retained.map((file) =>
                h(
                  'div',
                  { key: file, className: 'sw-crossing-item' },
                  h('code', null, file),
                  ' - a binding you made earlier. Turning Resources off did not withdraw it.'
                )
              )
            ),
          // An Upload the composer wrote crosses by becoming an Attachment (ADR-0023) — named here
          // by the Dataset it landed in, or by the reason it stayed in Chat when no Dataset was
          // writable to receive it. The refusal is the actionable half of that sentence, so it goes
          // in bare rather than under `.sw-crossing-name`, whose one-line ellipsis is built for a
          // short chart title and would hide the reason a person most needs to read.
          (crossed.uploads || []).length > 0 &&
            h(
              'div',
              { className: 'sw-crossing-group' },
              h('div', { className: 'sw-field-label' }, 'Uploads'),
              (crossed.uploads || []).map((u, i) =>
                h(
                  'div',
                  { key: `${u.name}:${i}`, className: 'sw-crossing-item' },
                  u.crossed && h('span', { className: 'sw-crossing-name' }, u.name),
                  u.crossed && u.dataset && h('code', null, u.dataset),
                  !u.crossed && u.reason
                )
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
    // Open by default only when an Upload stayed in Chat: that refusal is the one crossing outcome
    // ADR-0023 says must never be silent, and a collapsed panel is silent until someone clicks it.
    const [showCrossing, setShowCrossing] = useState(
      () => (block.crossed?.uploads || []).some((u) => !u.crossed)
    );
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
          `"${crossed.appName || crossed.appId}" is still here, with everything that was copied `
          + 'into it. The plan is archived, not deleted.'
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
              'Change what was included'
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

  // The guardrail search's card (ADR-0022). One block type, two frames: a spinner while the search
  // runs, then the answer in its place. `store.putWithholdCard` does the replacing, so React keeps
  // this component instance across the two — which is why nothing here may seed `useState` from the
  // settled data. It would run once, on the spinner frame, and never see what was found.
  //
  // Deliberately the `sw-nudge` shape, not `sw-suggestion`: this is the quiet rung. Clearing Recall
  // is the loud one, and its card sits below this when the search could not account for everything.
  function WithholdCard({ block }) {
    const [busy, run] = SW.util.useBusyAct();
    if (block.searching) {
      return h('div', { className: 'sw-nudge' },
        h(Spin, { size: 'small', style: { marginTop: 4 } }),
        h('div', { className: 'sw-nudge-main' },
          h('div', null, 'Finding what the policy matched…')));
    }

    const carriers = block.carriers || [];
    const named = (c) => (c.is_file
      ? h('strong', { key: c.key, className: 'sw-withhold-file' }, c.label)
      : h('span', { key: c.key }, c.label));
    const list = carriers.length === 1
      ? [named(carriers[0])]
      : carriers.flatMap((c, i) => (i ? [', ', named(c)] : [named(c)]));

    // Nothing to take away. Said plainly rather than left blank: the clear-Recall offer renders
    // underneath, and a person owes an explanation for why the cheaper rung was skipped.
    if (!carriers.length) {
      return h('div', { className: 'sw-nudge' },
        h('span', { className: 'sw-scope-dot is-hollow', style: { marginTop: 5 } }),
        h('div', { className: 'sw-nudge-main' },
          h('div', null, block.stopped === "not in this conversation's content"
            ? "What the policy matched isn't in anything this conversation can stop sending."
            : "Sage couldn't work out which part the policy matched.")));
    }

    // Found, but withholding them does not clear the refusal — so there is more than these, and
    // offering to take them away would promise a fix that the next turn would disprove.
    if (!block.complete) {
      return h('div', { className: 'sw-nudge' },
        h('span', { className: 'sw-scope-dot is-hollow', style: { marginTop: 5 } }),
        h('div', { className: 'sw-nudge-main' },
          h('div', null, 'The policy matched ', ...list,
            ", and something else as well that Sage couldn't pin down.")));
    }

    const survives = (block.surviving || 0) > 0;
    // No file anywhere in the set. Gates one CLAUSE, not a sentence and a button: there is no file
    // to name, and withholding is not redaction (ADR-0022's one hard promise). Everything after it
    // — what survives, what the button says — is the single rule below. While this arm kept its own
    // copy of that ending it drew byte-identical cards at `surviving: 0` and `surviving: 2`, and
    // said "what you wrote" over rows a `bash cat` fetched (#297).
    //
    // Read off the carriers rather than counted, for the reason the button below it is. This was
    // `carriers.length === 1 && !carriers[0].is_file`, and a second matched message drew that
    // destructive-sounding button with ADR-0022's promise nowhere on the card (#309). The count was
    // never what the clause is about: "no file Sage can name" is as true of five non-file carriers
    // as of one, and every population `withhold.py`'s `_text_label` names — a typed message, an
    // earlier answer, `bash cat`'s pathless rows — satisfies it whatever it arrives beside.
    //
    // Still one condition narrow, and that is a recorded GAP rather than a decision: a named file
    // BESIDE a typed message withholds the promise, because the clause is gated on what a carrier
    // arrives next to. `RecallWithheld` above says "Nothing was changed or deleted" for that same
    // click unconditionally, so the card is quieter before the click than the receipt is after it.
    // Not widened here: "not in a file Sage can name" is false of the file, so the promise has to
    // come apart from the file clause first, and that prose is #292's. Filed as #337.
    const noFileToName = !carriers.some((c) => c.is_file);
    // What the button acts on, read off the carriers rather than counted. Counting them and
    // calling whatever turned up "files" is what put "Stop sending these files" under a set
    // holding none, in this arm and in the `survives` one alike (#292).
    //
    // Three label populations arrive here, not two: `withhold.py`'s `_text_label` names a non-file
    // carrier "the message you sent", "an earlier answer in this conversation", OR "something a
    // tool read" — rows a turn fetched through `bash cat` or `grep`, which carry no path and so no
    // `is_file`. "These messages" would therefore be this same bug one word over. The card cannot
    // separate the three on its own, and sending it more would not fix that: `Carrier.is_data` is
    // the field that looks like the distinction and is not one. Since #290 it reads
    // `is_tool || _carries_mention`, so an @mention's rows make a carrier labelled "the message you
    // sent" arrive as data — a card that trusted it would name the wrong author in the other
    // direction. (`withhold.py` belongs to #290 and #307 rather than to this branch, which is the
    // second and weaker reason: the field would be the wrong answer even if it were free to edit.)
    // So the bare demonstrative is not a shorthand for a better word — it is the only noun that is
    // true of every set this branch can draw, which is the reason not to "improve" it into one of
    // the three later.
    //
    // "Them" rather than "these" because the sentence above already ends "Stop sending them and
    // this conversation will work again": the button and the prose become the same string, which
    // is what the singular/plural note up there is asking for. The list of names renders directly
    // above, so nothing is lost by the button not repeating them.
    const noun = carriers.every((c) => c.is_file)
      ? (carriers.length > 1 ? 'these files' : 'this file')
      // The singular arm is reachable since #297, which folded the button's no-file case into
      // this one rule. It needed no new word because it was written correct while it was still
      // unreachable — the reason to keep writing an unreachable arm as though someone will run it.
      : (carriers.length > 1 ? 'them' : 'it');
    // Whether the click actually carries on, which is the store's question and not this card's —
    // and it is ASKED here rather than answered. `withholdRerunPrompt` is the same call
    // `withholdContent` makes to decide whether to re-run the turn, so the button cannot promise a
    // thing the click does not do.
    //
    // It was derived here once, from `surviving` and `prompt`, and that copy was a clause short of
    // the click's three: the store also needs a question to re-send, and a drawn transcript with
    // no user row holding text has none. Both fields can say carry on over that transcript, so the
    // card read "Continue without it", the click withheld and returned, and no turn ran — the
    // button said the conversation carries on and nothing carried on, which is #297's defect one
    // condition further out (#311).
    //
    // Read by the button and by the prose's last sentence, which are the two places that describe
    // the ACT. The prose's other sentences read one field each, because each states one fact:
    // what is left reads `survives`, and whether the question is going reads `prompt`. Two
    // questions, two fields — and the pair only where the subject is what the click does.
    const carriesOn = !!SW.store.withholdRerunPrompt(block);
    return h('div', { className: 'sw-nudge' },
      h('span', { className: 'sw-scope-dot is-hollow', style: { marginTop: 5 } }),
      h('div', { className: 'sw-nudge-main' },
        h('div', null,
          h(React.Fragment, null, 'It matched values in ', ...list,
            // Author-neutral on purpose. The promise is that withholding is not redaction, and
            // that is true whoever wrote the thing; "what you wrote" was said over a tool's
            // rows (#297). Nothing is lost by not naming the author — the label two words
            // earlier already does, which is #292's argument for the bare noun on the button.
            //
            // The promise and nothing else. Naming the act here too ("— only stop sending it")
            // read as a third "stop sending" beside the ending's and the button's, and in the
            // `survives` arm it gave one act two names — prose "stop sending", button "Continue
            // without" — which is what the note under the button forbids. The ending below names
            // the act once, in whichever of its two arms is drawn.
            // "Sage can name" is doing real work: a `bash cat transactions.csv` DID read a file,
            // and lands here only because no path came with it (`withhold.py` — "rows are data
            // with no filename"). The bare "not in a file" told that person something false in
            // the same sentence that used to tell them they wrote it. True of the other two
            // populations too, which is what lets one string serve all three.
            //
            // "what it matched" rather than "it": the nearest noun to a bare "it" here is the FILE
            // the clause has just said is not involved, so the promise read as being about a file.
            // Dropping the author to serve the `bash cat` population dropped the referent with it;
            // this names the subject again without naming who wrote it. "It" is the policy, the
            // same "It" this sentence opens with.
            //
            // The dash rather than a comma: `...list` above is comma-joined with no "and", so on
            // the plural sets this gate now reaches, a comma here reads as one more thing the
            // policy matched. One string serves every size, and that is what stops the promise
            // going missing on whichever size a second string was not written for (#309).
            noFileToName
              ? " — not in a file Sage can name. Sage won't change what it matched. "
              : ". Sage won't change what it matched. ",
            // Three facts, each drawn when its own is true, joined rather than branched between.
            // They co-occur in every combination — a turn can have nothing left to answer from AND
            // lose its question, or keep every file AND lose its question. Each earlier shape here
            // was a ternary over the COMBINATION, and each silently dropped whichever fact it had
            // not branched on: the button lagged the store, then the prose lagged the button, then
            // the co-occurring card said only the first of two true things (#297). One clause per
            // fact is what stops that recurring, and `filter(Boolean)` is why no arm needs to
            // carry a space for the arm beside it.
            [
              survives
                ? 'Nothing else this turn read is affected.'
                // ADR-0022's premise, that the refusal outlives the turn and would refuse every
                // later one. Without this line the person reads a dead question and a button with
                // no stated benefit (#288).
                : "That was everything this turn read, so this question can't be answered "
                  + "from what's left.",
              // The question goes, the files stay. Re-running would ask nothing: the same words
              // hash to the same key and are stopped before they are sent, so the benefit on offer
              // is the NEXT turn rather than this one. Said before the click rather than only on
              // the receipt underneath it, which is where a person met it first (#297).
              block.prompt
                ? 'Your question is one of the things that matched, so asking it again in the '
                  + 'same words would be stopped before it is sent.'
                : '',
              // Only where the click does NOT re-run the turn, which is the store's pair and the
              // button's word. Promising the conversation will work again beside a button that
              // re-runs this one would describe the wrong act.
              carriesOn
                ? ''
                : 'Stop sending '
                  // Singular or plural with the button beneath, which counts carriers the
                  // same way — and, since #292, says the same word: any set that is not all
                  // files puts this exact "them" on the button.
                  + (carriers.length > 1 ? 'them' : 'it')
                  + ' and this conversation will work again — ask something else'
                  // Only offer the attachment when the turn READ what went. `is_fetched`, not
                  // `is_file`: `is_file` asks whether Sage can name the file, and a
                  // `bash cat transactions.csv` came out of a file that no path arrived with, so
                  // reading it here ended the one card a different file would actually fix with no
                  // remedy at all (#312). Both file populations get it wherever this arm is
                  // drawn at all, and the sentence above has already told the unnamed one that
                  // Sage cannot say which file it was. Those two sit together on purpose and do
                  // not argue: one says what SAGE can name, the other what the PERSON can do, and
                  // the person reading "a different file" knows which file they ran the command
                  // over. Rewording it to drop the comparison would cost the three populations
                  // where a file WAS named the only concrete thing the card tells them to do.
                  // Wherever the turn CARRIES ON the whole
                  // clause is absent, named file and `cat` alike — the button re-runs the turn
                  // there and nothing needs attaching. That gate is `carriesOn`'s, #311's, not
                  // this line's — it read #309 until #309 landed and turned out to own the promise
                  // clause forty lines up instead.
                  //
                  // Still no offer where nothing was read — several matched messages with no file
                  // anywhere, which is the population this gate was written against (#297): a
                  // person told to attach a different one goes looking for something their
                  // conversation never had.
                  //
                  // `Carrier.is_data` was the obvious field and is deliberately not sent here.
                  // Since #290 an @mention's inlined descriptor makes a carrier LABELLED "the
                  // message you sent" arrive as data, so hanging the offer there would put it back
                  // on prose — this same defect, inverted.
                  //
                  // `is_file` is read beside it for the rows ALREADY IN TRANSCRIPTS. A named file
                  // is a `read` result, so the two agree on every row written from here on; the
                  // pair is what stops a card re-drawn from a row written before the field existed
                  // from silently losing the offer it was first rendered with.
                  + (carriers.some((c) => c.is_fetched || c.is_file)
                    ? ', or attach a different file.' : '.'),
            ].filter(Boolean).join(' '))),
        h('div', { className: 'sw-withhold-scope' },
          'Applies to this conversation. A new conversation starts fresh.'),
        block.live
          ? h('div', { style: { marginTop: 8 } },
              h(Space, { size: 8, wrap: true },
                h(Button, {
                  type: 'primary', size: 'small',
                  loading: busy === 'withhold', disabled: !!busy,
                  onClick: run('withhold', () => SW.store.withholdContent(block)),
                  // Names the thing: a destructive-sounding button that does not say what it
                  // acts on is the one people refuse to press. The plural "them" is not an
                  // exception to that — `...list` sets out the names two lines above it, and the
                  // rule this was written against is a button standing alone with nothing on
                  // screen to resolve it. "Continue without" is only honest while something
                  // survives to continue WITH.
                }, (carriesOn ? 'Continue without ' : 'Stop sending ') + noun),
                h(Button, {
                  type: 'text', size: 'small', disabled: !!busy,
                  onClick: () => SW.store.dismissWithholdCard(block),
                }, 'Dismiss')))
          : null));
  }

  function GraduationNudge({ onSave }) {
    const { thread, resourceGroups } = SW.store.get();
    const files = (resourceGroups.file || []).filter((f) => f.sandbox);
    const artifacts = ((thread && thread.artifacts) || []).filter((a) => !a.missing);  // #255
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

  // Without `live` this is the status line it has always been: a replayed refusal is a record of a
  // gap, not an offer to close one, and its buttons would act on whichever app is selected today
  // rather than on the app the sentence names. Same rule as the three offers around it, and drawn
  // the old way on purpose so a reloaded transcript reads exactly as it did before this shipped.
  function MentionsUnresolved({ block }) {
    const { activeApp } = SW.store.get();
    const [busy, run] = SW.util.useBusyAct();
    // The third argument is what the click sends once it has written the record (#213): the request
    // that was refused, made again against the Binding or the Attachment that had been missing. Not
    // a gate being answered — this turn RAN and the agent declined it — so it is an ordinary send
    // with no skips, which is why it reaches `sendBuildPrompt` straight rather than through one of
    // the named answers beside it.
    const fixes = block.live
      ? SW.store.mentionFixes(block.entries, activeApp && activeApp.id,
                              block.prompt ? () => SW.store.sendBuildPrompt(block.prompt) : null)
      : [];
    if (!fixes.length) {
      // Nothing to click, so the way out is said instead (#213). The server's sentence stops at
      // what happened, because the live card draws the fix — this branch is the one that knows
      // there is none, and the only place the old instruction still belongs. A drop with no act
      // behind it kept its own directions in the prose and adds nothing here.
      const hints = SW.store.mentionFixHints(block.entries);
      return h('div', { className: 'sw-status-line is-err' },
               [block.message, ...hints].join(' '));
    }
    return h(
      'div',
      { className: 'sw-nudge' },
      h('span', { className: 'sw-scope-dot is-hollow', style: { marginTop: 5 } }),
      h(
        'div',
        { className: 'sw-nudge-main' },
        h('div', null, block.message),
        h(
          'div',
          { style: { marginTop: 8 } },
          h(Space, { size: 8, wrap: true }, fixes.map((fix, i) =>
            h(Button, {
              key: fix.key,
              // One filled button per card, whichever drop came first. Three side by side would be
              // three primary actions in one place, which is no hierarchy at all.
              type: i === 0 ? 'primary' : 'default',
              size: 'small',
              loading: busy === fix.key,
              disabled: !!busy,
              onClick: run(fix.key, fix.act),
            }, fix.label)))
        )
      )
    );
  }

  // The offer to open an investigation, drawn instead of an answer (#386, ADR-0056).
  //
  // Both buttons run the question. The card ENDED a turn that would otherwise have answered, so a
  // `No` that only recorded a decision would have charged the person a round trip for a card they
  // never asked for. `Yes` runs it with a shell and the record says so until they close it; `No`
  // runs exactly the turn that would have run anyway, and the offer does not come back here.
  //
  // `block.live && block.prompt`, the house rule for every card that can run a turn: the server's
  // copy carries no `live`, so a reload leaves the sentence with its buttons gone rather than a
  // grant somebody can make by scrolling back through a conversation.
  function InvestigationOffer({ block }) {
    const [busy, run] = SW.util.useBusyAct();
    const answer = (decision) => () =>
      SW.store.answerInvestigationAndAsk(block.prompt, block.threadId, decision);

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
                  loading: busy === 'open',
                  disabled: !!busy,
                  onClick: run('open', answer('open')),
                }, 'Investigate'),
                h(Button, {
                  size: 'small',
                  loading: busy === 'decline',
                  disabled: !!busy,
                  onClick: run('decline', answer('decline')),
                }, 'Just answer this'))
            )
          : null
      )
    );
  }

  // The door onto the lane that can compute, drawn under a finished answer (#411, ADR-0058).
  //
  // TWO BUTTONS, AND BOTH ANSWERS ARE ABOUT THIS CALCULATION RATHER THAN THIS CONVERSATION.
  // "Work it out" replays the question once under the server-minted grant the card arrived with, so
  // the turn runs unbounded and then the grant is gone. It is deliberately NOT the investigation
  // card's accept: that one opens a standing grant on the Thread, and accepting one calculation
  // must not do that — nor touch the investigation card's own answer in either direction (#389).
  // "Not now" calls nothing at all: the answer this card sits under is already on screen, so no
  // question is owed and nothing replays, and a decline that wrote a row would be a receipt for
  // nothing happening — the rule ADR-0056 states for its own decline. Retiring the card locally
  // leaves the person looking at exactly what a reload shows, which is the sentence without buttons.
  function OtherLaneOffer({ block }) {
    const [busy, run] = SW.util.useBusyAct();
    const [dismissed, setDismissed] = useState(false);

    return h(
      'div',
      { className: 'sw-nudge' },
      h('span', { className: 'sw-scope-dot is-hollow', style: { marginTop: 5 } }),
      h(
        'div',
        { className: 'sw-nudge-main' },
        h('div', null, block.message),
        block.live && block.prompt && !dismissed
          ? h(
              'div',
              { style: { marginTop: 8 } },
              h(Space, { size: 8, wrap: true },
                h(Button, {
                  type: 'primary',
                  size: 'small',
                  loading: busy === 'open',
                  disabled: !!busy,
                  onClick: run('open', () => SW.store.workItOutOnTheOtherLane(
                    block.prompt, block.threadId, block.grant)),
                }, 'Work it out'),
                h(Button, {
                  size: 'small',
                  disabled: !!busy,
                  onClick: () => setDismissed(true),
                }, 'Not now'))
            )
          : null
      )
    );
  }

  // The way back into a turn the ceiling stopped (#454). Drawn UNDER the block that says what
  // happened, like the card above it, and for the same reason: the turn has ended, `done` has been
  // read, and this is an offer rather than a question the turn is waiting on.
  //
  // ONE BUTTON, and no decline beside it. "Not now" on the card above retires a card that is
  // sitting under a finished answer; here there is no answer, the block above says so, and a
  // second button to say "leave it then" adds a click that does nothing the person is not already
  // doing by not pressing the first one. Nothing is recorded either way — a ceiling records no
  // decision, so the next turn that hits one offers again.
  //
  // `live` is what keeps a week-old transcript from starting ten minutes of work on a page load
  // nobody connected to it; without it this is the sentence alone, which is what it is a record of.
  function ContinueAfterTheCeiling({ block }) {
    const [busy, run] = SW.util.useBusyAct();

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
                loading: busy === 'continue',
                disabled: !!busy,
                onClick: run('continue', () => SW.store.continueAfterTheCeiling(
                  block.prompt, block.threadId)),
              }, 'Continue')
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
    const [busy, run] = SW.util.useBusyAct();

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
    const [busy, run] = SW.util.useBusyAct();
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

  // The Data Sources this caller can reach, for the person to pick one (#185, ADR-0038). The
  // question before the card below it: a table search needs a store to search, and an app that
  // records none leaves it nothing to read.
  //
  // Every source is drawn even where there is exactly one, because using the only one silently is
  // the same inference through a side door — and the day a second one appears the app would start
  // reading somewhere else without anybody saying so.
  //
  // Same `live` rule as every offer here: a replayed card would record a Binding and start a build
  // out of a message somebody is scrolling back through.
  function SourceCandidates({ block }) {
    const [busy, run] = SW.util.useBusyAct();
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
                (block.sources || []).map((source, i) => h(Button, {
                  key: source.id,
                  // One filled button per card, and only where the request named a store: the
                  // server put that one first and says so. Where it named none the order is the
                  // listing's own, and drawing its head as the recommended row would be a
                  // recommendation made out of nothing.
                  type: block.named && i === 0 ? 'primary' : 'default',
                  size: 'small',
                  loading: busy === source.id,
                  disabled: !!busy,
                  onClick: run(source.id, () => SW.store.chooseSourceAndSearch(
                    block.prompt, source.id, source.name, block.answered)),
                }, source.name)),
                // The way past a question this request was never asking. The words that reach this
                // card are a heuristic, and one that is wrong has to cost a click rather than a
                // retyped request. It is also the only button on the card for a caller the
                // platform offers nothing.
                h(Button, {
                  type: 'text',
                  size: 'small',
                  loading: busy === 'none',
                  disabled: !!busy,
                  onClick: run('none',
                    () => SW.store.buildWithoutSource(block.prompt, block.answered)),
                }, 'Build without one'))
            )
          : null
      )
    );
  }

  // The tables a search found, for the person to pick one (#183, ADR-0038). Sage finds; the person
  // binds — so this card IS the declaration, and every button on it writes the same record the
  // panel's picker writes before replaying the request that produced the card.
  //
  // Grouped by schema because the schema is the difference being asked about: `MARTS.GONG__CALLS`
  // is the modeled table a daily summary wants and `STAGING.STG_GONG__CALLS` is the raw one it does
  // not, and the two names alone cannot say which is which. Every table stays reachable behind
  // "show all", so a ranking that put the right one sixth costs a click and never a dead end.
  //
  // Same `live` rule as the two offers above, and the strongest case of it: a replayed card would
  // write a record and start a build out of a message somebody is only scrolling back through.
  function TableCandidates({ block }) {
    const [busy, run] = SW.util.useBusyAct();
    // With nothing matched, the shortlist is only the alphabetical head of the catalog, so opening
    // on the whole list is what keeps the card honest — five arbitrary names laid out like answers
    // read as answers. Only where the whole list is a list: a real warehouse holds 602 tables, and
    // mounting all of them the moment a vague prompt lands costs a paint on every re-render of the
    // transcript. Past that the button below says how many there are and one click opens them.
    // DERIVED, not seeded, because this component is not remounted between the searching card and
    // the settled one (#186): `putTableCard` replaces the block at the same index and the transcript
    // keys blocks by index, so React keeps the instance. A `useState` initializer would therefore
    // run exactly once — on the first searching frame, where `total` and `matched` are both 0 — and
    // hold `all` true through a settled card carrying 602 tables, which is the one case the cap
    // exists to prevent. The state is only the click, which is the only thing state is for here.
    const [expanded, setExpanded] = useState(false);
    const all = expanded || (!block.matched && (block.total || 0) <= 60);
    const groups = (all ? block.allGroups : block.groups) || [];
    const shown = groups.reduce((n, g) => n + (g.tables || []).length, 0);
    const hidden = (block.total || 0) - shown;

    // The warehouse is still being read (#186). The names found so far are shown and none of them
    // is a button: reading is what turns a fifteen-second silence into a wait somebody can sit
    // through, and picking waits for the walk because a click sends the request again — a request
    // sent now would queue behind the very turn that is still walking, which would then finish by
    // drawing its settled card onto a transcript that had already answered one.
    //
    // `block.groups` and not `groups`: a searching frame carries the shortlist alone, because there
    // is no whole list yet to show all of, and reading `allGroups` here would draw an empty card
    // under a sentence promising names.
    if (block.searching) {
      const reading = block.groups || [];
      return h(
        'div',
        { className: 'sw-nudge' },
        h(Spin, { size: 'small', style: { marginTop: 4 } }),
        h(
          'div',
          { className: 'sw-nudge-main' },
          h('div', null, block.message),
          reading.length
            ? h('div', { className: 'sw-table-candidates is-reading' },
                reading.map((group) => h(
                  'div',
                  { className: 'sw-table-group', key: `${group.database}.${group.schema}` },
                  h('div', { className: 'sw-table-group-head' },
                    [group.database, group.schema].filter(Boolean).join('.')),
                  h(Space, { size: 6, wrap: true },
                    (group.tables || []).map((table) => h(
                      'span', { className: 'sw-table-pick is-reading', key: table }, table)))
                )))
            : null
        )
      );
    }

    return h(
      'div',
      { className: 'sw-nudge' },
      h('span', { className: 'sw-scope-dot is-hollow', style: { marginTop: 5 } }),
      h(
        'div',
        { className: 'sw-nudge-main' },
        h('div', null, block.message),
        // Gone rather than greyed out on a replay, which is what the three offers above do and what
        // matters most here: hundreds of dead buttons under a sentence still asking somebody to pick
        // one reads as an app that has broken, not as a question already answered.
        block.live && block.prompt
          ? h(
              'div',
              { className: 'sw-table-candidates' },
              groups.map((group) => h(
                'div',
                { className: 'sw-table-group', key: `${group.database}.${group.schema}` },
                // The whole position, not the schema alone: `DWH.MARTS` and `SANDBOX.MARTS` are two
                // places, and the heading tells a reader which one they are picking out of.
                h('div', { className: 'sw-table-group-head' },
                  [group.database, group.schema].filter(Boolean).join('.')),
                h(Space, { size: 6, wrap: true },
                  (group.tables || []).map((table) => h(Button, {
                    key: table,
                    size: 'small',
                    className: 'sw-table-pick',
                    // Keyed on the whole position for the same reason the heading carries it: two
                    // schemas can hold one table name, and a key that dropped the database would
                    // spin both rows on one click.
                    loading: busy === `${group.database}.${group.schema}.${table}`,
                    disabled: !!busy,
                    // Which door the click writes through (#188). A card drawn in Chat carries the
                    // Thread it belongs to, and the table goes on that conversation's own row —
                    // Chat has no Built App to hold a Binding, and the record crosses at the
                    // handoff. Without a Thread this is the Build card and writes the Binding.
                    onClick: run(`${group.database}.${group.schema}.${table}`,
                      () => (block.threadId
                        ? SW.store.chooseTableAndAsk(
                          block.prompt, block.threadId, block.sourceId,
                          { database: group.database, schema: group.schema, table },
                        )
                        : SW.store.chooseTableAndBuild(
                          block.prompt, block.sourceId,
                          { database: group.database, schema: group.schema, table },
                          block.answered, block.bindFirst,
                        ))),
                  }, table)))
              )),
              hidden > 0
                ? h(Button, {
                    type: 'link',
                    size: 'small',
                    className: 'sw-table-more',
                    onClick: () => setExpanded(true),
                    // Through the pack, because the sentence right above it is: a button reading
                    // "Show all 602 tables" under "Pick the Dataset table…" is one surface using
                    // two words for one thing. The marked-position lint cannot catch a bare
                    // literal here, which is the reason to write it correctly the first time.
                  }, SW.brand.text('Show all {count} {scopePlural}', { count: block.total }))
                : null
            )
          : null
      )
    );
  }

  // What a bound Dataset holds, for the person to pick from (#196, ADR-0039). The click ATTACHES,
  // and that is the whole design rather than a step before one: a Dataset gets no scope on its
  // Binding, because its declaration already exists and is the Attachment. So this card writes the
  // record the product already has, on the surface that already owns it (ADR-0021).
  //
  // A row is a folder or a file, and the SERVER decided which — above the collapse threshold the
  // folder is the row, so answering a Dataset partitioned to the day is one click instead of two
  // hundred (ADR-0029, ADR-0030). Nothing here re-derives that: a second copy of the roll-up in
  // JavaScript is exactly how this card and the `@` menu would come to disagree about what a
  // Dataset looks like.
  //
  // Same `live` rule as the three offers above it, and the sharpest reason for it here: a replayed
  // card's buttons would attach files to an app and start a build out of a message somebody is only
  // scrolling back through.
  function DatasetFiles({ block }) {
    const [busy, run] = SW.util.useBusyAct();
    // DERIVED, not seeded, for the reason the card above documents at length: the transcript keys
    // blocks by index and React keeps the instance, so a `useState` initializer runs once and would
    // hold that first answer over every later render of this card.
    const [expanded, setExpanded] = useState(false);
    const rows = (expanded ? block.allRows : block.rows) || [];
    const hidden = (block.total || 0) - rows.length;
    // Keyed on kind AND path: a folder row and a file row can name the same path — the root folder
    // of a Dataset holding one loose file — and a key that dropped the kind would spin both.
    const at = (row) => `${row.kind}:${row.path}`;
    // A folder row with no path is the Dataset taken whole, which is the same act at depth 0 rather
    // than a second one (see `_folder_prefix`). It says the Dataset's name, because "" would be a
    // button with no label on it.
    const label = (row) => row.path || block.datasetName;
    const pick = (row) => (block.threadId
      // In Chat the click writes a `dsfile:` chip and nothing else — there is no Built App to
      // attach to, and the chip is what a handoff already turns into `App.requires`.
      ? SW.store.pinDatasetFileAndAsk(block.prompt, block.threadId, block.datasetId, row.path)
      : (row.kind === 'folder'
        ? SW.store.attachFolderAndBuild(block.prompt, block.datasetId, row.path, block.answered)
        : SW.store.attachFileAndBuild(block.prompt, block.datasetId, row.path, block.answered)));
    return h(
      'div',
      { className: 'sw-nudge' },
      h('span', { className: 'sw-scope-dot is-hollow', style: { marginTop: 5 } }),
      h(
        'div',
        { className: 'sw-nudge-main' },
        h('div', null, block.message),
        // Gone rather than greyed out on a replay, which is what every offer around this does: a
        // list of dead buttons under a sentence still asking somebody to pick reads as an app that
        // has broken, not as a question already answered.
        block.live && block.prompt
          ? h(
            'div',
            null,
            h(
              'div',
              { className: 'sw-dataset-files' },
              rows.map((row) => h(
                Button,
                {
                  key: at(row),
                  size: 'small',
                  className: 'sw-dataset-pick',
                  loading: busy === at(row),
                  disabled: !!busy,
                  onClick: run(at(row), () => pick(row)),
                },
                label(row),
                // What the row stands for, which the label cannot say for a folder: the click
                // carries every file below it, and a row that showed only a path would understate
                // an act that attaches two hundred files.
                //
                // A file row with no `size` at all is a file the listing named without measuring,
                // which is what a Dataset with no mount here can hand back. It draws bare rather
                // than as "0 bytes" (#197): the server left the key off precisely so this does not
                // have to guess whether a zero means empty or unmeasured.
                (row.kind === 'folder' || row.size !== undefined)
                  ? h('span', { className: 'sw-dataset-count' },
                      row.kind === 'folder'
                        ? ` — ${SW.util.number(row.count)} ${row.count === 1 ? 'file' : 'files'}`
                        : ` — ${SW.util.bytes(row.size)}`)
                  : null
              )),
              hidden > 0
                ? h(Button, {
                  type: 'link',
                  size: 'small',
                  className: 'sw-dataset-more',
                  onClick: () => setExpanded(true),
                }, `Show all ${SW.util.number(block.total)} rows`)
                : null
            ),
            // The way past the question, for an app that genuinely holds its own data and for a
            // person who does not want to answer. Without it a Dataset nobody wants to attach from
            // puts this card in front of every request forever.
            h(Button, {
              type: 'text',
              size: 'small',
              loading: busy === 'none',
              disabled: !!busy,
              onClick: run('none', () => (block.threadId
                ? SW.store.askWithoutAttaching(block.prompt, block.threadId, block.datasetId)
                : SW.store.buildWithoutAttaching(
                  block.prompt, block.datasetId, block.answered))),
            }, block.threadId ? 'Answer without a file' : 'Build without attaching anything')
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

  // Build's fold of another app's Lead-in (ADR-0019). Build shows the SELECTED app's Lead-in, so
  // every Chat turn that led to a different app folds — but it folds WHERE IT SAT, named and
  // counted, because a transcript that simply dropped those turns would be the loss #57 was filed
  // about wearing a different coat.
  //
  // Open is view state, held here and nowhere else: it closes on an app switch and on reload,
  // because "show me those two turns" is a glance, not an answer a person gives once.
  function LeadInFold({ block }) {
    const [open, setOpen] = useState(false);
    const turns = block.messages || [];
    return h(
      'div',
      { className: 'sw-leadin' },
      h(
        'div',
        { className: 'sw-leadin-head' },
        h('span', { className: 'sw-leadin-face' },
          `${block.count} turn${block.count === 1 ? '' : 's'} about ${block.appName}`),
        h(
          Button,
          { type: 'link', size: 'small', onClick: () => setOpen(!open) },
          open ? 'Hide the turns' : 'Show the turns'
        )
      ),
      open && h(
        'div',
        { className: 'sw-leadin-turns' },
        turns.map((message) => h(SW.Message, { key: message.id, message }))
      )
    );
  }

  // A turn's STEPS, folded behind one face (ADR-0063). An investigation spends most of its reads
  // finding out where to read — sixty table cards for one question, measured — and every one of
  // them was drawn as though it were the answer.
  //
  // The same fold as `LeadInFold` above, and deliberately the same one rather than a new pattern:
  // open is view state held here and nowhere else, it closes on reload, and the face counts what
  // is behind it so the gap in the transcript reads as work rather than as a hole.
  //
  // What is NOT the same is that the rows behind this face have not been READ. A `.table.json` is
  // the only Artifact kind that costs a round trip, and a sixty-row investigation is sixty of them
  // through the Domino proxy (#451) — so the fetch is deferred with the render, and the one moment
  // that can start it is somebody opening this.
  //
  // The face never says what was IN the rows, only how many there were. That is what makes the
  // fallback safe: a viewer who sees a step they did not need has a cluttered transcript, and a
  // viewer whose answer was folded away has been lied to with no way to find out.
  function WorkingReadsFold({ block }) {
    const [open, setOpen] = useState(false);
    // ONE state, the shape `SandboxRun` uses two hundred lines up and for the same reason: `null`
    // is "nobody has asked yet" and an array is the answer, so "still reading" is derived rather
    // than stored and cannot disagree with itself.
    //
    // There is no third, failed state here, and that is deliberate rather than an omission.
    // `blocksForArtifacts` catches per ROW — a file that will not read comes back as its own
    // "Open the file" link, in its own place in the order — so this promise has no rejection to
    // report, and a branch nothing can reach is a branch nothing has run.
    // `test_a_fold_whose_rows_cannot_be_read_offers_the_way_back` is that claim.
    //
    // One row does come back as NOTHING rather than as a link: a `.table.json` that is there but
    // blank is hidden outright, and it is hidden the same way inside this fold as outside it. So
    // `count` — which is what the turn WROTE — can be larger than the number of cards behind the
    // face. The face says "read N tables", which is a claim about reads and stays true; it is
    // worth knowing that the two numbers are not the same number.
    const [rows, setRows] = useState(null);
    // Whether a read is already on its way. A ref rather than state, because nothing renders it
    // and a re-render must not reset it.
    //
    // Without this, closing and reopening the fold before the first read lands reads everything
    // twice. The deps below go `[true, true]` → `[false, true]` → `[true, true]`, which React
    // sees as changed, and `rows` is still `null` so the guard lets it through. On the sixty-read
    // investigation this card exists for, one impatient double-click is sixty duplicate round
    // trips through the Domino proxy — the exact cost #451 measured and this ticket defers.
    const inFlight = useRef(false);
    const items = block.items || [];
    const count = typeof block.count === 'number' ? block.count : items.length;
    const reading = open && rows === null;

    // The condition IS the trigger, not the click: a fold can be open without anyone having
    // clicked it, which is a state a re-render can arrive in on its own.
    useEffect(() => {
      if (!(open && rows === null) || inFlight.current) return;
      inFlight.current = true;
      SW.hydrateArtifacts(items).then((blocks) => {
        inFlight.current = false;
        setRows(blocks);
      });
    }, [open, rows === null]);

    return h(
      'div',
      { className: 'sw-leadin sw-working-reads' },
      h(
        'div',
        { className: 'sw-leadin-head' },
        h('span', { className: 'sw-leadin-face' },
          SW.brand.text('{assistantName} read {count} table{plural} to answer this',
                        { count, plural: count === 1 ? '' : 's' })),
        h(
          Button,
          { type: 'link', size: 'small', onClick: () => setOpen(!open) },
          open ? 'Hide the steps' : 'Show the steps'
        )
      ),
      open && h(
        'div',
        { className: 'sw-leadin-turns' },
        reading && h('div', { className: 'sw-block-sub' }, 'Reading those tables…'),
        // Opened onto nothing, which is a dead end and needs a sentence rather than an empty box.
        // Every row behind this face can come back as no card at all: a `.table.json` that is
        // there but blank draws neither a card nor a link, inside this fold exactly as outside it.
        // The face still counted them, because `count` is what the turn WROTE.
        !reading && rows && rows.length === 0 && h(
          'div',
          { className: 'sw-block-sub' },
          count === 1
            ? "That table's file is empty, so there is nothing to show here."
            : "Those tables' files are empty, so there is nothing to show here."
        ),
        !reading && (rows || []).map((b, i) => h(SW.MessageBlock, { key: `wr_${i}`, block: b }))
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
    const [failed, setFailed] = useState(false);
    const href = block.src || (block.path
      ? `./api/project/file/raw?path=${encodeURIComponent(block.path)}` : '');
    // A chart that is not in this clone, in a Conversation reopened after a restart. The server
    // said so from a `stat` on the restore, so nothing here waits for an `<img>` to fail and draw
    // the broken image first (ADR-0045, #255). No link either: there is no file behind it.
    //
    // Two sentences, because `missing` is a `stat` and `notKept` is a reason. A chart can be gone
    // for reasons this decision has nothing to do with, and a card that blamed the setting for
    // those would contradict a switch the person just turned ON. Only the second names it.
    //
    // The date is the rest of what the card still knows, and it is what makes the loss readable:
    // a chart drawn this morning reads differently from one drawn in March.
    if (block.missing) {
      return h(
        'div',
        { className: 'sw-block-card' },
        block.title &&
          h('div', { className: 'sw-block-head' },
            h('div', { className: 'sw-block-title' }, block.title)),
        h(
          'div',
          { className: 'sw-block-body' },
          block.producedAt &&
            h('div', { className: 'sw-block-sub' }, `Drawn ${SW.util.relativeTime(block.producedAt)}`),
          h('div', { className: 'sw-block-sub' }, block.notKept
            ? SW.brand.text("This chart wasn't saved, so it didn't survive a restart. Ask for it "
                            + 'again.')
            : SW.brand.text("This chart isn't in this {project}'s files. Ask for it again to see "
                            + 'it.'))
        )
      );
    }
    // Same floor as TableBlock: a missing or unreadable file used to be a broken-image icon
    // sitting on the alt text, which reads as a rendering fault rather than a file that did
    // not arrive. Offer the file when there is one.
    if (failed) {
      return h(
        'div',
        { className: 'sw-block-card' },
        block.title &&
          h('div', { className: 'sw-block-head' },
            h('div', { className: 'sw-block-title' }, block.title)),
        h(
          'div',
          { className: 'sw-block-body' },
          h('div', { className: 'sw-block-sub' }, "This chart didn't load."),
          href &&
            h('a', { className: 'sw-block-sub', href }, 'Open the file')
        )
      );
    }
    return h(
      'div',
      { className: 'sw-block-card' },
      block.title &&
        h('div', { className: 'sw-block-head' }, h('div', { className: 'sw-block-title' }, block.title)),
      h(
        'div',
        { className: 'sw-block-body' },
        h('img', { src: block.src, alt: block.title || '',
                   style: { maxWidth: '100%', display: 'block' },
                   onError: () => setFailed(true) })
      )
    );
  }

  // One fold for the whole turn, a section inside it per operation the turn ran (#447). The card
  // was written to describe one operation and given one per card, so an answer that read a table,
  // computed on it and analysed text stacked three identical-looking dropdowns. Nothing is hidden
  // by the grouping: every paragraph a single operation used to get, it still gets.
  //
  // The count is in the summary because the fold is shut when the reader meets it, and "Data used"
  // alone cannot say whether it holds one operation or five.
  function DataUsed({ events }) {
    const operations = events || [];
    // Nothing rather than an empty fold. A "Data used" disclosure a reader opens to find no
    // source, no coverage and no Artifact reads as "this turn touched nothing" — which is a
    // claim, and one this card is the last surface that should make falsely. The shape that
    // reaches here always carries an operation; if that ever stops being true, draw no card
    // instead of an empty promise.
    if (!operations.length) return null;
    return h('details', { className: 'sw-data-used' },
      h('summary', null, operations.length > 1
        ? `Data used (${operations.length} operations)` : 'Data used'),
      ...operations.map((event) => {
        const coverage = event.coverage || {};
        const textOperation = event.operation === 'text_analysis';
        const documentOperation = event.operation === 'document_reference';
        const documentPrepared = documentOperation && (!event.status || event.status === 'prepared');
        const selectedPages = Array.isArray(coverage.selected_pages) ? coverage.selected_pages : [];
        const processedPages = Array.isArray(coverage.processed_pages) ? coverage.processed_pages : [];
        const pdfPageCoverage = documentPrepared && event.source_type === 'pdf'
          ? `PDF pages: ${coverage.source_pages || 0} source; ` +
            `${selectedPages.length} selected (${selectedPages.join(', ') || 'none'}); ` +
            `${processedPages.length} processed (${processedPages.join(', ') || 'none'}). `
          : '';
        const documentTextTruncated = Number(coverage.sent_characters || 0) <
          Number(coverage.selected_characters || 0);
        const documentFailure = ({
          withheld: 'Document content was withheld. No document text was prepared.',
          source_too_large: 'The document exceeded the source-size limit. No document text was prepared.',
          not_text: 'The file was not valid text. No document text was prepared.',
          heading_not_unique: 'The requested heading was missing or not unique. No document text was prepared.',
          heading_not_supported: 'This file type does not support heading selection. No document text was prepared.',
          selector_too_long: 'The requested heading exceeded the selector limit. No document text was prepared.',
          empty_document: 'The document contained no text to transfer.',
          unavailable: 'The document was unavailable. No document text was prepared.',
          malformed_document: 'The document was malformed or corrupt. No document text was prepared.',
          encrypted_document: 'The document was encrypted. No document text was prepared.',
          document_xml_too_large: 'The Word document XML exceeded the extraction limit. No document text was prepared.',
          no_extractable_text: 'The PDF had no extractable text. No document text was prepared.',
          invalid_page_selection: 'The PDF page selection was invalid. No document text was prepared.',
          too_many_pages: 'The PDF selection exceeded the 20-page limit. No document text was prepared.',
          page_out_of_range: 'The PDF page selection was outside the document. No document text was prepared.',
          page_selection_not_supported: 'This document type does not support page selection. No document text was prepared.',
          extraction_unavailable: 'PDF text extraction was unavailable. No document text was prepared.',
        })[event.status] || 'Document preparation failed. No document text was prepared.';
        const source = String(event.source || 'unknown source');
        return h('div', { key: event.operation_id, className: 'sw-data-used-op' },
          h('p', null, documentPrepared ? 'Prepared through the LLM Gateway from '
            : documentOperation ? 'Document preparation did not transfer content from '
            : textOperation ? 'Analyzed through the LLM Gateway from ' : 'Calculated in Domino from ',
            h(Tag, { 'aria-label': `Source file: ${source}` }, source.split('/').pop()), '.'),
          documentPrepared
            ? h('p', null,
              `${coverage.sent_characters || 0} of ${coverage.selected_characters || 0} characters prepared. `,
              pdfPageCoverage || (processedPages.length
                ? `Pages: ${processedPages.join(', ')}. `
                : event.selected_selector ? `Heading: ${event.selected_selector}. ` : 'Whole document. '),
              documentTextTruncated
                ? 'The selected text was truncated. ' : 'The selected text was complete. ',
              coverage.pages_truncated
                ? 'Page coverage was capped.' : '')
            : documentOperation
              ? h('p', null, documentFailure)
            : h('p', null, `${coverage.processed} of ${coverage.total} rows processed. ` +
              `${coverage.excluded} excluded; ${coverage.failed} failed; ${coverage.unfinished} unfinished.`),
          !documentOperation && h('p', null,
            `Selected fields: ${(event.selected_fields || []).join(', ') || 'Structure only'}.`),
          event.artifact && h('p', null, 'Artifact: ',
            h(Tag, { 'aria-label': `Artifact: ${event.artifact}` }, event.artifact.split('/').pop())),
          ...(event.requests || []).map((request) => h('div', { key: request.request_id },
            h('p', null, 'Requested model: ',
              h(Tag, { 'aria-label': `Requested model: ${request.requested_alias}` }, request.requested_alias),
              '. Serving model: ', request.serving_model || 'unknown', '.'),
            h('p', null, request.state === 'response_completed' ? 'Gateway response completed. '
              : request.state === 'failed' ? 'Gateway request failed. '
              : request.state === 'interrupted' ? 'Gateway response interrupted. '
              : 'Gateway request attempted. ',
              `Provider receipt: ${request.provider_receipt || 'unknown'}. ` +
              `Decision stage: ${request.decision_stage || 'unknown'}. ` +
              `Delivery: ${request.delivery || 'unknown'}. ` +
              `Cache: ${request.cache || 'unknown'}. ` +
              `Fallback: ${request.fallback || 'unknown'}.`),
            request.failure && h('p', null, `Failure: ${request.failure}.`,
              request.refusal_reason ? ` ${request.refusal_reason}.` : ''),
            h('p', null, `Request: ${request.request_id}`))),
          !(event.requests || []).length && h('p', null, 'Gateway delivery: unknown.'),
          h('p', null, `Operation: ${event.operation_id}`));
      }));
  }

  SW.MessageBlock = function MessageBlock({ block, onSave }) {
    switch (block.type) {
      case 'text':
        // A caret while the text is still arriving. Without it a model that pauses mid-sentence
        // looks like a model that finished a short answer, and the reader gives up on it.
        return h('div', { className: `sw-msg-text${block.streaming ? ' is-streaming' : ''}` },
                 SW.util.markdown(block.value));
      case 'data_used':
        return h(DataUsed, { events: block.events });
      case 'code':
        return h(CodeBlock, { code: block.value, language: block.language });
      case 'sandbox_run':
        return h(SandboxRun, { block });
      case 'chart':
        return h(ChartBlock, {
          chartId: block.chartId,
          onAddToPlan: SW.store.get().activePlanId
            ? () => antd.message.success('Added to the plan')
            : null,
        });
      case 'image':
        return h(ImageBlock, { block });
      case 'file':
        return h(FileCard, { block });
      case 'page':
        return h(PageBlock, { block });
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
      case 'lead_in_fold':
        return h(LeadInFold, { block });
      case 'working_reads_fold':
        return h(WorkingReadsFold, { block });
      case 'plan_card':
        return h(SW.PlanCard, { planId: block.planId });
      case 'build_plan':
        return h(BuildPlanCard, { block });
      case 'status':
        return h(
          'div',
          {
            className: `sw-status-line${block.ok === false ? ' is-err' : block.warn ? ' is-warn' : ''}`,
          },
          block.value
        );
      case 'mentions_unresolved':
        return h(MentionsUnresolved, { block });
      case 'reset_offer':
        return h(ResetOffer, { block });
      case 'incoming_changes':
        return h(IncomingChanges, { block });
      case 'source_candidates':
        return h(SourceCandidates, { block });
      case 'table_candidates':
        return h(TableCandidates, { block });
      case 'dataset_files':
        return h(DatasetFiles, { block });
      case 'investigation_offer':
        return h(InvestigationOffer, { block });
      case 'other_lane_offer':
        return h(OtherLaneOffer, { block });
      case 'continue_offer':
        return h(ContinueAfterTheCeiling, { block });
      case 'build_stalled':
        return h(BuildStalled, { block });
      case 'plan_suggestion':
        return h(PlanSuggestion, { block });
      case 'withhold':
        return h(WithholdCard, { block });
      case 'recall_offer':
        return h(RecallOffer, { block });
      case 'recall_cleared':
        return h(RecallCleared, { block });
      case 'recall_withheld':
        return h(RecallWithheld, { block });
      case 'recall_rebuilt':
        return h(RecallRebuilt);
      case 'graduation_nudge':
        return h(GraduationNudge, { onSave });
      default:
        return null;
    }
  };

  // The message as markdown: prose runs as-is, code stays fenced, a table becomes a real
  // markdown table since its rows/columns are already structured data. Image/file blocks resolve
  // to a same-origin, session-gated URL (`./api/project/file/raw?path=...`), so they get named
  // rather than linked — that URL pasted into Slack or a doc would be dead there anyway.
  function copyTextFor(message) {
    return message.blocks
      .map((b) => {
        if (b.type === 'text') return b.value;
        if (b.type === 'code') return `\`\`\`${b.language || ''}\n${b.value}\n\`\`\``;
        if (b.type === 'table') {
          const cell = (v) => String(v ?? '').replace(/\|/g, '\\|');
          // The same sentences the card shows. Pasting "(no rows)" for a table whose rows the
          // Project declined to keep would report an empty read, which is not what happened.
          if (isTableReceipt(b)) {
            return [b.title, ...tableReceiptLines(b)].filter(Boolean).join('\n');
          }
          // The card says this in words; a pasted `|  |` over `|  |` says it in a syntax that
          // renders as an empty table wherever it lands, which is how this reached a bug report.
          if (!b.columns.length && !b.rows.length) {
            return [b.title, '(no rows)'].filter(Boolean).join('\n');
          }
          const { names, width } = tablePaintShape(b);
          const headerCells = Array.from({ length: width }, (_, i) => names[i] || '');
          const header = `| ${headerCells.map(cell).join(' | ')} |`;
          const rule = `| ${headerCells.map(() => '---').join(' | ')} |`;
          const rows = b.rows.map((row) => (
            `| ${Array.from({ length: width }, (_, i) => cell((row || [])[i])).join(' | ')} |`
          ));
          return [b.title, header, rule, ...rows].filter(Boolean).join('\n');
        }
        if (b.type === 'image') return `[Image: ${b.title || 'untitled'}]`;
        if (b.type === 'file') return `[File: ${b.name || b.path || 'untitled'}]`;
        if (b.type === 'page') return `[Page: ${b.title || b.path || 'untitled'}]`;
        return null;
      })
      .filter((v) => v !== null)
      .join('\n\n');
  }

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

  // The prompt that led to this answer: the nearest user message walking back, stopping at
  // another assistant message rather than assuming index - 1, since a system notice can sit
  // between a question and its answer. Its own attachments ride along so the resend reproduces
  // the original turn, not a text-only echo of it.
  function retryTargetFor(message) {
    const { messages } = SW.store.get();
    const idx = messages.findIndex((m) => m.id === message.id);
    for (let i = idx - 1; i >= 0; i -= 1) {
      const m = messages[i];
      if (m.role === 'assistant') return null;
      if (m.role === 'user') {
        const text = (m.blocks || [])
          .filter((b) => b.type === 'text')
          .map((b) => b.value)
          .join('\n\n')
          .trim();
        return text ? { text, attachments: m.attachments || [] } : null;
      }
    }
    return null;
  }

  SW.Message = function Message({ message, onSave }) {
    const { me } = SW.store.get();
    const isUser = message.role === 'user';
    const isSystem = message.role === 'system';
    const pinTarget = isUser ? null : pinTargetFor(message);
    const retryTarget = isUser ? null : retryTargetFor(message);

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
          // Disclosure this viewer has put away stays ON the message, marked by the store, and is
          // skipped here (#448). Marked and not removed because other code both deletes blocks
          // from this array and caches positions into it — the store's comment on `pushBlock` has
          // the two defects that taught us. `build_plan`'s `folded` is the same shape: the store
          // decides, the component is handed the answer, and the preference is not read here.
          //
          // Keyed on the ORIGINAL index, so revealing a block does not renumber its neighbours and
          // make React rebuild the answer around it.
          message.blocks
            .map((block, i) => [block, i])
            .filter(([block]) => !block.hiddenDisclosure)
            .map(([block, i]) => h(SW.MessageBlock, { key: i, block, onSave }))
        ),

        // The way in to disclosure this viewer has put away (#448, ADR-0062). Drawn from what the
        // store stamped, never from the preference: `test_only_the_store_branches_on_the_preference`
        // is the house rule, and the stamp only exists when something was actually withheld — so
        // this branch cannot read the preference by accident, and it cannot draw an offer to reveal
        // nothing.
        //
        // The count is on the label because one withheld card and three are different offers, and
        // a person deciding whether to look wants to know which they are getting.
        !isUser && message.disclosureHidden > 0 &&
          h(
            'div',
            { className: 'sw-msg-disclosure' },
            h(
              Button,
              { type: 'link', size: 'small', onClick: () => SW.store.setDataAccessShown(true) },
              message.disclosureHidden === 1
                ? 'Show data access'
                : `Show data access (${message.disclosureHidden})`
            )
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
                onClick: () => SW.util.copy(copyTextFor(message)),
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
            retryTarget &&
              h(
                Tooltip,
                { title: 'Retry' },
                h(Button, {
                  type: 'text',
                  size: 'small',
                  icon: h(ReloadOutlined, null),
                  'aria-label': 'Retry',
                  onClick: () =>
                    SW.store.sendMessage(retryTarget.text, { attachments: retryTarget.attachments }),
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
