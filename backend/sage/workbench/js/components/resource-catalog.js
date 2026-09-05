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
    // The rail groups both of these under `Data`, and its add door has to land somewhere. Without
    // this entry the door opened on Everything, because a single kind would have meant Datasets and
    // hidden Data Sources behind a filter nobody chose (#164). The two stay listed below it: a
    // group is a wider filter than either kind, never a replacement for them.
    { key: 'data', label: 'Data', kinds: ['dataset', 'datasource'] },
    { key: 'dataset', label: '{datasetPlural}' },
    { key: 'datasource', label: '{dataSourcePlural}' },
    { key: 'model_llm', label: 'Language models' },
    { key: 'model_predictive', label: 'Predictive models' },
    { key: 'agent', label: 'Agents' },
    { key: 'skill', label: 'Skills' },
    { key: 'mcp', label: 'MCPs' },
  ];

  // What the number beside a sidebar entry says. `catalog` counts kinds, so a group entry adds its
  // own up rather than asking for a count of a kind that does not exist.
  function sideCount(entry, counts) {
    if (!entry.kinds) return counts[entry.key];
    const under = entry.kinds.filter((k) => counts[k] !== undefined);
    return under.length ? under.reduce((n, k) => n + counts[k], 0) : undefined;
  }

  function CatalogRow({ resource, scope, onAdd, onOpen, busy }) {
    return h(
      'div',
      { className: `sw-cat-row${resource.inProject ? ' is-in' : ''}` },
      h(
        'button',
        { className: 'sw-cat-open', onClick: () => onOpen(resource) },
        h('span', { className: 'sw-cat-icon' }, SW.util.iconFor(resource.kind)),
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
              )
          ),
          h('span', { className: 'sw-cat-desc' }, resource.description),
          h(
            'span',
            { className: 'sw-cat-meta' },
            h('span', null, SW.util.labelFor(resource.kind)),
            h('span', { className: 'sw-cat-dot' }, '·'),
            h('span', null, resource.originName),
            h('span', { className: 'sw-cat-dot' }, '·'),
            h('span', null, resource.ownerName),
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
    const listingErrors = Object.values((view && view.errors) || {}).filter(Boolean).join(' ');
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
          KINDS.map((entry) =>
            h(
              'button',
              {
                key: entry.key || 'all',
                className: `sw-cat-side-btn${kind === entry.key ? ' is-active' : ''}`,
                onClick: () => {
                  setKind(entry.key);
                  setDrill(null);
                },
              },
              h('span', null, SW.brand.text(entry.label)),
              entry.key &&
                sideCount(entry, counts) !== undefined &&
                h('span', { className: 'sw-cat-side-count' }, sideCount(entry, counts))
            )
          )
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
