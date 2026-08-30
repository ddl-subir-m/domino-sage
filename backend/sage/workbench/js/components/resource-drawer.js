window.SW = window.SW || {};

(function () {
  const { createElement: h, useState, useEffect } = React;
  const { Drawer, Button, Table, Alert, Space, Tag, Descriptions } = antd;

  SW.ResourceDrawer = function ResourceDrawer() {
    const { previewResourceId, attachments, resourceIndex, scope } = SW.store.get();
    const [resource, setResource] = useState(null);
    const [loading, setLoading] = useState(false);

    useEffect(() => {
      if (!previewResourceId) return;
      setLoading(true);
      SW.api
        .resource(previewResourceId)
        .then(setResource)
        .finally(() => setLoading(false));
    }, [previewResourceId]);

    const close = () => SW.store.previewResource(null);
    const attached = resource && attachments.some((a) => a.resourceId === resource.id);
    // The index only holds the project's working set, so membership is simply
    // whether this thing is in it.
    const inProject = Boolean(resource && resourceIndex[resource.id]);

    const mention = async () => {
      await SW.store.addToContext(resource);
      close();
    };

    const body = () => {
      if (!resource) return null;
      const meta = SW.util.RESOURCE_META[resource.kind] || {};

      const columns = (resource.schema || []).map((col, index) => ({
        title: col.name,
        dataIndex: index,
        key: col.name,
        ellipsis: true,
        render: (value) => (value === undefined || value === null ? '—' : String(value)),
      }));
      const rows = (resource.sampleRows || []).map((row, index) => ({ key: index, ...row }));

      return h(
        'div',
        null,
        h('p', { className: 'sw-secondary', style: { marginTop: 0 } }, resource.description),

        h(
          'dl',
          { className: 'sw-drawer-meta' },
          h('dt', null, 'Type'),
          h('dd', null, meta.label || resource.kind),
          h('dt', null, 'Owner'),
          h('dd', null, resource.ownerName),
          h('dt', null, 'Last updated'),
          h('dd', null, SW.util.relativeTime(resource.updatedAt)),
          resource.rowCount !== undefined && h('dt', null, 'Rows'),
          resource.rowCount !== undefined && h('dd', null, SW.util.number(resource.rowCount)),
          resource.freshness && h('dt', null, 'Freshness'),
          resource.freshness && h('dd', null, resource.freshness),
          (resource.lineage || []).length > 0 && h('dt', null, 'Comes from'),
          (resource.lineage || []).length > 0 &&
            h('dd', null, resource.lineage.join(', '))
        ),

        (resource.schema || []).length > 0 &&
          h(
            'div',
            { className: 'sw-drawer-section' },
            h('h4', null, resource.kind === 'model_predictive' ? 'Inputs and output' : 'Schema'),
            h(Table, {
              size: 'small',
              pagination: false,
              dataSource: resource.schema.map((col, i) => ({ key: i, ...col })),
              columns: [
                { title: 'Column', dataIndex: 'name', key: 'name', ellipsis: true },
                { title: 'Type', dataIndex: 'type', key: 'type', width: 140 },
              ],
            })
          ),

        rows.length > 0 &&
          h(
            'div',
            { className: 'sw-drawer-section' },
            h('h4', null, 'Sample rows'),
            h(Table, {
              size: 'small',
              pagination: false,
              scroll: { x: true },
              dataSource: rows,
              columns,
            })
          ),

        !inProject &&
          h(Alert, {
            type: 'info',
            showIcon: true,
            message: `Not in ${scope.name} yet`,
            // Three roles in one sentence: the platform's catalogue and our own name both resolve
            // through the pack, while the Project's name is the user's word and only fills a slot.
            description: SW.brand.text(
              'You are looking at it in the {platformName} catalogue. Adding it makes it available '
                + 'to {assistantName} everywhere in {scope}. You can remove it later.',
              { scope: scope.name },
            ),
          })
      );
    };

    return h(
      Drawer,
      {
        open: Boolean(previewResourceId),
        onClose: close,
        width: 480,
        title: resource ? `${SW.util.iconFor(resource.kind)}  ${resource.name}` : 'Resource',
        destroyOnClose: true,
        extra:
          resource &&
          h(
            Space,
            null,
            // Two different acts, and which one is primary depends on where the
            // thing already is. Getting it into the project comes first;
            // pointing at it in a conversation only makes sense after.
            inProject
              ? h(
                  Button,
                  { type: 'primary', disabled: attached, onClick: mention },
                  attached ? 'In this chat' : 'Use in this chat'
                )
              : h(
                  Button,
                  {
                    type: 'primary',
                    onClick: async () => {
                      await SW.store.addToProject(resource);
                      close();
                    },
                  },
                  `Add to ${scope.name}`
                )
          ),
      },
      loading ? h(antd.Skeleton, { active: true, paragraph: { rows: 8 } }) : body()
    );
  };
})();
