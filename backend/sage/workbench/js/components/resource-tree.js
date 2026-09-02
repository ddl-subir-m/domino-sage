window.SW = window.SW || {};

(function () {
  const { createElement: h, useState, useEffect } = React;
  const { Button, Spin, Tooltip } = antd;
  const { DownOutlined, RightOutlined } = icons;

  function bareId(id, kind) {
    const s = String(id || '');
    const prefix = `${kind}:`;
    if (s.startsWith(prefix)) return s.slice(prefix.length);
    if (kind === 'data_source' && s.startsWith('datasource:')) return s.slice('datasource:'.length);
    return s;
  }

  function filterName(name, query) {
    const q = (query || '').trim().toLowerCase();
    return !q || String(name || '').toLowerCase().includes(q);
  }

  function pinSet(pins) {
    const files = new Set();
    const tables = new Set();
    (pins || []).forEach((p) => {
      if (p && p.path) files.add(p.path);
      if (p && p.table) {
        tables.add([p.database || '', p.schema || '', p.table].join('\0'));
      }
    });
    return { files, tables };
  }

  function FolderNode({ name, children, query, depth, renderFile }) {
    const [open, setOpen] = useState(depth < 1);
    const files = (children.files || []).filter((f) => filterName(f.path.split('/').pop(), query));
    const folders = Object.keys(children.folders || {}).filter((n) => {
      if (filterName(n, query)) return true;
      return folderHasMatch(children.folders[n], query);
    });
    if (!files.length && !folders.length) return null;
    return h(
      'div',
      { className: 'sw-tree-folder' },
      h(
        'button',
        { className: 'sw-tree-folder-head', onClick: () => setOpen(!open) },
        h(open ? DownOutlined : RightOutlined, { style: { fontSize: 9 } }),
        h('span', null, name)
      ),
      open &&
        h(
          'div',
          { className: 'sw-tree-folder-body' },
          folders.map((n) =>
            h(FolderNode, {
              key: n,
              name: n,
              children: children.folders[n],
              query,
              depth: depth + 1,
              renderFile,
            })
          ),
          files.map((f) => renderFile(f))
        )
    );
  }

  function folderHasMatch(node, query) {
    if ((node.files || []).some((f) => filterName(f.path.split('/').pop(), query))) return true;
    return Object.keys(node.folders || {}).some(
      (n) => filterName(n, query) || folderHasMatch(node.folders[n], query)
    );
  }

  function nestFiles(files) {
    const root = { folders: {}, files: [] };
    (files || []).forEach((f) => {
      const parts = String(f.path || '').split('/').filter(Boolean);
      if (!parts.length) return;
      let cur = root;
      parts.slice(0, -1).forEach((part) => {
        cur.folders[part] = cur.folders[part] || { folders: {}, files: [] };
        cur = cur.folders[part];
      });
      cur.files.push(f);
    });
    return root;
  }

  // One row at the bottom of a tree, and the acts whose scope this tree owns.
  //
  // `Use in {app}` was here until #142. It is gone rather than moved: a Binding is the Built App's,
  // so its door is on the app's own surface (ADR-0021), and this tree is the working set's — it
  // shows what a Data Source or a Dataset CONTAINS, which is orientation. What it keeps is the
  // Conversation's act and the pin, whose scopes it does own.
  function LeafRow({ name, subtitle, pinned, onMention, onPin, onUnpin }) {
    return h(
      'div',
      { className: 'sw-tree-leaf' },
      h('span', { className: 'sw-tree-leaf-name', title: subtitle || name }, name),
      h(
        'span',
        { className: 'sw-tree-leaf-acts' },
        h(Button, { size: 'small', type: 'link', onClick: onMention }, 'Use in this chat'),
        pinned
          ? h(Button, { size: 'small', type: 'link', onClick: onUnpin }, 'Unpin')
          // Pin only reorders the @ menu — it sends nothing. Sitting unlabelled beside the control
          // that DOES send, it read as a second way to attach (docs/workbench/chat.md). The title
          // goes through the pack because it names the assistant.
          : h(
              Tooltip,
              {
                title: SW.brand.text(
                  'Keeps this at the top of the @ menu. It does not send it to {assistantName}.'
                ),
              },
              h(Button, { size: 'small', type: 'link', onClick: onPin }, 'Pin')
            )
      )
    );
  }

  // Both trees fail the same way — the platform refused and handed back a body — so they say it
  // the same way: our reason and our fix in the pack's words, its words quoted between them (#121).
  function treeFailure(error, reason, fix) {
    return h('div', { className: 'sw-tree-empty' }, h(SW.PlatformError, {
      reason: SW.brand.text(reason),
      body: error.body,
      fix: SW.brand.text(fix),
    }));
  }

  SW.DatasetFileTree = function DatasetFileTree({ resource, query, variant }) {
    const [files, setFiles] = useState(null);
    // The platform's own body, held apart from our copy so it can be quoted rather than retold
    // (#121). `null` is "nothing failed"; a failure with a silent platform is `{ body: '' }`.
    const [error, setError] = useState(null);
    const datasetId = resource ? bareId(resource.id, 'dataset') : '';
    const pins = pinSet(resource && resource.pins);

    // No mount check. A Dataset this container has not mounted is still readable through the
    // Domino data library, and a mount only ever covers this one project — so gating the tree on
    // `resource.path` hid the files of every Dataset shared from anywhere else.
    useEffect(() => {
      if (!resource) {
        setFiles([]);
        setError(null);
        return undefined;
      }
      let cancelled = false;
      setFiles(null);
      SW.api
        .assetFiles(datasetId)
        .then((body) => {
          if (!cancelled) setFiles(body.files || []);
        })
        .catch((err) => {
          if (!cancelled) {
            setFiles([]);
            setError({ body: err.message || '' });
          }
        });
      return () => {
        cancelled = true;
      };
    }, [datasetId]);

    if (files === null) return h(Spin, { size: 'small', className: 'sw-tree-spin' });
    if (error) {
      return treeFailure(
        error,
        '{assistantName} couldn’t list the files in this {dataset}.',
        'Check it is still shared with this project in {platformName}, then reopen this panel.',
      );
    }
    const visible = (files || []).filter((f) => filterName(f.path, query));
    if (!visible.length) {
      return h(
        'div',
        { className: 'sw-tree-empty' },
        query
          ? SW.util.noMatch(query)
          : SW.brand.text('No files in this {dataset}.')
      );
    }
    const tree = nestFiles(visible);
    const parent = {
      id: resource.id,
      kind: 'dataset',
      name: resource.name,
      path: resource.path,
    };
    const renderFile = (f) => {
      const leaf = {
        id: `dsfile:${datasetId}:${f.path}`,
        name: f.path.split('/').pop(),
        kind: 'file',
        datasetId,
        datasetRelPath: f.path,
        datasetName: resource.name,
        path: f.attached ? f.dest : undefined,
        parentId: resource.id,
      };
      return h(LeafRow, {
        key: f.path,
        name: leaf.name,
        subtitle: f.path,
        pinned: pins.files.has(f.path),
        onMention: () => SW.store.addToContext(leaf, { quiet: true }),
        onPin: () => SW.store.pinLeaf(parent, { path: f.path, name: leaf.name }),
        onUnpin: () => SW.store.unpinLeaf(parent, { path: f.path }),
      });
    };
    const folders = Object.keys(tree.folders);
    return h(
      'div',
      { className: `sw-tree sw-tree-${variant || 'rail'}` },
      folders.map((n) =>
        h(FolderNode, {
          key: n,
          name: n,
          children: tree.folders[n],
          query,
          depth: 0,
          renderFile,
        })
      ),
      tree.files.map(renderFile)
    );
  };

  // Walking a Data Source, for looking (#142, ADR-0021).
  //
  // It used to be where a Data Source was BOUND: #129 made a Scope the cascade position the creator
  // was standing on, and hung the door beside the crumb and on every leaf. That put a three-level
  // tree walk in front of every bind. Binding and scoping are two acts now — the app's own surface
  // binds, and its Scope door walks this same ladder afterwards — so what is left here is the
  // question the working set's panel is for: what is inside this thing.
  SW.DataSourceCascade = function DataSourceCascade({ resource, query, variant }) {
    const levels = (resource && resource.levels) || [];
    const sourceId = resource ? bareId(resource.id, 'data_source') : '';
    const defaults = {
      database: resource && resource.default_database,
      schema: resource && resource.default_schema,
    };
    const [database, setDatabase] = useState(defaults.database || '');
    const [schema, setSchema] = useState(defaults.schema || '');
    const [items, setItems] = useState(null);
    const [error, setError] = useState(null);
    const pins = pinSet(resource && resource.pins);

    const hasDatabase = levels.includes('database');
    const hasSchema = levels.includes('schema');
    // Shared with the Build header's Scope door, which walks the same ladder to choose rather than
    // to look (#142). One copy, so the two surfaces cannot disagree about which rung a walk is on.
    const stage = SW.util.cascadeStage(levels, database, schema);

    useEffect(() => {
      if (!resource) return undefined;
      if (!levels.length) {
        setItems([]);
        setError(null);
        return undefined;
      }
      let cancelled = false;
      setItems(null);
      setError(null);
      const load =
        stage === 'database'
          ? SW.api.dataSourceDatabases(sourceId)
          : stage === 'schema'
            ? SW.api.dataSourceSchemas(sourceId, database)
            : SW.api.dataSourceTables(sourceId, database, schema);
      load
        .then((body) => {
          if (!cancelled) setItems(body.items || []);
        })
        .catch((err) => {
          if (!cancelled) {
            setItems([]);
            setError({ body: err.message || '' });
          }
        });
      return () => {
        cancelled = true;
      };
    }, [sourceId, stage, database, schema, levels.length]);

    if (!levels.length) {
      return h(
        'div',
        { className: 'sw-tree-empty' },
        SW.brand.text('{assistantName} cannot look inside this {dataSource}.')
      );
    }

    const parent = {
      id: resource.id,
      kind: 'datasource',
      name: resource.name,
      bindingKey: resource.bindingKey || ['data_source', sourceId],
    };

    const crumb = [
      hasDatabase && database && { label: database, onClick: () => { setSchema(''); setDatabase(''); } },
      hasSchema && schema && { label: schema, onClick: () => setSchema('') },
    ].filter(Boolean);

    // Where the walk has got to, and the way back up out of it. A door used to stand beside this,
    // sending the position as a Scope (#129); it is on the Built App's own surface now, so the
    // crumb is a crumb again — where you are, and how to leave.
    const scopeBar = () =>
      crumb.length
        ? h(
            'div',
            { className: 'sw-tree-crumb' },
            crumb.map((c) =>
              h('button', { key: c.label, className: 'sw-tree-crumb-btn', onClick: c.onClick }, c.label)
            )
          )
        : null;

    if (items === null) return h(Spin, { size: 'small', className: 'sw-tree-spin' });
    if (error) {
      // A store that will not answer is not a Scope the creator has lost. They walked here through
      // listings that DID answer, so the position still holds — and the Binding is a decision about
      // which part of the source the app reads, not a promise that the next level down is readable
      // today. Same reason `_write_bound_schema` records the Binding when the columns fail to come
      // back: what is lost is the listing, and the door stays where the crumb is.
      return h(
        'div',
        { className: `sw-tree sw-tree-${variant || 'rail'}` },
        scopeBar(),
        treeFailure(
          error,
          '{assistantName} couldn’t look inside this {dataSource}.',
          'Check your credentials for it in {platformName}, then reopen this panel.',
        )
      );
    }

    const visible = (items || []).filter((n) => filterName(n, query));
    if (!visible.length) {
      return h(
        'div',
        { className: 'sw-tree' },
        scopeBar(),
        h(
          'div',
          { className: 'sw-tree-empty' },
          query ? SW.util.noMatch(query) : 'Nothing at this level.'
        )
      );
    }

    return h(
      'div',
      { className: `sw-tree sw-tree-${variant || 'rail'}` },
      scopeBar(),
      visible.map((name) => {
        if (stage !== 'table') {
          return h(
            'button',
            {
              key: name,
              className: 'sw-tree-step',
              onClick: () => {
                if (stage === 'database') setDatabase(name);
                else setSchema(name);
              },
            },
            name
          );
        }
        const dotted = [database, schema, name].filter(Boolean).join('.');
        const leaf = {
          id: `table:${sourceId}:${dotted}`,
          name,
          kind: 'table',
          bindingKey: parent.bindingKey,
          scope: { database: database || '', schema: schema || '', table: name },
          parentId: resource.id,
          subtitle: resource.name,
        };
        const key = [database || '', schema || '', name].join('\0');
        return h(LeafRow, {
          key: dotted,
          name,
          subtitle: dotted,
          pinned: pins.tables.has(key),
          onMention: () => SW.store.addToContext(leaf, { quiet: true }),
          onPin: () => SW.store.pinLeaf(parent, {
            database: database || '',
            schema: schema || '',
            table: name,
            name,
          }),
          onUnpin: () => SW.store.unpinLeaf(parent, {
            database: database || '',
            schema: schema || '',
            table: name,
          }),
        });
      })
    );
  };

  SW.ResourceTree = function ResourceTree({ resource, query, variant }) {
    if (!resource) return null;
    if (resource.kind === 'dataset') {
      return h(SW.DatasetFileTree, { resource, query, variant });
    }
    if (resource.kind === 'datasource') {
      return h(SW.DataSourceCascade, { resource, query, variant });
    }
    return null;
  };
})();
