window.SW = window.SW || {};

(function () {
  const { createElement: h, useState, useEffect, Fragment } = React;
  const { Modal, Input, Button, Tooltip, Tag, Skeleton, Empty } = antd;
  const { SearchOutlined, PlusOutlined, CheckOutlined } = icons;

  // Browsing a platform catalogue needs room for the facts you actually choose
  // on — who owns it, how fresh, who else uses it. That is
  // why this is a surface you open rather than a 300px column you live beside.
  const KINDS = [
    { key: null, label: 'Everything' },
    { key: 'dataset', label: 'Datasets' },
    { key: 'datasource', label: 'Data sources' },
    { key: 'model_llm', label: 'Language models' },
    { key: 'model_predictive', label: 'Predictive models' },
    { key: 'agent', label: 'Agents' },
    { key: 'skill', label: 'Skills' },
    { key: 'mcp', label: 'MCPs' },
  ];

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
                { title: 'Runs inside your environment.' },
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
    const [rows, setRows] = useState([]);
    const [counts, setCounts] = useState({});
    const [loading, setLoading] = useState(false);
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

    useEffect(() => {
      if (!catalogOpen) return undefined;
      let cancelled = false;
      setLoading(true);
      const timer = setTimeout(() => {
        SW.api
          .catalog({
            projectId: scope.id,
            q: query,
            kind: kind || '',
          })
          .then((found) => {
            if (cancelled) return;
            setRows(found.results || []);
            setCounts(found.counts || {});
          })
          .finally(() => !cancelled && setLoading(false));
      }, 140);
      return () => {
        cancelled = true;
        clearTimeout(timer);
      };
    }, [catalogOpen, query, kind, scope.id]);

    if (!catalogOpen) return null;

    const add = async (resource) => {
      setBusyId(resource.id);
      try {
        await SW.store.addToProject(resource);
        setRows((current) =>
          current.map((r) => (r.id === resource.id ? { ...r, inProject: true } : r))
        );
        if (drill && drill.id === resource.id) setDrill({ ...drill, inProject: true });
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
              h('span', null, entry.label),
              entry.key &&
                counts[entry.key] !== undefined &&
                h('span', { className: 'sw-cat-side-count' }, counts[entry.key])
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
                : 'Search everything in Domino…',
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
            : h(
                'div',
                { className: 'sw-cat-note' },
                `Adding something makes it available to ${SW.brand.assistant()} in ${scope.name} — in every conversation and every app here, not just this one.`
              ),
          h(
            'div',
            { className: 'sw-cat-list sw-scroll' },
            drill
              ? h(SW.ResourceTree, { resource: drillResource, query, variant: 'catalog' })
              : loading && rows.length === 0
              ? h(Skeleton, { active: true, paragraph: { rows: 6 }, style: { padding: 16 } })
              : rows.length === 0
              ? h(Empty, {
                  style: { padding: 32 },
                  description: query.trim()
                    ? `Nothing in Domino matches "${query.trim()}".`
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
              `${rows.length} ${rows.length === 1 ? 'result' : 'results'} · ${inCount} already in ${scope.name}`
            ),
            h(Button, { onClick: () => SW.store.closeCatalog() }, 'Done')
          )
        )
      )
    );
  };
})();
