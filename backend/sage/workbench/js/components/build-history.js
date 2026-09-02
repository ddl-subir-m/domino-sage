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
    const runs = useMemo(
      () =>
        SW.buildRuns((appHistory && appHistory.rows) || [])
          .map((message) => ({
            id: message.id,
            block: (message.blocks || []).find((b) => b.type === 'build_run'),
          }))
          .filter((row) => row.block)
          .reverse(),
      [appHistory]
    );

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
          h('div', { className: 'sw-empty-title' }, 'Couldn’t read this app’s build log'),
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

  // One build. Headed by the prompt that started it, because that is what a person remembers
  // asking for — the turns underneath are how it was answered, and they stay folded until asked
  // for. A run whose turns all folded away has nothing to open, so it offers nothing.
  function BuildRunRow({ block }) {
    const [open, setOpen] = useState(false);
    const turns = block.messages || [];

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
