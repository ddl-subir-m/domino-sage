window.SW = window.SW || {};

(function () {
  const { createElement: h } = React;
  const { Drawer } = antd;

  // The two owners a Problem sorts under, in the order they are read (ADR-0027). The creator's own
  // first, because a reader opening this wants to know which of these they can do something about
  // before they read a word of the rest — and because a Problem with two owners in truth, such as a
  // dead model slot, is filed here rather than under the administrator for exactly that reason.
  //
  // The heading is the client's own furniture rather than a sentence about the deployment, which is
  // why it is written here while every Problem's own words come composed off the wire. It names who
  // holds the remedy, not who is at fault: four of the six are nobody the reader can chase, and the
  // creator still has to know, because those failures land on their build.
  const GROUPS = [
    { owner: 'you', title: 'Yours to fix' },
    { owner: 'admin', title: 'Your administrator’s to fix' },
  ];

  SW.ProblemsDrawer = function ProblemsDrawer() {
    const { problemsOpen, problems } = SW.store.get();
    const found = Array.isArray(problems) ? problems : [];

    // A group with nothing in it is not drawn. "Yours to fix — none" is a heading that says the
    // reader has something to read, and a drawer opened off a lit chip has to be all signal.
    const group = (spec) => {
      const mine = found.filter((p) => p && p.owner === spec.owner);
      if (!mine.length) return null;
      return h(
        'section',
        { key: spec.owner, className: 'sw-problems-group' },
        h('h3', { className: 'sw-problems-group-title' }, spec.title),
        mine.map((p) =>
          h(
            'div',
            { key: p.id, className: 'sw-problem' },
            // Sage's two sentences and the platform's own words, through the one block every
            // surface that draws a passed-through body uses (ADR-0014). `message` is the fault and
            // `fix` the remedy, both composed server-side and neither rewritten here; `body` is
            // quoted, and absent when the platform said nothing.
            h(SW.PlatformError, { reason: p.message, fix: p.fix, body: p.body })
          )
        )
      );
    };

    return h(
      Drawer,
      {
        title: 'Problems',
        placement: 'right',
        width: 460,
        open: Boolean(problemsOpen),
        onClose: () => SW.store.openProblems(false),
      },
      // The drawer can be open over a list that has just emptied, because a Problem goes when it
      // stops being true rather than when anybody acknowledges it. Saying so beats an empty panel
      // that reads as a failed read.
      found.length
        ? GROUPS.map(group)
        : h('p', { className: 'sw-problems-empty' }, 'Nothing needs your attention right now.')
    );
  };
})();
