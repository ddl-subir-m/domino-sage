window.SW = window.SW || {};

(function () {
  const { createElement: h, useState, useEffect, useMemo } = React;
  const { Drawer, Button, Skeleton } = antd;

  // Every build of the Built App the Build header names (#88).
  //
  // WHAT IT IS NOT. It is not this Conversation's turns. #56 already made the merged transcript
  // the Conversation reading, and Build draws it in the pane behind this one. This is the other
  // question — what has been built into THIS app, by whoever asked, in whichever Conversation —
  // so nothing here is filtered by conversation and nothing here says "your conversation".
  //
  // WHY A DRAWER AND NOT A TAB. A build history is read AGAINST the app it built, and a tab makes
  // the two exclusive — the reason #86 gave for refusing a Plan tab, unchanged. So it overlays:
  // the preview stays where it is, the drawer closes, and nothing had to be given up to read it.
  // Mask, close button and Escape are all named rather than left to antd's defaults, because
  // "there is a way out" is the criterion and a default is not a claim.
  SW.BuildHistoryDrawer = function BuildHistoryDrawer() {
    const { buildHistoryOpen, appHistory, activeApp } = SW.store.get();
    const appId = (activeApp && activeApp.id) || null;
    // Three states, because a read that failed is not an app with no builds (see `appHistory` in
    // the store). Reading them apart here is what stops the empty state claiming the second one
    // whenever the first one happened.
    const loading = appHistory === null;
    const failed = !!(appHistory && appHistory.failed);

    // The one read. Open with nothing for the app on screen is the whole condition, and it
    // describes both moments that need a read: opening (which clears the list on the way in), and
    // the selection moving underneath an open drawer — which needs nobody to click, because a
    // second tab choosing another app moves it here too (#95). The gate drops the old app's list
    // when that happens, so `null` is the signal and the app id is what makes it fire twice.
    useEffect(() => {
      if (buildHistoryOpen && loading) SW.store.readAppHistory();
    }, [buildHistoryOpen, loading, appId]);

    // Newest first. A transcript is read forwards because it is a conversation; a history is
    // opened to see what happened last.
    //
    // Runs only. `SW.buildRuns` also hands back the rows that belong to no run — a confirmed
    // handoff's plan card, an `app-reset` — because the transcript it was built for draws them in
    // place. A LIST OF BUILDS has nothing to list them as, and one entry per build is the shape
    // this surface promises.
    //
    // Held across renders, because grouping walks the WHOLE log and `app.js` re-renders the entire
    // tree on every `notify()` — which the 2s build tick fires. Re-parsing megabytes on each of
    // those would give back exactly what reading on demand was meant to save.
    const runs = useMemo(() => {
      const summaries = (appHistory && appHistory.diagnostics) || [];
      const byTurn = new Map(summaries.map((record) => [record.turn.turnId, record]));
      const used = new Set();
      const transcriptMessages = SW.buildRuns((appHistory && appHistory.rows) || []);
      const transcript = transcriptMessages
        .map((message, index) => {
          const block = (message.blocks || []).find((b) => b.type === 'build_run');
          if (!block) return null;
          const turnId = block.diagnostics && block.diagnostics.turnId;
          const record = turnId && byTurn.get(turnId);
          if (record) used.add(turnId);
          return {
            id: message.id,
            at: sortTime((record && record.turn.startedAt) || block.at,
              index - transcriptMessages.length),
            block: { ...block, diagnosticTurns: record ? [record]
              : (block.diagnostics ? [{ turn: block.diagnostics, identityOnly: true }] : []) },
          };
        })
        .filter(Boolean);
      // A stopped turn can be absent from the transcript because Stop rolls that private content
      // back. The diagnostic store is the lifecycle record, so it supplies a metadata-only row.
      const retained = summaries
        .filter((record) => !used.has(record.turn.turnId))
        .map((record) => ({
          id: `diagnostic_${record.turn.turnId}`,
          at: sortTime(record.turn.startedAt, 0),
          block: {
            prompt: `${phaseLabel(record)} turn`,
            at: record.turn.startedAt,
            messages: [],
            diagnosticTurns: [record],
          },
        }));
      return transcript.concat(retained).sort((a, b) => Number(b.at || 0) - Number(a.at || 0));
    }, [appHistory]);

    return h(
      Drawer,
      {
        open: !!buildHistoryOpen,
        onClose: () => SW.store.closeBuildHistory(),
        // Every way out, said out loud: the backdrop, the X and the Escape key.
        mask: true,
        maskClosable: true,
        closable: true,
        keyboard: true,
        width: 460,
        placement: 'right',
        // The app is named in the title rather than left to the header behind the mask, which is
        // the one thing a reader cannot check while this is open.
        title: activeApp ? `Build history · ${activeApp.name}` : 'Build history',
      },
      loading && h(Skeleton, { active: true, paragraph: { rows: 6 } }),
      // The read failed, said as a fact about the read. Its own state rather than the empty one
      // below: "no builds of this app yet" is a claim about the app, and made after a 500 it is
      // simply wrong — it tells somebody with a month of builds that they have none. The way back
      // is a button here rather than "reload the page", on #90's precedent: the person has just
      // been told the thing they asked for did not arrive, and sending them hunting for the fix is
      // what makes it a dead end.
      failed &&
        h(
          'div',
          { className: 'sw-bh-empty' },
          h('div', { className: 'sw-empty-title' }, "Couldn't read this app's build log"),
          h(
            'div',
            { className: 'sw-empty-detail' },
            'The log is on the workspace volume and nothing is lost — this is the read, not your '
              + 'builds.'
          ),
          h(
            Button,
            {
              size: 'small',
              type: 'primary',
              style: { marginTop: 12 },
              onClick: () => SW.store.readAppHistory(),
            },
            'Try again'
          )
        ),
      !loading && !failed
        && h(
            'div',
            { className: 'sw-bh-list' },
            h(
              'div',
              { className: 'sw-bh-intro' },
              // Why there can be rows in here this conversation never asked for (#72). Without it
              // the extra rows read as a bug.
              'Includes builds asked for in other conversations.'
            ),
            (appHistory.historyFailed || appHistory.diagnosticsFailed) && h(
              'div', { role: 'alert', className: 'sw-bh-read-warning' },
              appHistory.diagnosticsFailed
                ? 'Diagnostic records could not be read. Transcript history is still shown.'
                : 'Transcript history could not be read. Retained diagnostic records are still shown.'
            ),
            runs.length === 0
              ? h(
                  'div',
                  { className: 'sw-bh-empty' },
                  h(
                    'div',
                    { className: 'sw-empty-title' },
                    activeApp ? `No builds of ${activeApp.name} yet` : 'No builds yet'
                  ),
                  h(
                    'div',
                    { className: 'sw-empty-detail' },
                    'Describe a change in the composer, or approve a plan.'
                  )
                )
              : runs.map((row) => h(BuildRunRow, { key: row.id, block: row.block }))
          )
    );
  };

  function sortTime(value, fallback) {
    const numeric = Number(value);
    if (Number.isFinite(numeric) && numeric > 0) return numeric;
    const parsed = Date.parse(value);
    return Number.isFinite(parsed) ? parsed / 1000 : fallback;
  }

  function phaseLabel(record) {
    const phase = record && record.turn && record.turn.phase;
    return phase === 'implementation' ? 'Implementation' : phase === 'planning' ? 'Planning' : 'Build';
  }

  function outcomeLabel(record) {
    if (record && record.identityOnly) return 'Status unavailable';
    if (record && record.capture && record.capture.status === 'running') return 'Running';
    if (record && record.capture && record.capture.status === 'interrupted') return 'Interrupted';
    const status = record && record.buildOutcome && record.buildOutcome.status;
    return {
      repeat_brake: 'Stopped — repeated calls',
      user_stop: 'Stopped by user',
      gateway_refusal: 'Gateway refused',
      error: 'Failed',
      success: 'Succeeded',
    }[status] || 'Interrupted';
  }

  // One build. Headed by the prompt that started it, because that is what a person remembers
  // asking for — the turns underneath are how it was answered, and they stay folded until asked
  // for. A run whose turns all folded away has nothing to open, so it offers nothing.
  function BuildRunRow({ block }) {
    const [open, setOpen] = useState(false);
    const [downloading, setDownloading] = useState('');
    const [downloadError, setDownloadError] = useState('');
    const download = async (record) => {
      const target = record.turn || record;
      setDownloading(target.turnId);
      setDownloadError('');
      try { await SW.api.downloadBuildDiagnostics(target); }
      catch (error) { setDownloadError(error.message || "Couldn't download diagnostics."); }
      finally { setDownloading(''); }
    };
    const turns = block.messages || [];
    const diagnosticTurns = block.diagnosticTurns || [];

    return h(
      'div',
      { className: 'sw-bh-run' },
      h(
        'div',
        { className: 'sw-bh-run-head' },
        h(
          'div',
          {
            className: 'sw-bh-run-prompt',
            // The heading is clamped to two lines, and the prompt is what tells one build from
            // the next — so the whole of it is on hover rather than lost to the ellipsis.
            title: block.prompt || '',
          },
          block.prompt || 'This build recorded no prompt'
        ),
        // A row written before Sage stamped the clock has no time, and no time is what it shows.
        // Deriving one from its neighbours would be a number nobody wrote down.
        block.at && h('div', { className: 'sw-bh-run-at' }, SW.util.relativeTime(block.at))
      ),
      diagnosticTurns.length === 0 && h(Button, { size: 'small', disabled: true,
        title: 'Diagnostics were not captured for this older turn.' }, 'Download diagnostics'),
      diagnosticTurns.map((record) => {
        const target = record.turn || record;
        return h('div', { className: 'sw-bh-diagnostic', key: target.turnId },
          h('div', { className: 'sw-bh-diagnostic-label' },
            `${phaseLabel(record)} · ${outcomeLabel(record)}`),
          h(Button, { size: 'small', loading: downloading === target.turnId,
            onClick: () => download(record) }, record.identityOnly ? 'Download diagnostics'
              : `Download ${phaseLabel(record).toLowerCase()} diagnostics`));
      }),
      downloadError && h('div', { role: 'alert' }, downloadError),
      // The run's `app_change` cards are deliberately NOT drawn. Every row in this log is this
      // app's — the file is the app's (ADR-0008) — so a card per row would name the app the title
      // already names, on every row, and say nothing.
      turns.length > 0 &&
        h(
          Button,
          { type: 'link', size: 'small', onClick: () => setOpen(!open) },
          open ? 'Hide the turns' : `Show the ${turns.length} turn${turns.length === 1 ? '' : 's'}`
        ),
      open &&
        h(
          'div',
          { className: 'sw-bh-run-turns' },
          turns.map((message) => h(SW.Message, { key: message.id, message }))
        )
    );
  }
})();
