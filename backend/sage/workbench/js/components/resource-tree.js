window.SW = window.SW || {};

(function () {
  const { createElement: h, useState, useEffect } = React;
  const { Button, Spin } = antd;
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

  function LeafRow({ name, subtitle, pinned, onMention, onPin, onUnpin }) {
    return h(
      'div',
      { className: 'sw-tree-leaf' },
      h('span', { className: 'sw-tree-leaf-name', title: subtitle || name }, name),
      h(
        'span',
        { className: 'sw-tree-leaf-acts' },
        h(Button, { size: 'small', type: 'link', onClick: onMention }, 'Add to chat'),
        pinned
          ? h(Button, { size: 'small', type: 'link', onClick: onUnpin }, 'Unpin')
          : h(Button, { size: 'small', type: 'link', onClick: onPin }, 'Pin')
      )
    );
  }

  SW.DatasetFileTree = function DatasetFileTree({ resource, query, variant }) {
    const [files, setFiles] = useState(null);
    const [error, setError] = useState('');
    const mounted = Boolean(resource && resource.path);
    const datasetId = resource ? bareId(resource.id, 'dataset') : '';
    const pins = pinSet(resource && resource.pins);

    useEffect(() => {
      if (!resource || !mounted) {
        setFiles([]);
        setError('');
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
            setError(err.message || 'Could not list files.');
          }
        });
      return () => {
        cancelled = true;
      };
    }, [datasetId, mounted]);

    if (!mounted) {
      return h(
        'div',
        { className: 'sw-tree-empty' },
        'Files are not mounted in this workspace.'
      );
    }
    if (files === null) return h(Spin, { size: 'small', className: 'sw-tree-spin' });
    if (error) return h('div', { className: 'sw-tree-empty' }, error);
    const visible = (files || []).filter((f) => filterName(f.path, query));
    if (!visible.length) {
      return h(
        'div',
        { className: 'sw-tree-empty' },
        query ? `Nothing matches “${query.trim()}”.` : 'No files in this Dataset.'
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
    const [error, setError] = useState('');
    const pins = pinSet(resource && resource.pins);

    const hasDatabase = levels.includes('database');
    const hasSchema = levels.includes('schema');
    const stage = !hasDatabase || database
      ? hasSchema && !schema ? 'schema' : (database || !hasDatabase) && (schema || !hasSchema) ? 'table' : 'database'
      : 'database';

    useEffect(() => {
      if (!resource) return undefined;
      if (!levels.length) {
        setItems([]);
        setError('');
        return undefined;
      }
      let cancelled = false;
      setItems(null);
      setError('');
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
            setError(err.message || 'Could not look inside this Data Source.');
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
        'Sage cannot look inside this Data Source.'
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

    if (items === null) return h(Spin, { size: 'small', className: 'sw-tree-spin' });
    if (error) return h('div', { className: 'sw-tree-empty' }, error);

    const visible = (items || []).filter((n) => filterName(n, query));
    if (!visible.length) {
      return h(
        'div',
        { className: 'sw-tree' },
        crumb.length
          ? h(
              'div',
              { className: 'sw-tree-crumb' },
              crumb.map((c) =>
                h('button', { key: c.label, className: 'sw-tree-crumb-btn', onClick: c.onClick }, c.label)
              )
            )
          : null,
        h(
          'div',
          { className: 'sw-tree-empty' },
          query ? `Nothing matches “${query.trim()}”.` : 'Nothing at this level.'
        )
      );
    }

    return h(
      'div',
      { className: `sw-tree sw-tree-${variant || 'rail'}` },
      crumb.length
        ? h(
            'div',
            { className: 'sw-tree-crumb' },
            crumb.map((c) =>
              h('button', { key: c.label, className: 'sw-tree-crumb-btn', onClick: c.onClick }, c.label)
            )
          )
        : null,
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
