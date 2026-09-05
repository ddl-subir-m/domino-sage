window.SW = window.SW || {};

(function () {
  const { createElement: h } = React;

  // The world's "today". Fixture dates are written against this, so relative
  // times read correctly no matter when the prototype is demoed.
  const TODAY = new Date();

  // The two rows the pack renames read their label through a getter rather than holding a string:
  // this file is evaluated before /api/brand answers, and `RESOURCE_META[kind].label` is read
  // directly as well as through `labelFor`, so read time is the only point that covers both.
  const RESOURCE_META = {
    plan:             { icon: '📋', label: 'plan',           group: 'artifacts' },
    dataset:          { icon: '📦', group: 'data', get label() { return SW.brand.text('{dataset}'); } },
    table:            { icon: '▦',  label: 'database table', group: 'data' },
    datasource:       { icon: '🔌', group: 'data', get label() { return SW.brand.text('{dataSource}'); } },
    model_llm:        { icon: '🧠', label: 'model',          group: 'models' },
    model_predictive: { icon: '🤖', label: 'predictive model', group: 'models' },
    tool:             { icon: '🔧', label: 'tool',           group: 'tools' },
    agent:            { icon: '✨', label: 'agent',          group: 'agents' },
    file:             { icon: '📄', label: 'file',           group: 'files' },
    artifact:         { icon: '🖼', label: 'artifact',       group: 'artifacts' },
    skill:            { icon: '📘', label: 'skill',          group: 'skills' },
    mcp:              { icon: '🧩', label: 'MCP',            group: 'mcp' },
    folder:           { icon: '📁', label: 'folder',         group: 'files' },
  };

  // The Resource kinds that get a membership row of their own, in the UI id space. A Dataset file
  // and a warehouse table hang off one of these and have no row. The same four in Domino's own
  // spelling are `_MEMBERSHIP_PARENT_KINDS` in `sage/orchestrator/service.py`; `uiKind` below is
  // the mapping between the two.
  //
  // Shared because two surfaces have to agree on it exactly: the store keeps the non-member ones
  // out of the rail so the @ menu can offer them, and the composer draws the group it keeps. A
  // kind in one list and not the other is either a row held and never shown, or shown and never
  // held — and both look like the menu forgetting things.
  const MEMBERSHIP_PARENT_KINDS = ['dataset', 'datasource', 'model_llm', 'model_predictive'];

  const PLAN_STATUS = {
    draft:      { label: 'Draft',      color: 'default' },
    in_review:  { label: 'In review',  color: 'blue' },
    approved:   { label: 'Approved',   color: 'green' },
    building:   { label: 'Building',   color: 'purple' },
    shipped:    { label: 'Shipped',    color: 'success' },
    superseded: { label: 'Superseded', color: 'default' },
    failed:     { label: 'Failed',     color: 'error' },
  };

  const APP_STATUS = {
    draft:    { label: 'Draft' },
    building: { label: 'Building' },
    running:  { label: 'Running' },
    stopped:  { label: 'Stopped' },
    failed:   { label: 'Failed' },
  };

  SW.util = {
    TODAY,
    RESOURCE_META,
    MEMBERSHIP_PARENT_KINDS,
    PLAN_STATUS,
    APP_STATUS,

    // One sentence, two surfaces: the catalogue row and the panel row wear the same tag, so the
    // tag has to say the same thing in both. It was written out twice and drifted apart once.
    SOVEREIGN_TITLE: 'Runs inside your environment.',

    // What the panel row's context mark means. It names the Conversation and the chips, because
    // the mark is a reflection of them and not a second store of the same fact (#137, ADR-0015) —
    // a reader who wants it gone has to be sent to where it can go. The panel offers no verb for
    // that: "Stop using here" is on the row's own menu and in the drawer behind it.
    IN_CONTEXT_TITLE: 'In this conversation — it is one of the chips above the composer.',

    // The mark a `missing` row wears, and the sentence behind it. Three surfaces read one answer —
    // the rail, the @ menu and the bind picker — so the words are written once, the way
    // `SOVEREIGN_TITLE` is: a person carries what they read from one menu to the other (ADR-0021).
    //
    // It marks and never withholds. A missing row stays selectable and says why at the point of
    // picking, because a Problem informs and never blocks (ADR-0027) and the two acts Sage refuses
    // already catch this downstream, knowing more about the failure than a picker does.
    missingMark() {
      return SW.brand.text('not in {platformName}');
    },
    // Says what the check actually proves, and no more. All three checkable listings are filtered
    // to what the CALLER may see — Datasets by `minimumPermission`, Data Sources and Aliases by the
    // accessible set — so an absence is a deletion or a revoked grant, and Sage cannot tell which
    // from here. A sentence asserting the Resource is gone would be flatly wrong for the second
    // case, and the second case is the one where somebody else can put it back.
    missingTitle() {
      return SW.brand.text(
        'You can no longer reach this on {platformName}: it was deleted, or your access to it was.'
      );
    },
    isMissing(row) {
      return !!row && row.liveness === 'missing';
    },

    // Every composer offers the same @ affordance, so the half of the placeholder that advertises
    // it is written once. Only the lead changes with what the composer is for.
    composerPlaceholder(lead) {
      return `${lead}… use @ to bring in a resource`;
    },

    // The zero-state for a filter box. Every tree, list and palette that filters uses this one,
    // so the quoting and the wording stay the same wherever somebody types a query that misses.
    noMatch(query) {
      return `Nothing matches "${String(query || '').trim()}".`;
    },

    // A shortcut label in the keys the reader's own keyboard has. Every handler in app.js already
    // takes `metaKey || ctrlKey`, so the shortcuts have always worked on Windows and Linux — only
    // the labels were Mac-only, which told a Windows reader that ⌘K was a key they do not have
    // and therefore that there was no shortcut (#150).
    //
    // Labels are written once, in Mac notation, and translated on the way to the screen: one
    // spelling in the source, so the two platforms cannot drift apart the way two hardcoded
    // strings would. `navigator.platform` is deprecated and still the only thing that answers
    // this in every browser Domino is opened in; a missing one reads as not-Mac, which is the
    // more explicit label of the two and safe to show a Mac user.
    //
    // ⇧ has no caller today, and the branch stays: the one ⇧ label in the Workbench is ⌘⇧N, held
    // back in Mac notation until somebody checks it on live Windows Chrome, which owns
    // Ctrl+Shift+N for a new incognito window. This is the half of that fix that is already ready.
    shortcut(label) {
      const platform = (window.navigator && window.navigator.platform) || '';
      if (/Mac|iPhone|iPad/.test(platform)) return label;
      return String(label)
        .replace(/⌘/g, 'Ctrl+')
        .replace(/⇧/g, 'Shift+')
        .replace(/⏎/g, 'Enter');
    },

    // Resolve a host-relative Domino path against the MAIN Domino host.
    //
    // The server hands these out with no host on purpose: DOMINO_API_HOST is the internal cluster
    // address, so the only browser-reachable host it can name is the one this page came from. That
    // is right in a Builder workspace, which is served from the main host. The published Workbench
    // App is served from apps.<host>, where the same path would resolve against the apps origin and
    // 404 — so the `apps.` label comes off first. Same rule as door.html's builderUrl().
    //
    // An absolute URL is somebody's deliberate override and passes through untouched.
    mainHostUrl(path) {
      if (!path || /^https?:\/\//i.test(path)) return path;
      const host = window.location.hostname;
      if (host.indexOf('apps.') === 0) {
        return `${window.location.protocol}//${host.slice(5)}${path}`;
      }
      return path;
    },

    // Which LLM Aliases can hold a conversation. Here rather than in either caller because both
    // the Chat picker and the model panel offer the same list, and two copies of this rule is how
    // they come to disagree about what a person may pick (ADR-0017).
    chatCapable(aliases) {
      return (aliases || []).filter((a) => {
        const caps = a.capabilities || [];
        return !(caps.includes('embeddings') && !caps.includes('chat'));
      });
    },

    // What every control that offers a one-click act does with a click: mark which button is
    // running so the row can show a spinner and disable its siblings, report the failure where the
    // person is looking, and let go either way. Here rather than beside one of them because two
    // surfaces now draw the same acts — the transcript's refusal card (#135) and the composer's
    // warning (#136) — and the copies were already at three when the second surface asked.
    // Returns `[busy, run]`: `busy` is the running button's key or '', `run(key, fn)` an onClick.
    useBusyAct() {
      const [busy, setBusy] = React.useState('');
      const run = (key, fn) => () => {
        setBusy(key);
        // `new Promise` and not `Promise.resolve(fn())`: that form calls `fn` BEFORE the chain
        // exists, so a synchronous throw goes straight past the catch — leaving `busy` set, every
        // button in the row disabled for good, and no sentence saying why. Two of the four mention
        // acts return synchronously, so the throw that does this has a caller.
        new Promise((resolve) => resolve(fn()))
          .catch((err) => antd.message.error(String(err.message || err)))
          .finally(() => setBusy(''));
      };
      return [busy, run];
    },

    iconFor(kind) {
      return (RESOURCE_META[kind] || RESOURCE_META.file).icon;
    },

    labelFor(kind) {
      return (RESOURCE_META[kind] || RESOURCE_META.file).label;
    },

    uiKind(kind) {
      if (kind === 'data_source') return 'datasource';
      if (kind === 'llm_alias') return 'model_llm';
      if (kind === 'model_api') return 'model_predictive';
      return kind || 'file';
    },

    // A Binding, in the id space everything else keys a Resource on.
    //
    // A Binding records the BARE Domino id beside its kind (`{kind: 'data_source', id: 'ds_1'}`),
    // while a Project Resource id and an attachment's `resourceId` both carry the kind as a prefix
    // (`data_source:ds_1`). So asking "is this row the thing the app is bound to" is a join, and a
    // join on `b.id` alone matches nothing — silently, and looking exactly like the empty state.
    //
    // Lives here because three surfaces now have to agree on it: the panel's "Required by {app}"
    // subtitle, its "In this app" rows, and the sentence `removeFromConversation` draws when a chip
    // leaves a Conversation the app still depends on. This is the same join `api.js` already makes
    // when it recovers a missing `resourceId` from `bindingKey.join(':')`.
    bindingId(binding) {
      return `${binding.kind}:${binding.id}`;
    },

    // What the Build header and the panel's app section both say when the selected app records
    // neither Bindings nor Attachments. One sentence in one place, because the two surfaces had
    // drifted into two answers to one question (ADR-0011) — the header's runs on from "{app}
    // ships" and the panel's stands alone, so only the lead differs.
    //
    // It names the handoff rather than an upload: `_promote_chat_file` writes the Attachment and
    // `_bind_from_handoff` the Bindings, while the composer's upload writes a scratch file and a
    // Conversation chip. A sentence naming the upload would leave a first-timer doing as they were
    // told and seeing neither list change.
    appScopeEmpty(lead) {
      return `${lead} Chat's resources and files land here after Open Builder.`;
    },

    // A Sage-managed upload: bytes Sage wrote under a Dataset's uploads/ or sensitive/ folder, and
    // so safe to destroy — mirrors the backend's own `_is_sage_upload` (service.py). A genuine
    // pre-existing Dataset file must never get this door (ADR-0023).
    isSageUpload(entry) {
      const rel = String((entry && entry.dataset_rel_path) || '');
      return Boolean(entry && (entry.source === 'upload' || rel.startsWith('uploads/')
        || rel.startsWith('sensitive/')));
    },

    // One of the selected app's Attachment records, as a working-set row. Three surfaces need the
    // same derivation since an Attachment stopped being listed under the Project (#148): the
    // panel's own "In {app} → Attachments" list, the composer's @ menu, and `collectTurnRefs` —
    // which has to turn the token the menu inserted back into the `public/data/…` path the turn
    // carries. A second copy is how the menu comes to offer a row the turn cannot resolve.
    //
    // `path` is the workspace-relative path and it is the load-bearing field: `_resolve_mentions`
    // keys the app's manifest on exactly that string. `file` is the path INSIDE the Dataset, so it
    // is read for the name only — the two agree on the basename and disagree on everything else.
    attachmentRow(entry) {
      const path = String((entry && entry.path) || '');
      return {
        id: `file:${path}`,
        name: String((entry && entry.file) || path).split('/').pop(),
        kind: 'file',
        path,
        // Where the bytes actually live. Keyed on `dataset_id` for the reason
        // `removeAttachmentFromApp` gives: a rehydrated entry still carries a `dataset`, filled
        // from the symlink's parent directory, and printing that as a Dataset name would name a
        // source the entry does not have.
        subtitle: entry && entry.dataset_id ? entry.dataset : path,
        // The row this file collapses into once there are more attachments than a menu of eight
        // can honestly show, or "" while the menu still shows files one by one (ADR-0030). The
        // SERVER decides it, by the function the `AGENTS.md` block already groups with: the
        // roll-up is a loop with a floor, and a second copy of it here is exactly how the block
        // the agent re-reads every turn and this menu would come to disagree.
        menuFolder: String((entry && entry.menu_folder) || ''),
        // How many files that row stands for — the FOLDER's size, not the query's. The collapse is
        // applied to the rows a query matched, so a count taken from those would read "3 files" on
        // a row whose pick carries twelve.
        menuFolderCount: Number((entry && entry.menu_folder_count) || 0),
      };
    },

    // The app's Attachments as the rows a person can PICK — the menu's question, and the turn's.
    // Not the same question as the panel's, which lists what the app RECORDS and so draws every
    // entry through the singular above.
    //
    // The filter is what makes the two differ, and it is the Project's old one kept rather than a
    // new rule: `isHiddenFromExplorer` matches on basename, so a Dataset file that happens to be
    // called `AGENTS.md` reaches `public/data/<slug>/AGENTS.md` and is hidden. It was hidden from
    // the @ menu before #148 because the Project's `file` group applied this filter; the app's own
    // panel row was never filtered and still is not. Same rows offered as before, from the other
    // list.
    attachmentRows(entries) {
      return (entries || [])
        .filter((e) => !SW.util.isHiddenFromExplorer(e && e.path))
        .map((e) => SW.util.attachmentRow(e));
    },

    // Above the threshold, the folder is the row (ADR-0030). The menu shows eight rows, so after a
    // 200-file attach it is a window onto a list that cannot be seen — and eight rows out of 200
    // read as a complete list, which is the menu misrepresenting itself.
    //
    // Applied to the rows a query already MATCHED, not to the whole list, which is what makes a
    // single file reachable by typing enough of its name: narrow to one file in a partition and
    // its group holds one, so it draws its own row. A group of one drawing its file is the
    // `AGENTS.md` block's rule as well, for the same reason — naming the file describes it exactly
    // as well as summarising would, and better.
    //
    // Rows carrying no folder are untouched and keep their order, so this is a no-op everywhere
    // the server did not collapse: below the threshold, and for every group that is not the app's
    // Attachments.
    collapseFolders(rows) {
      const held = {};
      // Every folder BETWEEN a row and its files. The roll-up level moves as the attachment count
      // does, so a token given for `raw/2026/01` names no row once the roll-up moves up to
      // `raw/2026` — and a matcher reading only today's path finds nothing, which is the silent
      // carry ADR-0030 rules out. These are the folders this row absorbed, so the token it was
      // given still reaches it and the turn says it widened.
      const absorbed = {};
      (rows || []).forEach((r) => {
        if (!r || !r.menuFolder) return;
        held[r.menuFolder] = (held[r.menuFolder] || 0) + 1;
        const under = String(r.path || '').slice(r.menuFolder.length + 1).split('/');
        let at = r.menuFolder;
        if (!absorbed[r.menuFolder]) absorbed[r.menuFolder] = [];
        under.slice(0, -1).forEach((segment) => {
          at += `/${segment}`;
          if (absorbed[r.menuFolder].indexOf(at) === -1) absorbed[r.menuFolder].push(at);
        });
      });
      const drawn = new Set();
      const out = [];
      (rows || []).forEach((row) => {
        const folder = (row && row.menuFolder) || '';
        if (!folder || held[folder] < 2) {
          out.push(row);
          return;
        }
        if (drawn.has(folder)) return;
        drawn.add(folder);
        // `held` decides WHETHER to collapse — a group of one still draws its file, whatever the
        // folder's size — and the row then reports the folder's size, because that is what the
        // pick carries.
        out.push(SW.util.folderRow(folder, row.menuFolderCount || held[folder],
                                   absorbed[folder]));
      });
      return out;
    },

    // One folder, as a row. `path` is the folder, because that is what `_resolve_mentions` expands
    // and what `mentionTokens` builds this row's tokens from — the same field a file row is
    // resolved through, so the turn needs no second rule to read a pick back.
    folderRow(folder, count, absorbed) {
      const path = String(folder || '');
      return {
        id: `folder:${path}`,
        name: path.split('/').pop(),
        kind: 'folder',
        path,
        count,
        subtitle: path,
        // Folders this row stands over, so a token given when one of THEM was the row still names
        // it. Read by `mentionTokens` only; nothing draws them.
        absorbed: absorbed || [],
      };
    },

    // Every row the app's Attachments can be offered as: its files, and the folders they collapse
    // into. The list `mentionToken` computes uniqueness against and the list `collectTurnRefs`
    // reads a token back off, because a token has to be unique among everything that could have
    // been GIVEN one — two partitions both called `2026` name the same folder row otherwise.
    attachmentPeers(entries) {
      const files = SW.util.attachmentRows(entries);
      return files.concat(SW.util.collapseFolders(files).filter((r) => r.kind === 'folder'));
    },

    // The shortest tail of `path` that names it and no other file in `peers` — "data.csv" while the
    // basename stands alone, "2026/data.csv" once a sibling partition holds one too (ADR-0030).
    // Whole-segment tails only, so what comes back is a path a person can read and type.
    //
    // Uniqueness is a question about a list, so it is asked only of a member of that list: a path
    // `peers` does not hold keeps its basename. That is deliberately narrow. An Upload and the
    // Attachment it crossed into (#147) say the same word ON PURPOSE, and computing this against
    // every row a menu can offer would rename one of them for a collision that is not this one.
    mentionSuffix(path, peers) {
      const full = String(path || '');
      const segments = full.split('/');
      const base = segments[segments.length - 1];
      const known = (peers || []).map((row) => String((row && row.path) || '')).filter(Boolean);
      if (!full || known.indexOf(full) === -1) return base;
      const rivals = known.filter((other) => other !== full);
      // Up to and INCLUDING the whole path, which is a candidate like any other: a root-level file
      // colliding with a nested one has no distinguishing tail at all, and the loop has to reach
      // that answer rather than be short of it by one and fall out with the basename unchecked.
      for (let take = 1; take <= segments.length; take += 1) {
        const suffix = segments.slice(segments.length - take).join('/');
        // A tail distinguishes only if no rival ENDS with it on a segment boundary: "data.csv" is
        // shared by "raw/2025/data.csv", and "6/data.csv" is not a tail anything has.
        if (!rivals.some((r) => r === suffix || r.endsWith('/' + suffix))) return suffix;
      }
      return full;
    },

    // The "@token" one row is named by. Prefer the file's basename so "@data.csv" matches the path
    // OpenCode reads. Lives here rather than in the composer because the menu that INSERTS a token
    // and the turn that reads it back off the prompt have to derive it the same way — a token only
    // one of them can produce is a mention that silently carries nothing (see store.collectTurnRefs).
    //
    // `peers` is the app's own Attachment list, and the token falls back to the shortest
    // distinguishing suffix when the basename is not unique in it (ADR-0030). This costs no rule
    // anybody has to learn, because the menu INSERTS the token: a person types "@", narrows, picks.
    // Called with no peers it is the basename it has always been.
    //
    // This is the WRITING half only. Reading a token back off a prompt is `mentionTokens` below,
    // which does not take peers at all — text already typed keeps the token it was given while the
    // peer list moves under it, so what a row would be called today is the wrong question there.
    mentionToken(resource, peers) {
      const path = String((resource && resource.path) || '');
      const fromPath = path ? SW.util.mentionSuffix(path, peers) : '';
      const fromName = String((resource && resource.name) || '').split('/').pop();
      return SW.util.mentionWord(fromPath || fromName || 'resource');
    },

    // "@" plus one path or name, spelled the way a token has to be spelled: whitespace collapsed to
    // "_" so the token is ONE word in the box, and a leading "@" dropped so a file called "@notes"
    // is not "@@notes". Its own function because every producer and every reader of a token has to
    // spell it identically — `_ambiguous_mentions` in `orchestrator/service.py` is the server's copy.
    mentionWord(text) {
      return '@' + String(text || '').replace(/\s+/g, '_').replace(/^@+/, '');
    },

    // Every token that could stand in the box for this row: the one the menu inserts today, and
    // every one it could have inserted before. Text already typed keeps the token it was GIVEN
    // while the peer list moves under it — a sibling is attached, a sibling is DETACHED, the
    // selected Built App changes mid-turn (#77). Matching only today's answer is how
    // `@2026/data.csv` comes to name nothing the day its twin is removed and `mentionToken`
    // collapses back to `@data.csv`: the same silent carry ADR-0030 rules out, running backwards.
    //
    // Whole-segment tails, because that is the whole set `mentionToken` can ever produce. A token
    // that names several rows names ALL of them, and the turn says so (`_ambiguous_mentions`).
    mentionTokens(resource) {
      const path = String((resource && resource.path) || '');
      if (!path) return [SW.util.mentionToken(resource)];
      // A file's path is fixed, so its own tails are the whole set. A folder ROW's is not — the
      // roll-up level moves as the attachment count crosses the threshold — so a folder row also
      // answers for the folders it absorbed, and the turn reports that it carried the wider one
      // (`_ambiguous_mentions`). Wider than what was asked for and said so, rather than silent.
      //
      // And the folders ABOVE this row, because the roll-up moves down as well as up: `@2024`
      // was a row, files leave, the menu shows files one by one, and without the parent tails
      // a file cannot answer `@2024`. Stop at `public/data/<slug>` inclusive — `_by_folder`'s
      // floor — so `@data` does not become a token of every Attachment.
      const paths = [path].concat((resource && resource.absorbed) || []);
      const parts = path.split('/').filter(Boolean);
      if (parts[0] === 'public' && parts[1] === 'data') {
        for (let n = parts.length - 1; n >= 3; n -= 1) {
          const folder = parts.slice(0, n).join('/');
          if (paths.indexOf(folder) === -1) paths.push(folder);
        }
      }
      const out = [];
      paths.forEach((each) => {
        const segments = String(each).split('/');
        for (let take = 1; take <= segments.length; take += 1) {
          const token = SW.util.mentionWord(segments.slice(segments.length - take).join('/'));
          if (out.indexOf(token) === -1) out.push(token);
        }
      });
      return out;
    },

    // Every "@token" standing in the text as its own word. ONE pass over the prompt, and the set
    // every reader of a token asks its question of — `collectTurnRefs` tests a few hundred candidate
    // tokens per keystroke, and a regex compiled per candidate is that cost multiplied by the depth
    // of every path.
    //
    // The trailing "." is the whole subtlety. A "." ends a sentence far more often than it begins a
    // suffix, so "@data.csv." has to be a mention of `data.csv` — but reading EVERY "." that way
    // makes "@report.csv" a mention of `report` as well, and an app holding both `report` and
    // `report.csv` then rides a turn with a file nobody named. So "." closes a token only when no
    // word character follows it.
    mentionTokensIn(text) {
      const found = new Set();
      const pattern = /(^|\s)@([^\s]+?)(?=[\s,;:!?)\]}'"]|\.(?!\w)|$)/g;
      let match = pattern.exec(String(text || ''));
      while (match) {
        found.add('@' + match[2]);
        match = pattern.exec(String(text || ''));
      }
      return found;
    },

    // Whether "@<token>" still stands in the text as its own word. Punctuation may follow it —
    // people write "@data.csv, please" — but a longer name must not match a shorter one's prefix.
    // Defined by the extractor above rather than beside it, because a second regex for the same
    // rule is how the one that INSERTS and the one that reads back come to disagree.
    mentionedIn(text, token) {
      const bare = String(token || '').replace(/^@+/, '');
      if (!bare) return false;
      return SW.util.mentionTokensIn(text).has(SW.util.mentionWord(bare));
    },

    // The part of a path a person is actually searching for. Two prefixes sit on paths all through
    // this menu and in nobody's head: `public/data/<slug>/`, where an Attachment is mounted
    // (`_attach_dest`), and `.sage/scratch/`, where a Chat upload lives. Searching them matches
    // EVERY row that carries one — on "data", on "public", on "sage" — and the menu shows eight, so
    // the row being hunted for is pushed off the list by the one thing it has in common with the
    // rest. Applied in `workingSetFirst` rather than stamped on a row, so it reaches every group
    // that has a path: the app's Attachments, the Project's Uploads, and the Conversation's chips,
    // which carry the mount path too.
    searchablePath(path) {
      const p = String(path || '').replace(/^\.\//, '');
      if (p.startsWith('.sage/scratch/')) return p.slice('.sage/scratch/'.length);
      const parts = p.split('/');
      if (parts.length > 3 && parts[0] === 'public' && parts[1] === 'data') {
        return parts.slice(3).join('/');
      }
      return p;
    },

    // The Chat explorer is the project's pickable working set, not the repo.
    isHiddenFromExplorer(path) {
      const p = String(path || '').replace(/^\.\//, '');
      if (!p) return false;
      const base = p.split('/').pop();
      if (base === 'AGENTS.md') return true;
      if (p === '.sage/scratch' || p.startsWith('.sage/scratch/')) return false;
      if (p === '.sage' || p.startsWith('.sage/')) return true;
      return false;
    },

    // The order a menu offers a Resource in: what this Project already holds, then the wider Domino
    // catalogue behind it. `groups` is the working set's own groups in the caller's order, and
    // `catalogue` is what the Project has not joined — always LAST, because it is the only group
    // whose rows are not here already, and picking one joins the Project on the way in (ADR-0018).
    //
    // Shared because two menus draw the same choice and one of them had it inline: the composer's @
    // menu and the Build header's picker (ADR-0021). The picker was written from the menu's shape,
    // and a second copy of the ordering is how the two come to disagree about where a Resource is —
    // which is the one thing a person carries from one surface to the other.
    //
    // One row per id, the first occurrence winning, so a Resource two groups both hold is offered by
    // the nearer one. `query` matches the name or the file's whole relative path, because the path
    // is what `mentionToken` builds a token from once a basename collides (ADR-0030) — asking the
    // same question of the same string keeps what a person typed and what the menu keeps from
    // drifting apart. On the basename alone, "2026" and "raw/2026" reached nothing for
    // `raw/2026/data.csv`, which left the one file a person could see two rows of as the one file
    // they could not narrow to. Through `searchablePath`, because the mount prefixes these paths
    // share are the one part of them nobody is searching for.
    //
    // The widening is contained, which is worth recording since this helper is shared on purpose:
    // the Build header's picker passes NO query (`modes/builder.js`), and the matcher returns true
    // on an empty one, so it reaches the composer's @ menu and nothing else.
    workingSetFirst({ groups, catalogue, query, limit, collapse }) {
      const lowered = String(query || '').trim().toLowerCase();
      const matches = (row) => {
        if (!lowered) return true;
        const name = String(row.name || '').toLowerCase();
        const path = SW.util.searchablePath(row.path).toLowerCase();
        return name.includes(lowered) || path.includes(lowered);
      };
      const seen = new Set();
      const out = [];
      [...(groups || []), catalogue || []].forEach((group) => {
        (group || []).forEach((row) => {
          if (!row || !row.id || seen.has(row.id) || !matches(row)) return;
          seen.add(row.id);
          out.push(row);
        });
      });
      // Between the matcher and the limit, which is the only place it can go: collapsing before
      // the match would hide the file a person is typing towards, and after the limit it would
      // fold eight arbitrary rows out of two hundred. Opt-in, because only the composer's @ menu
      // draws a folder — the Build header's picker offers Bindings, which have no path at all.
      const rows = collapse ? SW.util.collapseFolders(out) : out;
      return limit ? rows.slice(0, limit) : rows;
    },

    // Which level of a Data Source a walk is standing on, given the levels it HAS and the ones
    // already answered. Not every store has three: a connector with no database level opens on its
    // schemas, and one Domino pins a `default_database` for opens one rung down again.
    //
    // Shared because two surfaces walk the same ladder for two different reasons since #142 — the
    // Resource Browser's cascade, which walks it to look, and the Build header's Scope door, which
    // walks it to choose (ADR-0021). Same ladder, and a second copy of "what is a stage" is how the
    // two come to disagree about which level a person is on.
    cascadeStage(levels, database, schema) {
      const has = (name) => (levels || []).includes(name);
      if (has('database') && !database) return 'database';
      if (has('schema') && !schema) return 'schema';
      return 'table';
    },

    // Whether this kind of record HAS a Scope at all. One Resource kind does, and four surfaces ask
    // — the panel's row, the header's strip, its door, and the bind's receipt — so the fact lives
    // here rather than as four `=== 'data_source'` comparisons that have to be found together to be
    // changed together. Takes the kind, because one caller has a Binding and one has a binding key.
    recordsScope(kind) {
      return kind === 'data_source';
    },

    // What a Binding that records a Scope and has none is CALLED (#142). One word for one named
    // state, written once: the panel's row and the header's door both say it, and two casings of it
    // would read as two different states — which is the opposite of what naming it is for. Lower
    // case because it follows a name on the header's strip and sits among lower-case kind words in
    // the panel, and because it is a state rather than a title.
    NO_SCOPE_YET: 'not scoped yet',

    // A Scope as one dotted label, or "" for a record that has none. The join `Binding.scope` does
    // on the server, so a row, a tooltip and a receipt all name a Scope the way the AGENTS.md data
    // block names it. Reads any record carrying the three levels — a Binding, or the position a
    // walk is standing on.
    scopeText(record) {
      const at = record || {};
      return [at.database, at.schema, at.table].filter(Boolean).join('.');
    },

    thumbUrl(name) {
      return `./img/thumbs/${name || 'thumb-dashboard.svg'}`;
    },

    // Formatting ---------------------------------------------------------

    money(value, digits) {
      const decimals = digits === undefined ? 2 : digits;
      return `$${Number(value || 0).toLocaleString('en-US', {
        minimumFractionDigits: decimals,
        maximumFractionDigits: decimals,
      })}`;
    },

    compactMoney(value) {
      const n = Number(value || 0);
      if (n >= 1000000) return `$${(n / 1000000).toFixed(1)}M`;
      if (n >= 1000) return `$${(n / 1000).toFixed(1)}K`;
      return `$${n.toFixed(2)}`;
    },

    number(value) {
      return Number(value || 0).toLocaleString('en-US');
    },

    // A size a person reads. `human_bytes` in `sage/orchestrator/describe.py`, digit for digit:
    // the Dataset tree shows what a folder weighs BEFORE the click and the server's refusal names
    // the same number after it (ADR-0029), so a mismatch here would read as two different folders.
    bytes(value) {
      const units = ['B', 'KB', 'MB', 'GB'];
      let n = Number(value || 0);
      let unit = 0;
      while (n >= 1024 && unit < units.length - 1) {
        n /= 1024;
        unit += 1;
      }
      return unit === 0 ? `${Math.round(n)} B` : `${n.toFixed(1)} ${units[unit]}`;
    },

    compactNumber(value) {
      const n = Number(value || 0);
      if (n >= 1000000000) return `${(n / 1000000000).toFixed(1)}B`;
      if (n >= 1000000) return `${(n / 1000000).toFixed(1)}M`;
      if (n >= 1000) return `${(n / 1000).toFixed(1)}K`;
      return String(n);
    },

    percent(value, digits) {
      return `${Number(value || 0).toFixed(digits === undefined ? 0 : digits)}%`;
    },

    // "2 hours ago" inside 7 days, an absolute date beyond it — per the
    // Domino writing guidelines.
    relativeTime(iso) {
      if (!iso) return '';
      const then = new Date(iso);
      const diffMs = TODAY - then;
      const minutes = Math.round(diffMs / 60000);
      if (minutes < 1) return 'just now';
      if (minutes < 60) return `${minutes} min ago`;
      const hours = Math.round(minutes / 60);
      if (hours < 24) return `${hours} ${hours === 1 ? 'hour' : 'hours'} ago`;
      const days = Math.round(hours / 24);
      if (days === 1) return 'yesterday';
      if (days < 7) return `${days} days ago`;
      return then.toLocaleDateString('en-US', { month: 'long', day: 'numeric', year: 'numeric' });
    },

    shortDate(iso) {
      if (!iso) return '';
      return new Date(iso).toLocaleDateString('en-US', { month: 'short', day: 'numeric' });
    },

    isoDaysAgo(days) {
      const d = new Date(TODAY);
      d.setDate(d.getDate() - days);
      return d.toISOString().slice(0, 10);
    },

    // Groups a thread list the way the rail displays it.
    groupThreads(threads) {
      const buckets = [
        { key: 'pinned', label: 'Pinned', items: [] },
        { key: 'today', label: 'Today', items: [] },
        { key: 'week', label: 'Previous 7 days', items: [] },
        { key: 'month', label: 'Previous 30 days', items: [] },
        { key: 'older', label: 'Older', items: [] },
      ];
      threads.forEach((thread) => {
        if (thread.pinned) return buckets[0].items.push(thread);
        const days = (TODAY - new Date(thread.updatedAt)) / 86400000;
        if (days < 1) return buckets[1].items.push(thread);
        if (days < 7) return buckets[2].items.push(thread);
        if (days < 30) return buckets[3].items.push(thread);
        return buckets[4].items.push(thread);
      });
      return buckets.filter((b) => b.items.length);
    },

    // What Chat says it is doing while it is doing it. The server names the work, because which
    // bash command is a Data Source query is Sage's business; the sentence is the UI's. Nothing
    // here is kept — the Thread keeps the chart and the answer, not a tool log.
    activityLabel(ev) {
      const name = String(ev.detail || '').split('/').pop();
      switch (ev.doing) {
        case 'read': return name ? `Reading ${name}…` : 'Reading the data…';
        case 'query': return name ? `Querying ${name}…` : 'Running the query…';
        case 'write': return name ? `Saving ${name}…` : 'Saving the results…';
        case 'bash': return 'Running Python…';
        case 'idle': return 'Thinking…';
        // No `doing` at all is the transcript fallback, which only ever names bash.
        default: return ev.tool === 'bash' ? 'Running Python…' : 'Thinking…';
      }
    },

    // Lightweight markdown — bold, inline code, lists, paragraphs. Enough for
    // scripted assistant copy without pulling in a parser.
    markdown(text) {
      if (!text) return null;
      const blocks = String(text).split(/\n\n+/);
      return blocks.map((block, blockIndex) => {
        const lines = block.split('\n');
        if (/^#{1,6}\s/.test(lines[0])) {
          const title = h(
            'p',
            { key: `${blockIndex}-h`, style: { fontWeight: 600, margin: '8px 0 4px' } },
            SW.util.inline(lines[0].replace(/^#{1,6}\s+/, ''))
          );
          const rest = lines.slice(1).join('\n').trim();
          if (!rest) return title;
          return h('div', { key: blockIndex }, title, SW.util.markdown(rest));
        }
        // A pipe table. Without this it falls through to a paragraph, where the newlines
        // collapse and the whole table lands as one run of pipes — worse than plain prose,
        // and what the reader sees is the product looking broken.
        // The header needs a pipe and the second line has to be the |---|---| rule: outer pipes
        // are optional both ways, since a model writes the table either way. Requiring a pipe in
        // the rule too is what keeps a paragraph followed by a --- underline out of here.
        const isRule = (l) => /^[\s:|-]+$/.test(l) && l.includes('-') && l.includes('|');
        if (lines.length >= 2 && lines[0].includes('|') && isRule(lines[1])) {
          const cells = (line) => line.trim().replace(/^\||\|$/g, '').split('|').map((c) => c.trim());
          const head = cells(lines[0]);
          return h(
            'div',
            { key: blockIndex, className: 'sw-md-table-wrap' },
            h(
              'table',
              { className: 'sw-md-table' },
              h('thead', null, h('tr', null, head.map((c, i) => h('th', { key: i }, SW.util.inline(c))))),
              h('tbody', null, lines.slice(2).map((line, r) =>
                h('tr', { key: r }, cells(line).map((c, i) => h('td', { key: i }, SW.util.inline(c))))))
            )
          );
        }
        const isOrdered = lines.length > 0 && lines.every((l) => /^\s*\d+\.\s/.test(l));
        const isBullet = lines.length > 0 && lines.every((l) => /^\s*[-*]\s/.test(l));

        if (isOrdered || isBullet) {
          const tag = isOrdered ? 'ol' : 'ul';
          return h(
            tag,
            { key: blockIndex },
            lines.map((line, i) =>
              h('li', { key: i }, SW.util.inline(line.replace(/^\s*(\d+\.|[-*])\s/, '')))
            )
          );
        }
        return h('p', { key: blockIndex }, SW.util.inline(block));
      });
    },

    inline(text) {
      const parts = String(text).split(/(\*\*[^*]+\*\*|`[^`]+`)/g);
      return parts.filter(Boolean).map((part, i) => {
        if (part.startsWith('**') && part.endsWith('**')) {
          return h('strong', { key: i }, part.slice(2, -2));
        }
        if (part.startsWith('`') && part.endsWith('`')) {
          return h('code', { key: i }, part.slice(1, -1));
        }
        return part;
      });
    },

    // Very small tokenizer so the read-only code view is not a wall of text.
    highlight(code, language) {
      const keywords = /\b(import|from|export|default|function|const|let|var|return|if|else|for|while|async|await|new|class|extends|type|interface|try|catch|throw|SELECT|FROM|WHERE|JOIN|ON|GROUP BY|ORDER BY|WITH|AS|SUM|MAX|SQRT|POWER)\b/g;
      const lines = String(code).split('\n');
      return lines.map((line, lineIndex) => {
        const trimmed = line.trimStart();
        const isComment =
          trimmed.startsWith('//') ||
          trimmed.startsWith('#') ||
          trimmed.startsWith('*') ||
          trimmed.startsWith('/*') ||
          trimmed.startsWith('--');
        if (isComment || language === 'markdown') {
          return h('div', { key: lineIndex, className: isComment ? 'sw-tok-com' : undefined }, line || ' ');
        }
        const segments = line.split(/('[^']*'|"[^"]*")/g);
        return h(
          'div',
          { key: lineIndex },
          segments.filter((s) => s !== undefined).map((segment, i) => {
            if (/^['"]/.test(segment)) return h('span', { key: i, className: 'sw-tok-str' }, segment);
            const words = segment.split(keywords);
            return words.map((word, j) =>
              keywords.test(word) && word.match(keywords)
                ? h('span', { key: `${i}-${j}`, className: 'sw-tok-key' }, word)
                : word
            );
          })
        );
      });
    },

    initialsOf(name) {
      return String(name || '?')
        .split(' ')
        .map((w) => w[0])
        .slice(0, 2)
        .join('')
        .toUpperCase();
    },

    copy(text, label) {
      const done = () => antd.message.success(label || 'Copied');
      if (navigator.clipboard && navigator.clipboard.writeText) {
        navigator.clipboard.writeText(text).then(done, () => antd.message.error('Copy failed'));
      } else {
        const area = document.createElement('textarea');
        area.value = text;
        document.body.appendChild(area);
        area.select();
        document.execCommand('copy');
        document.body.removeChild(area);
        done();
      }
    },

    sleep(ms) {
      return new Promise((resolve) => setTimeout(resolve, ms));
    },

    detectOs() {
      const ua = navigator.userAgent;
      if (/Win/i.test(ua)) return 'windows';
      if (/Linux|X11/i.test(ua) && !/Android/i.test(ua)) return 'linux';
      return 'macos';
    },
  };

  // Small shared presentational pieces used across modes ------------------

  SW.Avatar = function Avatar({ user, size = 24 }) {
    const dimension = { width: size, height: size, fontSize: Math.round(size * 0.42) };
    return h(
      'span',
      {
        className: 'sw-avatar',
        style: { ...dimension, background: (user && user.color) || '#8F8FA3' },
        title: user && user.name,
      },
      (user && user.initials) || SW.util.initialsOf(user && user.name)
    );
  };

  SW.ProvenanceBadge = function ProvenanceBadge({ addedBy, rationale }) {
    if (addedBy !== 'sage') {
      return h(
        antd.Tooltip,
        { title: 'You chose this.' },
        h(antd.Tag, { className: 'sw-prov sw-prov-you', bordered: false }, 'you')
      );
    }
    return h(
      antd.Tooltip,
      { title: rationale || `Picked for you by ${SW.brand.assistant()}.` },
      h(antd.Tag, { className: 'sw-prov sw-prov-sage', bordered: false }, SW.brand.assistant())
    );
  };

  SW.PlanStatusTag = function PlanStatusTag({ status }) {
    const meta = PLAN_STATUS[status] || PLAN_STATUS.draft;
    const props = { color: meta.color === 'default' ? undefined : meta.color, bordered: false };
    if (status === 'building') {
      return h(antd.Tag, props, h(antd.Space, { size: 4 }, h(antd.Spin, { size: 'small' }), meta.label));
    }
    return h(antd.Tag, props, meta.label);
  };

  SW.StatusDot = function StatusDot({ status }) {
    return h('span', { className: `sw-dot sw-dot-${status || 'draft'}` });
  };

  SW.Sparkline = function Sparkline({ data, width = 64, height = 18, color = '#543FDE' }) {
    const values = (data && data.length ? data : [0]).slice(-14);
    const max = Math.max(...values, 1);
    const step = values.length > 1 ? width / (values.length - 1) : width;
    const points = values
      .map((v, i) => `${(i * step).toFixed(1)},${(height - (v / max) * (height - 2) - 1).toFixed(1)}`)
      .join(' ');
    return h(
      'svg',
      { className: 'sw-sparkline', width, height, viewBox: `0 0 ${width} ${height}`, 'aria-hidden': true },
      h('polyline', {
        points,
        fill: 'none',
        stroke: color,
        strokeWidth: 1.5,
        strokeLinejoin: 'round',
        strokeLinecap: 'round',
      })
    );
  };
})();
