window.SW = window.SW || {};

(function () {
  const { createElement: h, useState, useEffect } = React;
  const { Modal, Input, Button, Tooltip, Tag, Skeleton, Empty } = antd;
  const { SearchOutlined, PlusOutlined, CheckOutlined } = icons;

  // Browsing a platform catalogue needs room for the facts you actually choose
  // on — who owns it, how fresh, who else uses it. That is
  // why this is a surface you open rather than a 300px column you live beside.
  const KINDS = [
    { key: null, label: 'Everything' },
    // One entry for everything data-like, with the two shapes under it (ADR-0054). It is also where
    // a group's add door lands: without this entry the door opened on Everything, because a single
    // kind would have meant Datasets and hidden Data Sources behind a filter nobody chose (#164).
    //
    // The two stay listed, because a shape is a filter somebody wants — but as CHILDREN. As peers
    // they were three rows whose first count was the sum of the next two, which reads as a third
    // kind of thing rather than as the pair above its halves.
    { key: 'data', label: 'Data', kinds: ['dataset', 'datasource'] },
    { key: 'dataset', under: 'data' },
    { key: 'datasource', under: 'data' },
    { key: 'model_llm', label: 'Language models' },
    { key: 'model_predictive', label: 'Predictive models' },
    { key: 'agent', label: 'Agents' },
    { key: 'skill', label: 'Skills' },
    { key: 'mcp', label: 'MCPs' },
  ];

  // Whether the count for one kind is a thing Domino said. `catalog` fills every count key
  // unconditionally, so a kind whose read refused reports `0` — and more often than that
  // `keepUnreadKinds` carries the PREVIOUS rows over the refusal, so it reports a stale non-zero
  // instead. Neither is an answer. So the trigger here is the error key and never the value: a
  // rule written against the `0` would leave the commoner case unmarked (#368).
  function countState(key, counts, errors) {
    // The join between the two spellings already exists and is already public — `errors` is keyed
    // `data_sources` where `counts` is keyed `datasource`. It covers four of the seven count keys:
    // `agent`, `skill` and `mcp` report no error of their own, so they have no refusal to draw and
    // this is the whole of the state they can be in.
    const failed = SW.api.LISTING_ERROR_KEY[key];
    if (!failed) return counts[key] === undefined ? 'absent' : 'read';
    // A fault in the read itself is answered with a listing that has no kind key at all, so the
    // per-kind branch below cannot reach one: without this, the same defect at full width, with
    // every kind that could have refused reading `0` on its own authority.
    if (errors.listing || errors[failed]) return 'unread';
    return counts[key] === undefined ? 'absent' : 'read';
  }

  // What the number beside a sidebar entry says, and whether it is a number at all. `catalog`
  // counts kinds, so a group entry adds its own up rather than asking for a count of a kind that
  // does not exist — and it adds up only the kinds that answered. That leaves the parent honest
  // without leaving the child lying, because the child that refused is drawing a `—` directly
  // under the sum, which is what explains it.
  function sideState(entry, counts, errors) {
    const kinds = entry.kinds || [entry.key];
    const read = kinds.filter((k) => countState(k, counts, errors) === 'read');
    if (read.length) return { state: 'read', count: read.reduce((n, k) => n + counts[k], 0) };
    // Nothing answered, which is two different facts: every kind under this entry refused, which
    // is worth saying, or the platform does not offer the kind, which is the silence this slot has
    // always kept. Collapsing them is how a person gets one explanation over the other's row.
    if (kinds.some((k) => countState(k, counts, errors) === 'unread')) return { state: 'unread' };
    return { state: 'absent' };
  }

  // The reason to hang under the `—`: the same strings the note above the rows joins, cut down to
  // the legs this one entry stands for.
  function sideError(entry, errors) {
    if (errors.listing) return errors.listing;
    const kinds = entry.kinds || [entry.key];
    return kinds.map((k) => errors[SW.api.LISTING_ERROR_KEY[k]]).filter(Boolean).join(' ');
  }

  // What a child row says it is a shape of. `KINDS` stays one flat array carrying `under`, so the
  // parent's own label is the single place that word is written.
  function parentLabel(entry) {
    const parent = KINDS.find((e) => e.key === entry.under);
    return SW.brand.text(parent.label || SW.util.dataTypeLabel(parent.key));
  }

  // The count in words. `—` is a mark rather than a word, and a label is handed to a screen reader
  // INSTEAD of the button's contents — so a state left unspelled here is a row with no count at
  // all for the people the label exists for (#369).
  function countWords(drawn) {
    if (drawn.state === 'read') return String(drawn.count);
    return drawn.state === 'unread' ? 'not read' : '';
  }

  // What a screen reader is handed instead of a button's contents, on the two rows that need one.
  //
  // A child says what it is a shape OF, because the indent that says it to the eye is the whole of
  // the nesting and an indent is not read out (#369).
  //
  // ANY row says an unread count in words. `—` reaches a reader as nothing wherever it is drawn,
  // and three of the four kinds that can refuse have no parent — so leaving those to their
  // contents would have them announce LESS after #368 than the wrong `0` announced before it.
  //
  // A row that is neither gets no label at all: its own contents already read correctly, and a
  // second copy of a word is one more thing to keep in step with the visible one.
  function ariaLabel(entry, label, drawn) {
    const words = countWords(drawn);
    if (entry.under) return [label, `in ${parentLabel(entry)}`, words].filter(Boolean).join(', ');
    return drawn.state === 'unread' ? `${label}, ${words}` : undefined;
  }

  function CatalogRow({ resource, scope, onAdd, onOpen, busy }) {
    return h(
      'div',
      { className: `sw-cat-row${resource.inProject ? ' is-in' : ''}` },
      h(
        'button',
        { className: 'sw-cat-open', onClick: () => onOpen(resource) },
        h('span', { className: 'sw-cat-icon' }, SW.util.iconNodeFor(resource.kind)),
        h(
          'span',
          { className: 'sw-cat-main' },
          h(
            'span',
            { className: 'sw-cat-name-line' },
            h('span', { className: 'sw-cat-name' }, resource.name),
            resource.sovereign &&
              h(
                Tooltip,
                { title: SW.util.SOVEREIGN_TITLE },
                h(Tag, { bordered: false, className: 'sw-sens sw-sens-internal' }, 'sovereign')
              ),
            // Read before the Add, on the row the Add is on. This is the earliest moment the
            // declaration can be shown, and the lock it leads to is easiest to accept from here
            // (ADR-0043).
            resource.declared &&
              h(
                Tooltip,
                { title: SW.util.declaredTitle() },
                h(
                  Tag,
                  { bordered: false, className: 'sw-sens sw-sens-confidential' },
                  SW.util.DECLARED_MARK
                )
              )
          ),
          h('span', { className: 'sw-cat-desc' }, resource.description),
          h(
            'span',
            { className: 'sw-cat-meta' },
            // What this row is, in the words the sidebar filters by. Every other kind keeps the
            // Domino noun, which is all `labelFor` was ever answering here.
            h('span', null,
              SW.util.dataTypeLabel(resource.kind) || SW.util.labelFor(resource.kind)),
            // `originName` and `ownerName` were dropped from this line (ADR-0054). A Dataset's
            // origin is its Project, which `description` above already says as `in <project>`, so
            // the row printed one project name twice; a Data Source has no Project and fell back to
            // the platform's own name, which is true of every row in a catalogue of that platform.
            // `ownerName` has been the empty string since this modal was written, so all it ever
            // drew was a separator with nothing after it.
            resource.freshness && h('span', { className: 'sw-cat-dot' }, '·'),
            resource.freshness && h('span', null, resource.freshness),
            resource.usedInProjects > 0 && h('span', { className: 'sw-cat-dot' }, '·'),
            resource.usedInProjects > 0 &&
              h(
                'span',
                null,
                `in ${resource.usedInProjects} ${resource.usedInProjects === 1 ? 'project' : 'projects'}`
              )
          )
        )
      ),
      resource.inProject
        ? h(
            Tooltip,
            { title: `Already in ${scope.name}` },
            h(
              'span',
              { className: 'sw-cat-in' },
              h(CheckOutlined, { style: { fontSize: 10 } }),
              'In project'
            )
          )
        : h(
            Button,
            {
              size: 'small',
              type: 'primary',
              loading: busy,
              icon: h(PlusOutlined, { style: { fontSize: 10 } }),
              onClick: () => onAdd(resource),
            },
            'Add'
          )
    );
  }

  SW.ResourceCatalog = function ResourceCatalog() {
    const { catalogOpen, catalogKind, scope } = SW.store.get();
    const [query, setQuery] = useState('');
    const [kind, setKind] = useState(null);
    const [busyId, setBusyId] = useState(null);
    const [drill, setDrill] = useState(null);

    // Sage can send the user here asking for a specific kind of thing, so the
    // opening state answers that question rather than showing everything.
    useEffect(() => {
      if (catalogOpen) {
        setKind(catalogKind || null);
        setQuery('');
        setDrill(null);
      }
    }, [catalogOpen, catalogKind]);

    // Once per open, and never per keystroke. The rows below are already on screen off the store
    // while this is in flight, so this is the platform correcting a listing rather than the wait
    // before anything can be read.
    useEffect(() => {
      if (!catalogOpen) return;
      SW.store.refreshResourceListing();
    }, [catalogOpen, scope.id]);

    if (!catalogOpen) return null;

    // A view of what the store holds, recomputed on every draw. Typing and picking a kind are now
    // filters over memory, so neither reaches the network (#159).
    // A sidebar entry standing for a group asks for every kind under it; every other one asks for
    // itself.
    const picked = KINDS.find((e) => e.key === kind);
    const view = SW.api.catalog({ q: query, kind: (picked && picked.kinds) || kind || '' });
    // `null` is the window right after a project switch, when the store's listing has been cleared
    // and the fresh one has not landed. That is a spinner, never an empty catalogue: an empty one
    // reads as "Domino holds nothing you can add", which is the one thing that is not true here.
    const loading = !view;
    const rows = (view && view.results) || [];
    const counts = (view && view.counts) || {};
    // What `fetchDominoListing` could not read. Partial is the normal case — Datasets answered and
    // Data Sources did not — so this has to be sayable beside rows, not only instead of them. An
    // outage silently drawn as an empty catalogue tells somebody their platform is bare.
    const errors = (view && view.errors) || {};
    const listingErrors = Object.values(errors).filter(Boolean).join(' ');
    // A search box with something in it, or a kind picked in the sidebar. It decides who gets to
    // say why the list is empty: with a filter standing, the answer is the filter, and a refusal
    // that has nothing to do with what was typed must not take that sentence over.
    const narrowed = Boolean(query.trim()) || Boolean(kind);

    const add = async (resource) => {
      setBusyId(resource.id);
      try {
        // No local patch of the row: `inProject` is read off the working set the store just
        // reloaded, so the row and the rail cannot disagree about what happened.
        await SW.store.addToProject(resource);
      } finally {
        setBusyId(null);
      }
    };

    const openRow = (resource) => {
      if (resource.kind === 'dataset' || resource.kind === 'datasource') {
        setQuery('');
        setDrill(resource);
        return;
      }
      SW.store.previewResource(resource.id);
    };

    const inCount = rows.filter((r) => r.inProject).length;
    const { resourceGroups } = SW.store.get();
    const member = drill && ((resourceGroups[drill.kind] || []).find((r) => r.id === drill.id) || null);
    const drillResource = drill && {
      ...drill,
      ...(member || {}),
      pins: (member && member.pins) || [],
    };

    return h(
      Modal,
      {
        open: true,
        onCancel: () => SW.store.closeCatalog(),
        footer: null,
        width: 900,
        title: `Add to ${scope.name}`,
        className: 'sw-cat-modal',
        styles: { body: { padding: 0 } },
        destroyOnClose: true,
      },
      h(
        'div',
        { className: 'sw-cat' },
        h(
          'nav',
          { className: 'sw-cat-side' },
          h('div', { className: 'sw-group-label sw-cat-side-head' }, 'Browse'),
          KINDS.map((entry) => {
            // A shape's own label comes from the shared rule the rail's subheads read, so the
            // two surfaces cannot end up calling one shape two things.
            const label = SW.brand.text(entry.label || SW.util.dataTypeLabel(entry.key));
            const drawn = entry.key ? sideState(entry, counts, errors) : { state: 'absent' };
            return h(
              'button',
              {
                key: entry.key || 'all',
                className: `sw-cat-side-btn${kind === entry.key ? ' is-active' : ''}`
                  + (entry.under ? ' is-child' : ''),
                // The indent is the whole of the nesting, and an indent is not read out. This says
                // the one thing it stands for and stops. Children only: a label REPLACES a
                // button's contents for the readers it exists for, so one over a row that already
                // reads correctly can only make it wrong, and is a second copy of a word to keep
                // in step with the visible one (#369).
                'aria-label': ariaLabel(entry, label, drawn),
                onClick: () => {
                  setKind(entry.key);
                  setDrill(null);
                },
              },
              h('span', null, label),
              drawn.state === 'read' &&
                h('span', { className: 'sw-cat-side-count' }, drawn.count),
              // Said where the eye lands, rather than only in the note above the rows: the number
              // beside the filter is the part a person reads and the part that was unmarked.
              drawn.state === 'unread' &&
                h(
                  Tooltip,
                  { title: sideError(entry, errors) },
                  h('span', { className: 'sw-cat-side-count' }, '—')
                )
            );
          })
        ),
        h(
          'div',
          { className: 'sw-cat-results' },
          h(
            'div',
            { className: 'sw-cat-toolbar' },
            h(Input, {
              prefix: h(SearchOutlined, { style: { color: '#8F8FA3' } }),
              placeholder: drill
                ? `Search in ${drill.name}…`
                : SW.brand.text('Search everything in {platformName}…'),
              value: query,
              allowClear: true,
              autoFocus: true,
              onChange: (e) => setQuery(e.target.value),
            })
          ),
          drill
            ? h(
                'div',
                { className: 'sw-cat-note sw-cat-drill-head' },
                h(
                  Button,
                  { type: 'link', size: 'small', onClick: () => { setDrill(null); setQuery(''); } },
                  'Back'
                ),
                h('span', { className: 'sw-cat-drill-name' }, drill.name),
                !(member || drill.inProject)
                  ? h(
                      Button,
                      {
                        size: 'small',
                        type: 'primary',
                        loading: busyId === drill.id,
                        onClick: () => add(drill),
                      },
                      'Add to project'
                    )
                  : h('span', { className: 'sw-cat-in' }, 'In project')
              )
            : null,
          !drill && listingErrors && (rows.length > 0 || narrowed)
            ? h('div', { className: 'sw-cat-note' }, listingErrors)
            : null,
          h(
            'div',
            { className: 'sw-cat-list sw-scroll' },
            drill
              ? h(SW.ResourceTree, { resource: drillResource, query, variant: 'catalog' })
              : loading
              ? h(Skeleton, { active: true, paragraph: { rows: 6 }, style: { padding: 16 } })
              : rows.length === 0
              ? h(Empty, {
                  style: { padding: 32 },
                  // A read that failed is not a platform with nothing in it, and the two must not
                  // share a sentence. It only gets this one when nothing is narrowing the list:
                  // otherwise the filter is why the list is empty, and the refusal is said in the
                  // note above it instead.
                  description: listingErrors && !narrowed
                    ? listingErrors
                    : query.trim()
                    // What the person typed is theirs: it fills a slot and is never scanned for
                    // tokens, so a search for `{dataset}` reads back as itself.
                    ? SW.brand.text('Nothing in {platformName} matches "{query}".',
                      { query: query.trim() })
                    : 'Nothing here.',
                })
              : rows.map((resource) =>
                  h(CatalogRow, {
                    key: resource.id,
                    resource,
                    scope,
                    busy: busyId === resource.id,
                    onAdd: add,
                    onOpen: openRow,
                  })
                )
          ),
          h(
            'div',
            { className: 'sw-cat-foot' },
            h(
              'span',
              { className: 'sw-secondary' },
              // "0 results" while the listing is still being read would be the count contradicting
              // the spinner beside it.
              loading
                ? SW.brand.text('Reading {platformName}…')
                : `${rows.length} ${rows.length === 1 ? 'result' : 'results'} · ${inCount} already in ${scope.name}`
            ),
            h(Button, { onClick: () => SW.store.closeCatalog() }, 'Done')
          )
        )
      )
    );
  };
})();
