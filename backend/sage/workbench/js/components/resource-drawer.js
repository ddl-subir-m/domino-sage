window.SW = window.SW || {};

(function () {
  const { createElement: h, useState, useEffect } = React;
  const { Drawer, Button, Table, Alert, Space } = antd;

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

    // The join, reported when it fails — because nothing else here would report it. The drawer
    // stays open, the alert above still says the resource is not in the project and the button
    // still reads `Use in this chat`, so a refused join looks exactly like a click that never
    // landed. The composer says so about its own @mention for the same reason.
    const useHere = () =>
      SW.store
        .addToContext(resource, { quiet: true })
        .catch((err) => antd.message.error(String((err && err.message) || err)));

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

        // Where this is used, in one look (#133). Three lists decide whether a Resource is reachable
        // — Project membership, this Conversation's context, and each Built App's Bindings — and
        // until now nothing on screen laid them beside each other, so the model was invisible until
        // it bit. The chip is client state and the apps are the server's enrichment of the same
        // membership row the rail is drawn from; no request of its own.
        //
        // This Conversation and no other: a chip belongs to the Conversation it was added in
        // (ADR-0015), so a scan of other Conversations would report context this one does not have.
        h(
          'div',
          { className: 'sw-drawer-section' },
          h('h4', null, 'Where this is used'),
          h(
            'dl',
            { className: 'sw-drawer-meta sw-where-used' },
            h('dt', null, 'This conversation'),
            h('dd', null, attached ? 'In use' : 'Not in use'),
            ...(resource.usedBy || []).flatMap((entry) => [
              h('dt', { key: `app-${entry.appId}` }, entry.name),
              // The Scope where the Binding recorded one, because for a Data Source which part of
              // the store the app reads is the answer, not that it reads one. Every other kind
              // records no Scope, and there "In use" is the whole of what there is to say.
              h('dd', { key: `scope-${entry.appId}` }, entry.scope || 'In use'),
            ])
          ),
          (resource.usedBy || []).length === 0 &&
            h(
              'p',
              { className: 'sw-secondary', style: { margin: 0 } },
              `No app in ${scope.name} uses it yet. Use it in one from the project list.`
            )
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
              'Using it in this chat adds it to {scope}, so {assistantName} can reach it '
                + 'everywhere in the project. You can remove it later.',
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
            // One act either way, so one button. Membership is the provisioning step, and it now
            // happens on the way into the chat instead of gating it — `Add to {project}` named the
            // machine's reason and made the user do it first.
            h(
              Button,
              {
                type: 'primary',
                disabled: attached,
                // A resource already in the project closes the drawer on its way out, the way it
                // always did, and Sage acknowledges the pick in the Thread.
                //
                // A catalogue one does NOT close: the alert above says it is not in the project,
                // and this button flipping to `In this chat` is where the user watches that stop
                // being true. It is quiet for the same reason — the flip and the join toast are
                // already the feedback, and with the drawer still up, a third acknowledgement is
                // being told once per surface.
                onClick: inProject
                  ? mention
                  : useHere,
              },
              attached ? 'In this chat' : 'Use in this chat'
            )
          ),
      },
      loading ? h(antd.Skeleton, { active: true, paragraph: { rows: 8 } }) : body()
    );
  };
})();
