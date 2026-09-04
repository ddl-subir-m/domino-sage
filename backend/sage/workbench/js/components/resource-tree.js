window.SW = window.SW || {};

(function () {
  const { createElement: h, useState, useEffect, useRef } = React;
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

  // What one folder holds, all the way down, for every folder in a listing — computed in one pass
  // over the files rather than per row.
  //
  // Off the WHOLE listing, never off the filtered tree below. The act attaches the subtree, so a
  // count that narrowed as you typed would be a number the act does not honour — and a
  // filter-driven attach is the thing ADR-0029 turned down, because a set that changes as you type
  // leaves nothing stable to name in a confirmation. `''` is the Dataset root, which is this act at
  // depth 0.
  // Two counts per folder, because the row and the act ask different questions. The row says what
  // the folder HOLDS; the confirmation and the cap turn on what this act would ADD, and a file the
  // app already carries is passed over rather than attached twice. Counting the held ones into the
  // question would promise 43 files and attach 3.
  // One shape for a folder's counts, so a row that has none and a row that has some are read the
  // same way and a fifth count cannot be added to one and forgotten in the other.
  const noFiles = () => ({ files: 0, bytes: 0, pending: 0, adds: 0 });

  function folderTotals(files) {
    const totals = {};
    const add = (key, file) => {
      const at = totals[key] || (totals[key] = noFiles());
      at.files += 1;
      at.bytes += Number(file.size || 0);
      if (!file.attached) {
        at.pending += 1;
        at.adds += Number(file.size || 0);
      }
    };
    (files || []).forEach((f) => {
      const parts = String(f.path || '').split('/').filter(Boolean);
      add('', f);
      let prefix = '';
      for (let i = 0; i < parts.length - 1; i += 1) {
        prefix = prefix ? `${prefix}/${parts[i]}` : parts[i];
        add(prefix, f);
      }
    });
    return totals;
  }

  // What the selected app carries below each folder, keyed the same way `folderTotals` is.
  //
  // Off the APP's own attachment records, never off the Dataset listing's `attached` flags, because
  // that is what the server's removal acts on: a file attached earlier and since deleted from the
  // Dataset is still carried and still removable, and a truncated listing does not mention its tail
  // at all. Counting the listing would then promise five and remove six.
  //
  // A folder ROW still comes from the listing, so a folder wholly inside a cut tail draws none —
  // "All files" is where its files can be taken back in one act, and the app's own list is where
  // they can be taken back one at a time (ADR-0011). The count is right wherever a row exists,
  // which is the part that had to be true before an irreversible act.
  //
  // `root` is `public/data/<slug>/`, sent by the listing. Not rebuilt here: `_slug` is a server
  // function and a second copy would be one edit away from disagreeing — and derived instead from
  // whichever entry happens to name this Dataset, a workspace whose attachments were all rebuilt by
  // the rehydrate fallback (which records no `dataset_id`) would have no entry to derive it from,
  // and the removal would vanish from a tree the server would happily have acted on.
  //
  // The matching rule is the server's, including its exception: an entry with no `dataset_id` is
  // rehydrated, records no Dataset to be attributed to, and goes with the folder it sits in.
  function carriedTotals(attachments, datasetId, root) {
    if (!root) return {};
    const totals = {};
    (attachments || []).forEach((a) => {
      if (!a || !a.path) return;
      if (a.dataset_id && a.dataset_id !== datasetId) return;
      if (!a.path.startsWith(root)) return;
      const parts = a.path.slice(root.length).split('/').filter(Boolean);
      totals[''] = (totals[''] || 0) + 1;
      let prefix = '';
      for (let i = 0; i < parts.length - 1; i += 1) {
        prefix = prefix ? `${prefix}/${parts[i]}` : parts[i];
        totals[prefix] = (totals[prefix] || 0) + 1;
      }
    });
    return totals;
  }

  // The numbers a folder row carries, and the act they are there for.
  //
  // The size is drawn only when there is one to draw. A Dataset this container has no mount for is
  // listed through the Domino data library, whose listing carries no sizes at all, so every file in
  // it reports 0 — and "0 B" beside 43 files is a measurement, not a missing one.
  //
  // The act is a link rather than a menu, for the reason `LeafRow` beside it is: this is the one
  // row in the tree that acts on more than one file, and hiding it behind a second click would
  // make the bulk act cost more than the per-file one it exists to replace.
  function FolderActs({ path, label, totals, carried, act, remove }) {
    const stat = totals[path] || noFiles();
    // What the APP carries below this folder, which is what the removal acts on — never
    // `stat.files - stat.pending`, which is what this LISTING happens to mention.
    const held = carried[path] || 0;
    if (!stat.files) return null;
    const shown = `${SW.util.number(stat.files)} ${stat.files === 1 ? 'file' : 'files'}`;
    const meta = stat.bytes ? `${shown} · ${SW.util.bytes(stat.bytes)}` : shown;
    // Nothing left to add is its own unavailable state, with its own reason. Offered anyway it
    // would open a confirmation about zero files and answer with a no-op.
    const reason = act.reason || (stat.pending ? '' : act.carried);
    // The other direction, and it does NOT share the attach's gate. Attach is withheld wherever the
    // subtree cannot be measured, because the cap has to be pre-flighted; the removal reads the
    // app's own record, so a Dataset that has since lost its mount does not strand what it already
    // gave (ADR-0029). What it needs is only that the app carries something here — offered
    // otherwise, it would open a question about zero files and answer with a no-op.
    return h(
      'span',
      { className: 'sw-tree-folder-acts' },
      h('span', { className: 'sw-tree-folder-meta' }, meta),
      reason
        // Unavailable WITH its reason, rather than absent: a folder row that simply offers nothing
        // is indistinguishable from one nobody has built the act for yet.
        ? h(
            Tooltip,
            { title: reason },
            h(Button, { size: 'small', type: 'link', disabled: true }, act.label)
          )
        : h(
            Button,
            {
              size: 'small',
              type: 'link',
              // What the act would ADD, which is the number the cap turns on and therefore the
              // number the question has to name.
              onClick: () => act.run({ path, label, files: stat.pending, bytes: stat.adds }),
            },
            act.label
          ),
      remove && held
        ? h(
            Button,
            {
              size: 'small',
              type: 'link',
              // The existing removal styling, and for the existing reason: it is the one act on
              // this row that takes something away (ADR-0011).
              danger: true,
              onClick: () => remove.run({ path, label, files: held }),
            },
            remove.label
          )
        : null
    );
  }

  function FolderNode({ name, path, children, query, depth, renderFile, totals, carried, act,
                       remove }) {
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
        'div',
        { className: 'sw-tree-folder-row' },
        h(
          'button',
          { className: 'sw-tree-folder-head', onClick: () => setOpen(!open) },
          h(open ? DownOutlined : RightOutlined, { style: { fontSize: 9 } }),
          h('span', null, name)
        ),
        h(FolderActs, { path, label: name, totals, carried, act, remove })
      ),
      open &&
        h(
          'div',
          { className: 'sw-tree-folder-body' },
          folders.map((n) =>
            h(FolderNode, {
              key: n,
              name: n,
              path: path ? `${path}/${n}` : n,
              children: children.folders[n],
              query,
              depth: depth + 1,
              renderFile,
              totals,
              carried,
              act,
              remove,
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
    // The listing stopped at the provider's cap, so these files are part of the Dataset and no
    // folder among them can be shown to be whole (ADR-0029). Said on screen rather than kept for
    // the act alone: a tree that looks complete is the thing that misleads.
    const [truncated, setTruncated] = useState(false);
    // Whether a folder row here may offer **Attach folder**, and the server's reason when it may
    // not. Composed there rather than worked out here, so the sentence this row draws before the
    // click and the one the refusal carries after it are the same sentence (ADR-0029).
    const [folderAct, setFolderAct] = useState(null);
    // Where this Dataset's files are served from, `public/data/<slug>/`. The removal counts what the
    // app carries under it, which is the set the server acts on.
    const [attachRoot, setAttachRoot] = useState('');
    // Which Dataset the files on screen belong to, so a re-read of the SAME one can leave them
    // standing. A ref rather than state: nothing redraws because of it.
    const drawn = useRef('');
    // The platform's own body, held apart from our copy so it can be quoted rather than retold
    // (#121). `null` is "nothing failed"; a failure with a silent platform is `{ body: '' }`.
    const [error, setError] = useState(null);
    // Bumped by a folder act that changed something, because the listing carries the `attached`
    // flag every row's numbers are built from. Without it an attach leaves every row still offering
    // to attach what it just attached, and a removal leaves its own button standing over a folder
    // the app no longer carries — until somebody closes the panel and opens it again.
    const [reread, setReread] = useState(0);
    const datasetId = resource ? bareId(resource.id, 'dataset') : '';
    const pins = pinSet(resource && resource.pins);

    // No mount check. A Dataset this container has not mounted is still readable through the
    // Domino data library, and a mount only ever covers this one project — so gating the tree on
    // `resource.path` hid the files of every Dataset shared from anywhere else.
    useEffect(() => {
      if (!resource) {
        setFiles([]);
        setTruncated(false);
        setFolderAct(null);
        setAttachRoot('');
        setError(null);
        // Forgotten with them, or picking the SAME Dataset again would find its id still standing
        // here, skip the blanking below, and draw the empty tree until the fetch lands.
        drawn.current = '';
        return undefined;
      }
      let cancelled = false;
      // Blanked only when the DATASET changed. A re-read after a folder act is the same tree
      // answering again, and dropping to the spinner would remount every `FolderNode` — collapsing
      // four levels of expansion and scrolling the row just acted on off the screen. Not covered by
      // the harness beside this file: its React re-invokes components rather than mounting them, so
      // a remount is the one thing it cannot see.
      if (datasetId !== drawn.current) setFiles(null);
      drawn.current = datasetId;
      // Cleared with the files, not left standing. The instance survives a change of `resource`,
      // so without this a Dataset that answered fine drew the previous one's failure — the read
      // landed, `files` was replaced, and the stale `error` won the branch above it. The Data
      // Source cascade beside this one has always reset both.
      setError(null);
      SW.api
        .assetFiles(datasetId)
        .then((body) => {
          if (!cancelled) {
            setFiles(body.files || []);
            setTruncated(!!body.truncated);
            setFolderAct(body.folder_act || null);
            setAttachRoot(body.attach_root || '');
          }
        })
        .catch((err) => {
          if (!cancelled) {
            setFiles([]);
            setTruncated(false);
            setFolderAct(null);
            setAttachRoot('');
            setError({ body: err.message || '' });
          }
        });
      return () => {
        cancelled = true;
      };
    }, [datasetId, reread]);

    if (files === null) return h(Spin, { size: 'small', className: 'sw-tree-spin' });
    if (error) {
      return treeFailure(
        error,
        '{assistantName} couldn’t list the files in this {dataset}.',
        'Check it is still shared with this project in {platformName}, then reopen this panel.',
      );
    }
    // Drawn above the files in both branches, including the one where the filter matched nothing:
    // `No files match` is a lie about a listing whose tail was never read.
    const cut = truncated && h(
      'div',
      { className: 'sw-tree-truncated' },
      // No claim about WHICH files these are. The mounted walk is sorted, so its cap cuts the
      // tail; the data library answers in its own order, and neither promise would hold for both.
      SW.brand.text(
        'This {dataset} holds more files than {assistantName} can list. The listing stopped at '
        + '{count}, so what is here is part of it, not all of it.',
        { count: SW.util.number((files || []).length) }
      )
    );
    const visible = (files || []).filter((f) => filterName(f.path, query));
    if (!visible.length) {
      const empty = h(
        'div',
        { className: 'sw-tree-empty' },
        query
          ? SW.util.noMatch(query)
          : SW.brand.text('No files in this {dataset}.')
      );
      // Returned bare when there is nothing to qualify, so a complete listing draws exactly what
      // it drew before — the wrapper carries the tree's own indent, and this row is not in a tree.
      return cut ? h('div', { className: `sw-tree sw-tree-${variant || 'rail'}` }, cut, empty) : empty;
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
    // One act, drawn on every folder row and on the Dataset's own. What withholds it is settled
    // once, here: the server's answer about this listing, and the one thing only the client knows
    // — which Built App the label would name (ADR-0008 makes that a question every surface has to
    // answer, and a door promising "to this app" with none selected is a dead end).
    const app = SW.store.get().activeApp;
    // A cancelled confirmation changed nothing, so it costs no fetch. A removal that FAILED can
    // still have moved files — it commits what it unlinked — and answers `'stale'`, which is
    // truthy here for exactly that reason.
    const rereadIfChanged = (changed) => {
      if (changed) setReread((n) => n + 1);
      return changed;
    };
    const act = {
      label: app ? `Attach folder to ${app.name}` : 'Attach folder',
      // Fails CLOSED on a listing that carried no answer. Reading a missing `folder_act` as
      // "available" would draw an enabled button on exactly the Datasets the route turns down,
      // which is the one arrangement this field exists to make impossible.
      reason: !folderAct
        ? SW.brand.text('{assistantName} could not tell whether this folder can be attached.')
        : folderAct.available
        ? (app ? '' : 'No app selected — a folder is attached to one app.')
        : folderAct.reason,
      carried: app ? `${app.name} already carries every file here.` : '',
      run: ({ path, label, files: count, bytes }) =>
        SW.store.attachFolderToApp({
          datasetId,
          label,
          folder: path,
          files: count,
          bytes,
        }).then(rereadIfChanged),
    };
    // The removal, which names the app for the reason every removal label does: the scope is the
    // only thing telling the three of them apart (ADR-0011). With no app selected there is no scope
    // to name and nothing carried to remove, so it is absent rather than disabled — unlike the
    // attach, which is a door somebody came looking for and has to say why it is shut.
    const remove = app
      ? {
          label: `Remove folder from ${app.name}`,
          run: ({ path, label, files: count }) =>
            SW.store.removeFolderFromApp({
              datasetId,
              label,
              folder: path,
              files: count,
            }).then(rereadIfChanged),
        }
      : null;
    const totals = folderTotals(files);
    // Read at render off the store, which `loadScopeData` has already refreshed by the time either
    // act resolves — so the counts the rows redraw with are the app's newest record, not the one
    // this listing was fetched beside.
    const carried = carriedTotals(SW.store.get().appAttachments, datasetId, attachRoot);
    return h(
      'div',
      { className: `sw-tree sw-tree-${variant || 'rail'}` },
      cut,
      // The Dataset root, which is this act at depth 0 rather than a second feature with its own
      // name and its own edge cases. A row rather than a folder around the tree: the Dataset is
      // already named by the row this tree hangs under, and wrapping it would indent every folder
      // to say so again.
      h(
        'div',
        { className: 'sw-tree-root-row' },
        // Not "the whole Dataset". The root is a folder like any other here, and borrowing the
        // whole-Dataset chip's words would give one phrase two meanings — that chip reads the
        // mount to answer a question, and this ships the bytes (ADR-0029).
        h('span', { className: 'sw-tree-root-name' }, 'All files'),
        h(FolderActs, { path: '', label: resource.name, totals, carried, act, remove })
      ),
      folders.map((n) =>
        h(FolderNode, {
          key: n,
          name: n,
          path: n,
          children: tree.folders[n],
          query,
          depth: 0,
          renderFile,
          totals,
          carried,
          act,
          remove,
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
