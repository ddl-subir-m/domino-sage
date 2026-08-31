window.SW = window.SW || {};

(function () {
  // Until /project answers, the chip has nothing real to name. Not a sandbox: scratch is not
  // offered as a Project any more (ADR-0004) — every Thread lives in a git-backed sage-* Project.
  const NO_SCOPE = {
    id: '',
    name: 'Default',
    color: '#543FDE',
    appCount: 0,
    planCount: 0,
    memberCount: 1,
  };

  // The pack's documented defaults (docs/workbench/brand.md), which are Domino's. The shell paints
  // before /api/brand answers, so the Workbench carries them — but as ONE copy: this is both what
  // the store opens on and what an absent key falls back to when a token is resolved. A second
  // table beside the accessors would be the same fact in two places, drifting apart silently.
  //
  // A noun carries both forms — `{dataset}` and `{datasetPlural}` — because a plural is read from
  // the pack, never derived. Copy that would need `a`/`an` is reworded instead.
  const BRAND_DEFAULT = {
    productName: 'AI Workbench',
    assistantName: 'Sage',
    platformName: 'Domino',
    pageTitle: 'Sage Workspace',
    logoUrl: './img/domino-logo.svg',
    logoAlt: 'Domino',
    nouns: {
      dataset: { singular: 'Dataset', plural: 'Datasets' },
      dataSource: { singular: 'Data Source', plural: 'Data Sources' },
      modelApi: { singular: 'Model API', plural: 'Model APIs' },
      llmAlias: { singular: 'LLM Alias', plural: 'LLM Aliases' },
      builtApp: { singular: 'Built App', plural: 'Built Apps' },
      gallery: { singular: 'Gallery', plural: 'Galleries' },
    },
    colors: {
      primary: '#543FDE',
      primaryDark: '#311EAE',
      primaryLight: '#EEEBFC',
    },
  };

  const state = {
    ready: false,
    me: null,
    brand: BRAND_DEFAULT,
    projects: [],
    // Whether this container can create or attach Projects at all (Domino + a git host).
    // False on a laptop run, where New project has nothing to create against.
    canProvision: false,
    scope: NO_SCOPE,
    scopeFlash: false,

    // Dock
    dockTab: null,        // null = collapsed
    panelFilter: null,    // resource kind the assistant asked the user to pick
    railHidden: false,
    railAppFilter: null,  // show only conversations that changed this app
    previewResourceId: null,

    // The catalogue picker. Browsing the platform is a deliberate, occasional
    // act, so it gets a surface you open and close rather than a column you
    // live beside.
    catalogOpen: false,
    catalogKind: null,

    // Overlays
    handoffOpen: false,
    handoffDraft: null,
    graduationOpen: false,
    inviteOpen: false,
    paletteOpen: false,
    scopePickerOpen: false,
    helpOpen: false,
    settingsOpen: false,

    // Project-scoped data
    resourceGroups: {},
    resourceErrors: {},
    resourceIndex: {},
    // Every Dataset this container has mounted writable, whether or not it is in the project rail.
    // Membership is a curated list; the promote target is a fact about the disk.
    datasetTargets: [],
    // The catalogue parents this viewer can reach that are NOT in the project rail. The @ menu
    // offers them last, because picking one joins the project on the way in (see `attach`).
    catalogueParents: [],
    gatewayAliases: [],
    resourcesLoading: true,
    members: [],
    directory: [],
    userIndex: {},
    activity: [],
    charts: {},
    starters: null,
    notifications: [],

    // Chat
    threads: [],
    thread: null,
    messages: [],
    typing: null,
    // "New conversation" was pressed and nothing has been said yet. There is no Thread behind
    // this and there is not going to be one until the first message — it exists so the rail can
    // draw a row for the conversation you are looking at, the same way the centre pane already
    // draws its turns. Read with `!thread`, never alone: once a real conversation opens, that is
    // the row, and this one has nothing left to stand for.
    pendingConversation: false,
    // A turn is running somewhere in this project. Not "in this conversation": one project runs
    // one turn at a time (the server's turn lock), so a Chat turn, a Build turn and a turn another
    // tab started are all the same fact here. It no longer decides whether Chat can SEND — a second
    // question is queued now rather than refused (#79) — so what is left is whether to show the
    // turn bar and offer Stop.
    chatRunning: false,
    // The conversation whose Chat turn THIS tab is streaming, or null. Not the same question as
    // `chatRunning`: a turn started in another tab, or before a reload, is running with nobody here
    // reading it. Knowing which lets the composer say whether the turn holding it up is the one on
    // screen — "wait" and "wait, over there" send someone to different places.
    chatTurnThread: null,
    // WHICH turn holds the lock, as `{kind, conversation}` (#126). `chatRunning` and `buildRunning`
    // above answer "is the Project busy" and are read by the controls that must not fire during
    // ANY turn — Reset app, the app switcher, the model override. This answers the narrower
    // question only the Stop bars ask: is the turn holding the lock the one on this screen. Null
    // when what holds it is not a turn you can Stop — a wedge, or a publish or reset that took the
    // lock without ever queueing.
    runningTurn: null,
    // Turns this tab has asked for that have not started yet (#79). A pending turn is an INTENTION,
    // not a commitment: nothing of it has run, nothing of it is on the server's disk, and Cancel
    // drops it without touching whatever is running. `{ ticket, text, message }`, oldest first —
    // which is also the order they will run in, because the queue drains by when it was asked.
    //
    // This tab's own, not the project's. A turn queued in another tab is somebody else's held
    // connection and there is nothing here to cancel it with; `turnPending` is how many the server
    // is holding altogether.
    queuedTurns: [],
    turnPending: 0,
    // The workspace is wedged on a turn that would not stop (#39), and only a restart clears it.
    // The one state where the composer is still disabled: a queue cannot form behind a lock that is
    // never coming back, so every send would be refused. `build/state` reports it (#79) — it used
    // to be visible only in the refusal a send came back with, and under a queue that send waits
    // instead of coming back.
    turnWedged: false,
    pendingTurn: null,
    scriptMeta: { planTemplate: 'tpl_generic' },
    assistantTurns: 0,
    nudgeDismissed: false,

    // Context has an owner. `attachments` is this conversation's — disposable,
    // and nobody else's, rendered as chips in the composer and nowhere else.
    // What the previewed app durably depends on is `bindings`, which is the
    // app's and is read per app.
    attachments: [],
    // The Built Apps in this Project, oldest first, and the one Build is pointed at. The Build
    // rail's list, where Chat's is `threads` — a Project holds many of each and neither is the
    // other (ADR-0008).
    apps: [],
    activeApp: null,
    activePlanId: null,
    activePlan: null,
    // plan.md, for the rail's pin. Not the plan document above.
    projectPlan: null,

    // Apps this conversation changed, newest first. Drives the change dots on
    // the app selector and the tags in the rail.
    touched: [],

    // Artifacts — plans and outputs the conversation produced. The plan viewer
    // opens beside the work rather than taking over the main area.
    planViewerId: null,

    // Composer
    model: '',
    catalogAsk: '',
    reasoningEffort: null,
    phase: 'planning',
    // Build agent mode (Auto / Ask / Plan / Implement). Distinct from the
    // prototype model picker above. `buildMode` is the picker's standing
    // choice; `buildTurnMode` is what the in-flight turn is pinned to.
    buildMode: 'auto',
    buildTurnMode: 'auto',
    // The pinned model for each slot, and the standing override on top of it. Build's picker draws
    // both: the slot the current mode routes to is the "(default)", and `buildModel` is the user's
    // choice against it. Empty means no override, which is what the pin is for.
    catalog: null,
    buildModel: '',
    buildPhase: 'plan',
    // Only ever non-empty on an `openai` gateway, where /healthz names the open-weight models this
    // deployment will accept beyond the four configured slots. On Domino it is [], and the picker
    // is the four slots alone.
    openWeightModels: [],
    // The model panel (ADR-0017). Its own fetch rather than a slice of `status`, which is polled:
    // it costs a gateway listing and an endpoint listing, and the drawer is almost always closed.
    assignmentsOpen: false,
    assignmentsLoading: false,
    // { slots: [{slot, model, default, assigned}], aliases: [{name, display_name, capabilities,
    // serving, problem}], error }. Null until first opened.
    assignments: null,
    // Why the Alias list is missing, when it is. The panel stays open and read-only on this rather
    // than falling back to the models already assigned — a list that can only offer what is already
    // chosen cannot express a change.
    assignmentsError: '',

    // Build is the project's history.jsonl, not the Chat Thread. Chat ↔ Build
    // is turning your head: the Thread stays selected, this transcript is the app's.
    buildHistory: [],
    buildMessages: [],
    // The Conversation's Chat turns, for Build to draw above its own (#57). Empty under the split
    // view, which is what keeps that arm the screen it is today. Kept apart from `buildMessages`
    // rather than folded into it because the greeting asks a question only `buildMessages` can
    // answer: has THIS app got turns yet?
    conversationChat: [],
    // The two above, in the order they happened — what Build actually renders.
    buildTranscript: [],
    buildTyping: null,
    buildRunning: false,
    bindings: [],
    // The selected app's own files, which is NOT `attachments` above: that list is the
    // Conversation's and must not follow the app (#84). This one is read off the app's own
    // manifest, so switching app replaces it — see `loadBuild` (#92).
    appAttachments: [],
    // What the last app-scoped removal reported, drawn as a notice inside the section that did it.
    // `{ text, prompt }` — `prompt` is null when the app's code refers to nothing that went, and a
    // notice with nothing to act on carries no offer. Never a toast: five seconds is not long
    // enough to read a file list and decide (ADR-0011).
    appRemoval: null,
    // The selected app's own build log, as last read (#88). `null` until it has been read for this
    // app; `{ rows, failed }` after — THREE states, not two, for the reason `loadAppList` gives
    // above about `apps()`: a read that failed is not an app with no builds, and `[]` cannot tell
    // the two apart. An empty list is a sentence about the app; a failed read is a sentence about
    // the read, and only one of them is true when the route 500s.
    //
    // NOT `buildHistory` above: that one is this Conversation's turns in this app, which is what
    // Build replays. This is every build of the app, whoever asked for it and in whichever
    // Conversation — the log as the app's directory holds it (ADR-0008).
    //
    // Read only when something asks to see it, and dropped the moment the selection moves, so the
    // list can never be one app's builds under another app's name.
    appHistory: null,
    // Whether the build history is on screen. The person's, not the app's — which is why it is not
    // in `APP_SCOPED` below, for the reason `composerSeed` is not: switching app changes WHICH
    // builds are listed, never whether you had asked to see them.
    buildHistoryOpen: false,
    // A prompt written into the composer and left there. The cleanup offer above puts work in front
    // of the person rather than firing a build turn, which the per-project turn lock can refuse and
    // which would put work past a plan gate they never read.
    composerSeed: null,
    previewSrc: './preview/',
    previewStatus: 'idle',
  };

  const listeners = new Set();
  function notify() {
    listeners.forEach((fn) => fn(state));
  }

  // A conversation owns its context, always — before a plan exists, before an
  // app exists. There is no fallback to guess an owner from, which is what let
  // one conversation report different context in Chat and Build.
  function conversationId() {
    return (state.thread && state.thread.id) || null;
  }

  function applyModelStatus(status) {
    const m = (status && status.model) || status;
    if (!m) return;
    state.buildMode = m.selected_mode || m.mode || state.buildMode;
    state.buildTurnMode = m.mode || state.buildTurnMode;
    // Kept whole, not reduced to `ask`. Build's picker offers every slot and marks the one the
    // current mode is pinned to, so a status that discarded the rest left the picker with nothing
    // to draw — which is why Build had no picker at all.
    if (m.catalog) {
      state.catalog = m.catalog;
      if (m.catalog.ask) state.catalogAsk = m.catalog.ask;
    }
    if ('picked_model' in m) state.buildModel = m.picked_model || '';
    if (m.phase) state.buildPhase = m.phase;
    if ('chat_model' in m || m.chat_model === null) {
      state.model = m.chat_model || '';
    }
    if ('reasoning_effort' in m) state.reasoningEffort = m.reasoning_effort || null;
  }

  function indexResources(groups) {
    const index = {};
    Object.values(groups || {}).forEach((list) => list.forEach((r) => { index[r.id] = r; }));
    return index;
  }

  let scopeLoad = 0;
  // Which open is the current one. `selectApp` guards with `selecting`, which makes a second
  // asker bail — right there, because you are already going where it asked. Wrong here: clicking
  // A after B must land on A, not be ignored. So this is a generation counter like `scopeLoad`,
  // and a superseded open drops its answer instead of writing it.
  let openSeq = 0;

  // ---------------------------------------------------------------------
  // App-scoped state, sequenced (#101)
  // ---------------------------------------------------------------------
  //
  // `activeApp`, `bindings`, `appAttachments` and `appRemoval` describe ONE app between them, and
  // at `ee24c31` eleven functions wrote them: `loadScopeData`, `loadAppList`, `refreshAppScope`,
  // `refreshBindings` and `loadBuild` reading them; the two removals and `reportRemoval` under them
  // writing what an act returned; and `setScope`, `clearApp` and `dismissAppRemoval` putting one
  // down by hand. The async ones read in parallel and none knew about the others, so whichever
  // RESOLVED last won — which is not the same as whichever STARTED last. A read taken under the app
  // you left could land on top of a fresh one and print the old app's records under the new app's
  // name: the wrong pairing #95 fixed, arrived at by timing rather than by a missing refresh.
  //
  // Ten of the eleven now go through `applyAppScope` and nothing assigns these fields directly.
  // (`reportRemoval` is the eleventh; it returns its notice for its caller to install, as
  // `removalNotice`.) The shared gate is the half of the fix a counter cannot do — one local to
  // `refreshAppScope` would never see `loadBuild`'s `refreshBindings`, and the stale write would
  // still land.
  let appScopeSeq = 0;
  // Which app the state describes. Moved by the selection moving, and checked only by the acts
  // below — a read is ordered by when it started, and holding it to a generation as well would
  // drop the newest read of all whenever a slower one happened to move the selection first.
  let appGen = 0;

  // The fields the gate covers, named rather than spread blind: a writer handing over a key that is
  // not on this list is writing something else, and a silent new key on `state` is how that would
  // go unnoticed. `composerSeed` is deliberately absent — it is a draft handed to the composer and
  // cleared on read, and it belongs to the person rather than to the app. `buildHistoryOpen` is
  // absent for the same reason: it says whether somebody asked to see the builds, not whose.
  //
  // `appHistory` is here because it is the same kind of thing as the four before it (#88): a list
  // read per app, over a route that carries no app id, which means a read taken under the app you
  // left can land after you have moved and print that app's builds under this one's name. That is
  // #101's bug arriving through a new door, and this list is the door.
  const APP_SCOPED = ['activeApp', 'bindings', 'appAttachments', 'appRemoval', 'appHistory'];

  // Where each field's newest write got to, PER FIELD rather than one number for all four. Sharing
  // one would make every writer supersede every other: the 2s build tick calls `loadAppList` and
  // writes `activeApp` alone, and it must not throw away a `/bindings` read still in flight for the
  // same app — nor must clicking Dismiss, which writes only the notice.
  const appScopeApplied = {};
  APP_SCOPED.forEach((key) => { appScopeApplied[key] = 0; });

  // A place in the queue. A read takes its ticket where it ISSUES its requests and carries that one
  // ticket through whatever chain installs the answer, so the number says when what it carries was
  // true and the newer of two reads wins however the two resolve.
  //
  // An ACT takes its ticket where its ROUTE ANSWERS instead, passing the generation it was issued
  // under. The server has just written the list the route hands back, so it is newer than any read
  // in flight and must not lose to one that started first and would put back what has just gone;
  // claiming last puts it at the head of the queue, where its start position would have left it
  // behind. That costs it the sequence's protection against an app switch, which is what the
  // generation buys back: the list is right, but by then it can be another app's list.
  function appScopeTicket(gen = null) {
    return { seq: ++appScopeSeq, gen };
  }

  // Whether this ticket is still the newest word on `key`. Asked before a cascade as well as at the
  // write, so a tick that has already lost costs the one read it had made and stops there.
  function appScopeCurrent(ticket, key) {
    return ticket.seq >= appScopeApplied[key] && (ticket.gen === null || ticket.gen === appGen);
  }

  // Install what a ticket carries, field by field. One pass with no await in it, so no render
  // catches it half-applied (#95) — and a field some newer write already owns is left where it is
  // rather than rolled back to this one.
  function applyAppScope(ticket, fields) {
    for (const key of APP_SCOPED) {
      if (!(key in fields) || !appScopeCurrent(ticket, key)) continue;
      appScopeApplied[key] = ticket.seq;
      // `activeApp` comes first in the list, so a selection that moves settles the notice before
      // the loop reaches it.
      if (key === 'activeApp'
          && (fields.activeApp && fields.activeApp.id)
             !== (state.activeApp && state.activeApp.id)) {
        appGen += 1;
        // The notice reports one act on one app's lists, so the selection moving is what makes it
        // another app's. Cleared here rather than by the paths that move the selection, because
        // three of those four never did: `refreshAppScope` cleared it by hand, and `loadAppList`'s
        // non-cascading branch — the one `loadBuild`, and so every app switch made by hand, goes
        // down — did not, nor did `clearApp` or `setScope`.
        state.appRemoval = null;
        appScopeApplied.appRemoval = ticket.seq;
        // The builds listed are the app's, so the selection moving is what makes them somebody
        // else's (#88). Dropped rather than left up: a list that stayed would be the wrong pairing
        // #95 fixed, printed as prompts under a header naming a different app. Claiming this
        // ticket is also what makes a read still in flight for the app you left lose to the switch,
        // whenever it lands.
        state.appHistory = null;
        appScopeApplied.appHistory = ticket.seq;
      }
      state[key] = fields[key];
    }
  }

  function applyResourceGroups(groups, extras = {}) {
    state.resourceGroups = groups;
    state.resourceIndex = indexResources(groups);
    if ('aliases' in extras) state.gatewayAliases = extras.aliases || [];
    if ('errors' in extras) state.resourceErrors = extras.errors || {};
  }

  function applyBrandChrome(brand) {
    if (!brand) return;
    if (brand.pageTitle) document.title = brand.pageTitle;
    const colors = brand.colors || {};
    const root = document.documentElement.style;
    if (colors.primaryDark) {
      root.setProperty('--purple-700', colors.primaryDark);
      root.setProperty('--purple-600', colors.primaryDark);
    }
    if (colors.primary) {
      root.setProperty('--purple-500', colors.primary);
      root.setProperty('--accent-1', colors.primary);
    }
    if (colors.primaryLight) {
      root.setProperty('--purple-100', colors.primaryLight);
    }
  }

  async function loadScopeData() {
    const scope = state.scope;
    const gen = ++scopeLoad;

    const [resources, activity] = await Promise.all([
      SW.api.resources(scope.id),
      SW.api.activity(scope.id),
    ]);
    if (gen !== scopeLoad) return;
    applyResourceGroups(resources.groups, { aliases: resources.aliases, errors: {} });
    // Cleared here, with the members it is the complement of, and not left standing until the
    // deferred listing below refills it. The two are read together: a resource that IS a member of
    // the scope just picked but was not a member of the last one would otherwise sit in the @ menu
    // captioned `not in {project}` — the opposite of true — until the listing landed.
    state.catalogueParents = [];
    state.resourcesLoading = false;
    state.activity = activity;
    notify();

    const appTicket = appScopeTicket();
    Promise.all([
      SW.api.project().catch(() => ({ attached: [] })),
      SW.api.resourceListing(),
    ]).then(([project, listing]) => {
      if (gen !== scopeLoad) return;
      // Off the same read, because this is the other moment the app's manifest changes: adding a
      // scratch file to a Dataset attaches it, and the panel refreshes through here rather than
      // through `loadBuild`. Without this the Build header would go on saying the app ships
      // nothing until the next app switch (#92).
      applyAppScope(appTicket, { appAttachments: project.attached || [] });
      const files = [
        ...(project.scratch || []).map((e) => ({
          id: `file:${e.path}`,
          name: e.name || (e.path || '').split('/').pop(),
          kind: 'file',
          path: e.path,
          source: 'scratch',
        })),
        ...(project.attached || [])
          .filter((e) => !SW.util.isHiddenFromExplorer(e.path))
          .map((e) => ({
            id: `file:${e.path}`,
            name: (e.path || '').split('/').pop(),
            kind: 'file',
            path: e.path,
          })),
      ];
      applyResourceGroups(
        SW.api.overlayResourceListing({ ...state.resourceGroups, file: files }, listing),
        {
          aliases: (listing.groups && listing.groups.model_llm) || [],
          errors: listing.errors || {},
        }
      );
      // The overlay keeps only the Datasets already in the rail. A scratch file can be promoted
      // onto any Dataset this container mounts writable, so that set is kept whole here.
      state.datasetTargets = ((listing.groups && listing.groups.dataset) || []).filter((d) => d.writable);
      // Same read, same reason: the overlay discards every non-member, and the @ menu needs them.
      // Parents only — a warehouse table is a level down and this listing never fetched one.
      const members = new Set(
        SW.util.MEMBERSHIP_PARENT_KINDS.flatMap(
          (kind) => (state.resourceGroups[kind] || []).map((r) => r.id)
        )
      );
      state.catalogueParents = SW.util.MEMBERSHIP_PARENT_KINDS.flatMap(
        (kind) => (((listing.groups && listing.groups[kind]) || []).filter((r) => !members.has(r.id)))
      );
      notify();
    }).catch(() => {});

    const members = await SW.api.members(scope.id || null);
    if (gen !== scopeLoad) return;
    state.members = members.members;
    state.directory = members.directory;

    // Anything that renders a name or avatar looks the person up here, so
    // author IDs on plans and comments resolve even for non-members.
    state.userIndex = {};
    [...members.directory, ...members.members].forEach((user) => {
      state.userIndex[user.id] = user;
    });
    if (state.me) state.userIndex[state.me.id] = state.me;
    notify();
  }

  async function loadThreadList() {
    state.threads = await SW.api.threads(state.scope.id);
    notify();
  }

  // The app a select is on the way to, or null. Two things ask for the same app before the first
  // answer lands — the route asserts it on mount, and the rail's own load re-renders underneath —
  // and the second request would be refused by the turn lock the first one is holding, which
  // reaches the person as a warning about a build that is not running.
  let selecting = null;

  // Whether a New app is in flight. The turn lock is released before `create_app` returns, so
  // nothing downstream refuses a second click — it mints a SECOND app, and the person is left
  // deleting one they never asked for. Same shape as `selecting`, for the same reason.
  let creating = false;

  // The Build rail's list. `activeApp` follows it rather than being set beside it, so the row that
  // is lit and the app the server is pointed at cannot drift apart.
  //
  // What hangs off the app follows it too (#95). This is the read Build polls every 30s, and every
  // 2s mid-build, so the selected app can move here with nobody clicking — a second tab choosing a
  // different app is enough. Until this cascaded, `bindings` and `appAttachments` went on
  // describing the app selected before, and the header's scope row names the app over those lists
  // outright, so the wrong pairing was printed rather than implied.
  //
  // Guarded on the id, not run every tick: unguarded, a poll costing one request would cost three,
  // forever, in every open Build tab. `cascade: false` is for the callers that refresh app-scoped
  // state themselves straight after — without it `loadBuild` would read `/bindings` twice on every
  // app switch.
  //
  // `ticket` is passed by the callers that read this list as part of a bigger chain, and defaulted
  // here for the rest: which app is selected is what THIS read said, so the cascade it fires writes
  // under that read's place in the queue rather than minting a fresher one for information that is
  // no newer (#101).
  async function loadAppList({ cascade = true, ticket = appScopeTicket() } = {}) {
    const apps = await SW.api.apps().catch(() => null);
    state.apps = apps || [];
    const next = state.apps.find((a) => a.selected) || null;
    const moved = (next && next.id) !== (state.activeApp && state.activeApp.id);
    // A read that FAILED is not an app that moved. `apps()` answers empty for a 500 as readily as
    // for a Project with no apps, so without this one blip fires the whole cascade, and the tick
    // that recovers fires it again.
    //
    // A superseded read is not worth two more requests to install either, so the queue is asked
    // here as well as at the write: the tick that lost costs the one read it had already made.
    if (apps && moved && cascade && appScopeCurrent(ticket, 'activeApp')) {
      await refreshAppScope(next, ticket);
    } else {
      applyAppScope(ticket, { activeApp: next });
    }
    notify();
  }

  async function refreshAttachments() {
    const id = conversationId();
    state.attachments = id ? await SW.api.conversationContext(id) : [];
    notify();
  }

  // Everything read per app, refetched rather than blanked. Blanking on an app change is cheaper
  // and never shows a wrong pairing, but it drops the header's row to an empty state reading
  // "nothing yet", which for an app that ships two Bindings is a lie.
  //
  // The app and both lists are assigned TOGETHER, once every read has landed — which is why
  // this reads `/bindings` itself rather than going through `refreshBindings`. Assigning the app
  // first would put the new name over the old lists for the length of the reads, and that window
  // is not private: `refreshPreview`'s interval and every SSE build frame call `notify()`, so
  // mid-build the row WOULD be repainted inside it, saying exactly what #95 is about.
  async function refreshAppScope(app, ticket = appScopeTicket()) {
    const [project, bound] = await Promise.all([
      SW.api.project().catch(() => null),
      SW.api.bindings().catch(() => null),
    ]);
    // The notice goes with the lists, and the gate drops it when the selection actually moves —
    // which is the same rule applied on every path the selection moves down, rather than only on
    // this one (#101).
    //
    // A read that failed keeps what is on screen. Emptying on a 502 would have the row report an
    // app that ships nothing — the same lie as blanking, arrived at by accident.
    applyAppScope(ticket, {
      activeApp: app,
      ...(project ? { appAttachments: project.attached || [] } : {}),
      ...(bound ? { bindings: bound.bindings || [] } : {}),
    });
  }

  // Re-picking a Binding does not cost the same over the three kinds, and one sentence covering all
  // of them would either overstate or understate. A Data Source's Scope goes with the Binding
  // record and nothing else holds it, so it has to be chosen again. A Model API's access token does
  // NOT go — it lives in `CredentialStore`, keyed by model id, which `unbind` never touches — so
  // saying nothing here would let someone expect the worse outcome and keep a Binding they do not
  // want.
  const UNBIND_COPY = {
    data_source: {
      stops: 'stops being allowed to read it',
      cost: 'Pick it again from Project resources and you will choose its Scope again — the Scope '
        + 'goes with the Binding.',
    },
    model_api: {
      stops: 'stops being allowed to call it',
      cost: 'Pick it again from Project resources. The access token stays, so it will not ask for '
        + 'the sample request again.',
    },
  };
  // The third kind, and any kind added later. An LLM Alias carries neither a Scope nor a
  // credential, so re-picking it costs the pick and nothing else — which is worth saying plainly
  // rather than leaving the confirm to imply a cost the kind does not have.
  const UNBIND_PLAIN = {
    stops: 'stops being allowed to use it',
    cost: 'Pick it again from Project resources.',
  };

  // Whose lists these are. A Build with no selected app draws no section, so the fallback is for
  // the window where a read has not landed rather than for a state anyone acts in.
  function appScopeName() {
    return state.activeApp ? state.activeApp.name : 'this app';
  }

  // What a removal REPORTED, once it has happened. Both routes read the app's own source before the
  // record goes and hand back what still uses it, so this reports an answer rather than asking for
  // one: nothing here scans anything, and nothing warned before the act (ADR-0010).
  //
  // The offer is only attached when there is something to act on. Where the app's code refers to
  // nothing, the sentence is still worth drawing — it is the acknowledgement that the act landed.
  //
  // Returned rather than assigned: the notice and the list the act rewrote are one answer about
  // one app, so they go into the store together, under the act's own ticket (#101).
  function removalNotice(where, name, refs, alsoDid) {
    const uses = refs.length
      ? `The app's code still uses it in ${refs.join(', ')}.`
      : "Nothing in the app's code refers to it.";
    return {
      text: `${name} is out of ${where}.${alsoDid ? ` ${alsoDid}` : ''} ${uses}`,
      prompt: refs.length
        ? `${name} is no longer part of this app, and ${refs.join(', ')} still refer to it. `
          + 'Remove or replace those uses.'
        : null,
    };
  }

  // ---------------------------------------------------------------------
  // Scripted assistant delivery
  // ---------------------------------------------------------------------

  function fillTemplate(blocks, values) {
    return JSON.parse(
      JSON.stringify(blocks).replace(/\{\{(\w+)\}\}/g, (_, key) => values[key] || '')
    );
  }

  function pushMessage(message) {
    state.messages = [...state.messages, message];
    notify();
  }

  function summarise(text) {
    const trimmed = text.trim();
    return trimmed.length > 48 ? `${trimmed.slice(0, 48)}…` : trimmed;
  }

  function fileUrl(path) {
    return `./api/project/file/raw?path=${encodeURIComponent(path)}`;
  }

  async function blocksForArtifacts(items) {
    const blocks = [];
    for (const art of items || []) {
      const path = art.path || '';
      const lower = path.toLowerCase();
      if (art.kind === 'chart' || lower.endsWith('.png')) {
        blocks.push({ type: 'image', title: art.title || art.name, src: fileUrl(path) });
      } else if (art.kind === 'table' || lower.endsWith('.table.json')) {
        try {
          const body = await fetch(`./api/project/file?path=${encodeURIComponent(path)}`).then((r) => r.json());
          const data = JSON.parse(body.content || '{}');
          blocks.push({
            type: 'table',
            title: data.title || art.title,
            columns: data.columns || [],
            rows: data.rows || [],
          });
        } catch (err) {
          blocks.push({ type: 'file', name: art.name || path, path });
        }
      } else if (path) {
        blocks.push({ type: 'file', name: art.name || path, path });
      }
    }
    return blocks;
  }

  async function historyToMessages(history, handoff) {
    const messages = [];
    let assistant = null;
    // Where in the read this message started. Only the merged conversation view uses it — it
    // interleaves these with the Build half's runs, and both are produced by walking their own
    // rows, so the position in the merged read is the only thing that can put them back in order.
    // A plain Chat read carries `order` and nothing looks at it.
    let pos = 0;
    const ensureAssistant = () => {
      if (!assistant) {
        assistant = { id: `a_${messages.length}`, role: 'assistant', at: new Date().toISOString(),
                      order: pos, blocks: [] };
        messages.push(assistant);
      }
      return assistant;
    };
    const hideSuggest = handoff && (handoff.suppressed || handoff.status === 'suppressed'
      || handoff.status === 'bound' || handoff.status === 'planned');
    // Only the newest offer is live. A Thread may hand off more than once (ADR-0008), so the
    // history holds one suggest event per handoff and the older ones were answered long ago —
    // replaying them puts a dead callout back in the middle of the conversation.
    const liveSuggest = (history || []).reduce(
      (last, ev, i) => (ev.type === 'handoff-suggest' ? i : last), -1);
    const shownArts = new Set();
    for (const [i, ev] of (history || []).entries()) {
      pos = ev.order === undefined ? i : ev.order;
      if (ev.type === 'user') {
        assistant = null;
        messages.push({
          id: `u_${messages.length}`,
          role: 'user',
          at: ev.at,
          order: pos,
          blocks: [{ type: 'text', value: ev.text || '' }],
          contextIds: ev.contextIds,
          attachments: attachmentsFromUserEvent(ev),
        });
      } else if (ev.type === 'agent' && ev.kind === 'text' && ev.text) {
        ensureAssistant().blocks.push({ type: 'text', value: ev.text });
      } else if (ev.type === 'agent' && ev.kind === 'tool') {
        continue;
      } else if (ev.type === 'artifacts' || (ev.type === 'done' && ev.artifacts && ev.artifacts.length)) {
        const items = (ev.items || ev.artifacts || []).filter((a) => {
          const key = a.path || a.id;
          if (!key || shownArts.has(key)) return false;
          shownArts.add(key);
          return true;
        });
        if (items.length) {
          ensureAssistant().blocks.push(...(await blocksForArtifacts(items)));
        }
      } else if (ev.type === 'error' || ev.type === 'stopped') {
        // Why the turn ended, on reload as well as live. The server has always persisted these —
        // the timeout sentence that names what to try instead, and now Stop — and this loop
        // dropped both, so reopening a Thread showed a question with no answer and no reason.
        ensureAssistant().blocks.push({
          type: 'status',
          ok: false,
          value: ev.message || (ev.type === 'stopped' ? 'Stopped.' : 'The turn failed.'),
        });
      } else if (ev.type === 'handoff-suggest' && !hideSuggest && i === liveSuggest) {
        assistant = null;
        messages.push({
          id: `sug_${messages.length}`,
          role: 'system',
          order: pos,
          blocks: [{ type: 'plan_suggestion', reason: ev.reason }],
        });
      }
    }
    return withHandoffCallout(messages, handoff);
  }

  function attachmentsFromUserEvent(ev) {
    const snap = ev.context;
    if (!Array.isArray(snap) || !snap.length) return [];
    return snap.map((c) => ({
      resourceId: c.id,
      name: c.name,
      kind: SW.util.uiKind(c.kind),
    }));
  }

  function withHandoffCallout(messages, handoff) {
    if (!handoff || handoff.suppressed || handoff.status !== 'suggested') return messages;
    if (messages.some((m) => (m.blocks || []).some((b) => b.type === 'plan_suggestion'))) return messages;
    return messages.concat([{
      id: 'handoff_suggest',
      role: 'system',
      // A live offer, not a turn that happened: it belongs after everything either half has done,
      // which is where the merged view's sort puts it too.
      order: Infinity,
      blocks: [{ type: 'plan_suggestion' }],
    }]);
  }

  async function readSSE(res, onEvent) {
    const reader = res.body.getReader();
    const dec = new TextDecoder();
    let buf = '';
    while (true) {
      const { done, value } = await reader.read();
      if (done) break;
      buf += dec.decode(value, { stream: true });
      const parts = buf.split('\n\n');
      buf = parts.pop();
      for (const part of parts) {
        for (const line of part.split('\n')) {
          if (line.startsWith('data: ')) {
            // Awaited, because `sendMessage` passes an async handler that fetches artifact
            // bodies. Unawaited, the next frame re-entered it while that fetch was still in
            // flight, and the dedupe Set was built from blocks the first call had not appended
            // yet — so a table artifact rendered twice. Awaiting also puts a handler's own
            // rejection inside this catch, where it was previously invisible.
            try { await onEvent(JSON.parse(line.slice(6))); } catch (err) { /* keep-alive or partial */ }
          }
        }
      }
    }
  }

  // Decisions whose own card already says what happened and what to do next. A red "Stopped —"
  // line under one of those reads as a failure the user has to fix, when the turn stopped exactly
  // as designed and the thing above it is asking them a question.
  const GATE_DECISIONS = {
    'awaiting approval': true,
    'architecture ready': true,
    'reset offered': true,
    'incoming changes': true,
  };

  // What each tool is called in the user's words. `bash` has read "Ran a command" since the first
  // build card; every other tool rendered its raw OpenCode name — "Ran glob", "Ran skill" — which
  // names the harness's plumbing rather than the step the user is watching. One entry per tool the
  // pinned 1.18.4 binary emits, with `ran` for the finished card and `doing` for the line that says
  // what the build is on right now. A tool that isn't here still falls back to its own name: an
  // unrecognised step should say something odd, not say nothing.
  const TOOL_LABELS = {
    bash: { ran: 'Ran a command', doing: 'Running a command' },
    edit: { ran: 'Edited a file', doing: 'Editing a file' },
    patch: { ran: 'Edited a file', doing: 'Editing a file' },
    multiedit: { ran: 'Edited a file', doing: 'Editing a file' },
    write: { ran: 'Wrote a file', doing: 'Writing a file' },
    read: { ran: 'Read a file', doing: 'Reading a file' },
    grep: { ran: 'Searched the code', doing: 'Searching the code' },
    glob: { ran: 'Searched for files', doing: 'Searching for files' },
    list: { ran: 'Listed a folder', doing: 'Listing a folder' },
    webfetch: { ran: 'Read a web page', doing: 'Reading a web page' },
    task: { ran: 'Ran a sub-task', doing: 'Running a sub-task' },
    skill: { ran: 'Used a skill', doing: 'Using a skill' },
    todowrite: { ran: 'Updated the task list', doing: 'Updating the task list' },
    todoread: { ran: 'Read the task list', doing: 'Reading the task list' },
  };

  // Map the "@name" tokens still standing in a Build prompt back to what they name: attached files as
  // workspace paths, Resources as Binding identities (kind + id — an id is unique only within its
  // kind). Read off the same rows the picker offered, so what a row offers and what the turn sends
  // cannot drift apart. Chat resolves its own tokens server-side against the Thread's context; a
  // Build turn has to carry them in the request, and carried nothing at all until this existed —
  // which made every @mention in Build a plain word the agent had to go looking for.
  function collectTurnRefs(text) {
    const groups = state.resourceGroups || {};
    const mentions = [];
    const resources = [];
    const seen = new Set();
    Object.keys(groups).forEach((group) => {
      (groups[group] || []).forEach((row) => {
        if (!SW.util.mentionedIn(text, SW.util.mentionToken(row))) return;
        // A bindingKey IS the Binding identity, so the rows that carry one are exactly the rows the
        // server can honor as Resources. No second list of kinds to keep in step with that one.
        if (row.bindingKey && row.bindingKey.length === 2) {
          const ref = { kind: row.bindingKey[0], id: row.bindingKey[1], name: row.name || '' };
          if (row.scope && row.scope.table) ref.table = row.scope.table;
          const key = `${ref.kind}:${ref.id}:${ref.table || ''}`;
          if (seen.has(key)) return;
          seen.add(key);
          resources.push(ref);
          return;
        }
        // A Chat upload or a pinned Dataset file is offered by the menu and cannot be read by a
        // build. Send what names it anyway rather than dropping it here: the turn reports what it
        // could not use, and a mention nobody hears about is one nobody can fix.
        const path = row.path || row.datasetRelPath || row.name;
        if (path && !mentions.includes(path)) mentions.push(path);
      });
    });
    return { mentions, resources };
  }

  function buildHistoryToMessages(history) {
    const messages = [];
    let assistant = null;
    let pendingPlan = null;
    let pos = 0;
    const ensureAssistant = () => {
      if (!assistant) {
        assistant = { id: `ba_${messages.length}`, role: 'assistant', at: new Date().toISOString(),
                      order: pos, blocks: [] };
        messages.push(assistant);
      }
      return assistant;
    };
    for (const [i, ev] of (history || []).entries()) {
      pos = ev.order === undefined ? i : ev.order;
      if (ev.type === 'user') {
        assistant = null;
        messages.push({
          id: `bu_${messages.length}`,
          role: 'user',
          at: ev.at,
          order: pos,
          blocks: [{ type: 'text', value: ev.text || '' }],
        });
      } else if (ev.type === 'agent' && ev.kind === 'text' && ev.text) {
        ensureAssistant().blocks.push({ type: 'text', value: ev.text });
      } else if (ev.type === 'agent' && ev.kind === 'tool') {
        ensureAssistant().blocks.push({
          type: 'sandbox_run',
          label: (TOOL_LABELS[ev.tool] || {}).ran || `Ran ${ev.tool || 'tool'}`,
          // Absent on turns OpenCode did not time, and on every turn recorded before Sage started
          // reading the clock. The card leaves the duration off rather than inventing one.
          durationMs: ev.durationMs,
          code: ev.detail || '',
        });
      } else if (ev.type === 'plan-proposed') {
        assistant = null;
        if (pendingPlan) pendingPlan.pending = false;
        const block = {
          type: 'build_plan',
          plan: ev.plan || '',
          kind: ev.kind || 'plan',
          // The plan document this turn wrote. Empty for an architecture, which has no document.
          planId: ev.planId || '',
          steps: ev.steps || 0,
          pending: true,
          // What a confirmed handoff carried in, and which Built App it went to (#60). Absent on
          // a plan the Build gate wrote, which crossed nothing and so has nothing to report.
          crossed: ev.crossed || null,
        };
        pendingPlan = block;
        messages.push({
          id: `bp_${messages.length}`,
          role: 'assistant',
          order: pos,
          blocks: [block],
        });
      } else if (ev.type === 'plan-stale') {
        if (pendingPlan) pendingPlan.pending = false;
      } else if (ev.type === 'plan-superseded') {
        // Written into THIS conversation's transcript by a turn in ANOTHER one, which handed off
        // into the same Built App (#59). Without it the card here goes on offering "Approve &
        // build" for a plan the app stopped holding, which is the whole defect. Matched on the
        // document so a log with several plans in it corrects the right card.
        if (pendingPlan && (!ev.planId || pendingPlan.planId === ev.planId)) {
          pendingPlan.pending = false;
          pendingPlan.superseded = { by: ev.by || '', conversation: ev.byConversation || '' };
        }
      } else if (ev.type === 'handoff-recrossed') {
        // Change redid the crossing (#60). Folded onto the card the handoff already has rather
        // than drawn as one of its own, because only one card appears for a handoff — which is
        // also why the server writes this row instead of confirming a second time.
        //
        // Merged, not replaced: the row carries what crossed and says nothing about where it
        // went, so the app and whether it was new survive a Change that never asked about them.
        if (pendingPlan && (!ev.planId || pendingPlan.planId === ev.planId) && pendingPlan.crossed) {
          pendingPlan.crossed = { ...pendingPlan.crossed, ...(ev.crossed || {}) };
        }
      } else if (ev.type === 'plan-cancelled') {
        // Undo, or the plain Cancel on any plan card. The plan is archived rather than deleted, so
        // the card stays and stops offering a build — and on a handoff it goes on to say the Built
        // App it minted is still there, which is only sayable because this row survives a reload.
        if (pendingPlan && (!ev.planId || pendingPlan.planId === ev.planId)) {
          pendingPlan.pending = false;
          pendingPlan.cancelled = true;
        }
      } else if (ev.type === 'typecheck') {
        ensureAssistant().blocks.push({
          type: 'status',
          ok: ev.ok,
          value: ev.ok ? 'Typecheck passed' : `Typecheck: ${ev.errors} error(s)`,
        });
      } else if (ev.type === 'done') {
        if (!GATE_DECISIONS[ev.decision]) {
          if (pendingPlan) pendingPlan.pending = false;
          ensureAssistant().blocks.push({
            type: 'status',
            ok: ev.ok,
            value: ev.decision === 'answered'
              ? 'Answered'
              : (ev.ok ? 'Done — build is clean' : `Stopped — ${ev.decision}`),
          });
        }
      } else if (ev.type === 'error' && ev.message) {
        ensureAssistant().blocks.push({ type: 'status', ok: false, value: ev.message });
      } else if (ev.type === 'saved') {
        const value = ev.ok
          ? (ev.pushed ? 'Saved and pushed' : `Saved${ev.detail ? ` — ${ev.detail}` : ''}`)
          : `Couldn't save — ${ev.detail || 'git error'}`;
        ensureAssistant().blocks.push({ type: 'status', ok: !!ev.ok, value });
      } else if ((ev.type === 'ask-blocked' || ev.type === 'ask-active') && ev.message) {
        ensureAssistant().blocks.push({ type: 'status', ok: false, value: ev.message });
      } else if (ev.type === 'data-leak' && ev.file) {
        // Persisted since it shipped and never drawn, so the defect it reports — attached data
        // copied into src/, which leaks it into git — reached the transcript and nobody ever saw it.
        // The nudge is the agent's half; this is the creator's, and without it a prompt that keeps
        // causing the copy gets repeated by the one person who could stop writing it.
        ensureAssistant().blocks.push({
          type: 'status',
          ok: false,
          value: `${ev.file} was copied into ${(ev.where || []).join(', ') || 'the app source'}`
            + ' — moving it back to data/',
        });
      } else if (ev.type === 'gateway-call' && ev.file) {
        // Calling Domino's LLM Gateway around askModel (#94). Drawn beside the leak line above for
        // the same reason: the agent is being nudged to fix it, and a creator who can see which
        // file did it can tell whether the fix landed.
        ensureAssistant().blocks.push({
          type: 'status',
          ok: false,
          value: SW.brand.text(
            "{file} calls {platformName}'s LLM Gateway directly — rewriting it to use askModel",
            { file: ev.file }
          ),
        });
      } else if (ev.type === 'gateway-alias-unbound' && ev.message) {
        // The half of that the agent cannot finish: only a person can bind an Alias (ADR-0010), so
        // this sentence is the whole point of the event and is written server-side, already
        // addressed to the creator.
        ensureAssistant().blocks.push({ type: 'status', ok: false, value: ev.message });
      } else if (ev.type === 'mentions-unresolved' && ev.message) {
        // Sits above the turn it belongs to rather than beside the composer: what the build could not
        // use is part of the record of that build, and a toast would be gone by the time the app it
        // built came back wrong.
        ensureAssistant().blocks.push({ type: 'status', ok: false, value: ev.message });
      } else if (ev.type === 'reset-offer' && ev.message) {
        // `live` is set only on the frame that arrived over SSE this session (see applyBuildEvent),
        // and a reload replaces buildHistory with plain server rows that never carry it. So a
        // replayed offer renders as text with no buttons: an old message must not be able to reset
        // the app on a page load nobody connected it to.
        ensureAssistant().blocks.push({
          type: 'reset_offer',
          message: ev.message,
          prompt: ev.prompt || '',
          live: !!ev.live,
        });
      } else if (ev.type === 'incoming-changes' && ev.message) {
        // Same `live` rule as the reset offer above, and for the same reason: an offer replayed
        // from the transcript must not be able to pull the repo on a page load nobody connected
        // it to. The files are what makes it readable — "somebody changed this app" is a fact you
        // can act on only once you can see what they changed.
        ensureAssistant().blocks.push({
          type: 'incoming_changes',
          message: ev.message,
          prompt: ev.prompt || '',
          files: ev.files || [],
          count: ev.count || (ev.files || []).length,
          live: !!ev.live,
        });
      } else if (ev.type === 'build-stalled' && ev.message) {
        // A turn that stopped saying anything and was given up on (#39). An offer rather than a
        // status line: the message explains what happened and what was kept, and the button asks
        // again. Same `live` rule as the two offers above — a replayed row must not resend a build
        // on a page load nobody connected it to. A turn with no sentence of the person's to replay
        // (an approve, a phase of a phased build) arrives with an empty `prompt` and renders as the
        // message alone.
        ensureAssistant().blocks.push({
          type: 'build_stalled',
          message: ev.message,
          prompt: ev.prompt || '',
          live: !!ev.live,
        });
      } else if (ev.type === 'app-reset') {
        ensureAssistant().blocks.push({
          type: 'status',
          ok: true,
          value: 'This app is reset to the starter template. Your attached files, Resources, this '
            + 'conversation and your other apps are unchanged.',
        });
      } else if (ev.type === 'app_change') {
        // What the turn changed, attached to the app it changed. Here rather than behind the
        // conversation view, because the block belongs to the build turn and both views show it
        // (#83): Build draws the card at the end of the turn, and Chat's merged read folds a run's
        // cards into the row's face. The card is what survives if the folding goes away (#61).
        ensureAssistant().blocks.push({
          type: 'app_change',
          appId: ev.appId || ev.app || '',
          name: ev.name || '',
        });
      }
    }
    return messages;
  }

  // ---- the merged conversation (#56) ---------------------------------------------------------
  //
  // Chat's half and Build's half are one Conversation, and `SW.api.conversation` returns them in
  // one order, each row labelled. Turning that into messages is the UNIFIED conversation view's
  // work — the split view still reads the two halves apart, exactly as it does today.
  //
  // Left here rather than in a mode, because #57 shows the same merged Conversation in Build and
  // reuses this rather than deriving it a second time. Whichever ticket landed first was to build
  // it; this one did.

  // The apps a run changed, one entry per app rather than per card: a run that touched the same app
  // over several turns is still one app, and the newest card is the one that names it as it was
  // called by the end of the run.
  function appsChangedIn(rows) {
    const byApp = new Map();
    for (const ev of rows) {
      if (ev.type !== 'app_change') continue;
      const appId = ev.appId || ev.app || '';
      byApp.set(appId, { type: 'app_change', appId, name: ev.name || '' });
    }
    return [...byApp.values()];
  }

  // The Build half, folded. A RUN is a build turn: it opens on the user row that asked for it and
  // closes on that turn's `done`, and only a run folds — the row summarises a prompt, so a stretch
  // of log with no prompt behind it has nothing to summarise.
  //
  // Two kinds of row are deliberately not runs, and folding them was the same bug twice. A confirmed
  // handoff writes the plan card and its `done` into the Build log with no user row (see
  // Orchestrator.confirm_handoff), and that plan card is the handoff's own card (#60) — it belongs
  // on screen, not behind "Show the turns". `app-reset` and `attachments-restored` are appended
  // outside any turn, and swallowed into the run above them they read as something the last build
  // prompt did. Both render in place, exactly as Build renders them.
  function buildRunMessages(rows) {
    const out = [];
    let loose = [];
    let run = null;
    let seq = 0;

    const flushLoose = () => {
      if (!loose.length) return;
      // Ids have to survive being concatenated with every other segment's, and each row's position
      // in the merged read is already unique.
      out.push(...buildHistoryToMessages(loose).map((m) => ({ ...m, id: `${m.id}_${seq}` })));
      seq += 1;
      loose = [];
    };
    const flushRun = () => {
      if (!run) return;
      out.push({
        id: `run_${run.order}`,
        role: 'assistant',
        order: run.order,
        blocks: [{
          type: 'build_run',
          prompt: run.prompt,
          // When the run was asked for, off the user row that opened it. Absent on every row
          // written before Sage started stamping the clock, and a surface that shows the time
          // leaves it off rather than inventing one — the rule `durationMs` already follows in
          // `buildHistoryToMessages`.
          at: run.at,
          apps: appsChangedIn(run.rows),
          // The cards are the row's FACE, so they are not repeated inside the turns it opens on.
          messages: buildHistoryToMessages(run.rows.filter((ev) => ev.type !== 'app_change')),
        }],
      });
      run = null;
    };

    for (const [i, ev] of rows.entries()) {
      if (ev.type === 'user') {
        flushLoose();
        flushRun();
        // The row's own position when nothing stamped one, exactly as `buildHistoryToMessages`
        // falls back. The merged read numbers every row before it splits them (#56), but a log
        // read straight off the app's disk carries no `order` at all — and `run_undefined` is the
        // same id for every run in it, which React draws as one (#88).
        run = {
          order: ev.order === undefined ? i : ev.order,
          at: ev.at,
          prompt: ev.text || '',
          rows: [],
        };
      }
      if (!run) {
        loose.push(ev);
        continue;
      }
      run.rows.push(ev);
      if (ev.type === 'done') flushRun();
    }
    flushLoose();
    flushRun();
    return out;
  }

  // The merged read, cut into the two halves their own readers know how to walk. `appId` narrows
  // the build half to one Built App; Chat passes none, because with no preview to bind it Chat
  // shows every app the Conversation drove (#56).
  //
  // A build row with no app at all is one adopted from a log written before there were per-app logs
  // (#68), so there was only one app for it to be in. It stays, because a merged view that hid it
  // would be strictly emptier than the split view it replaces — the failure the server adopts
  // legacy history to avoid.
  function splitConversationHalves(history, appId) {
    const chat = [];
    const build = [];
    (history || []).forEach((row, i) => {
      if (row.half === 'build') {
        if (!appId || !row.app || row.app === appId) build.push({ ...row, order: i });
        return;
      }
      chat.push({ ...row, order: i });
    });
    return { chat, build };
  }

  async function mergedHistoryToMessages(history, handoff) {
    const { chat, build } = splitConversationHalves(history);
    // Each half is walked by the reader that already knows how to read it — a build turn's tool
    // cards and plan cards are not chat blocks — and `order` is what puts the two back together.
    const messages = (await historyToMessages(chat, handoff)).concat(buildRunMessages(build));
    return messages.sort((a, b) => a.order - b.order);
  }

  // ---- the same Conversation, in Build (#57) ---------------------------------------------------
  //
  // Build reads the merged read above rather than a second one, and it takes the halves apart
  // instead of taking one list back, because it draws them differently to Chat.
  //
  // The asymmetry is deliberate. Chat has no preview, so it shows every Built App the Conversation
  // drove. Build has a preview bound to ONE app, so its build turns are the SELECTED app's — the
  // other app's work belongs on a screen that can show it. The Chat turns are the whole
  // Conversation either way: that half is what this ticket exists to stop losing.
  //
  // Nothing here folds a run. Chat folds one because twenty raw implementation turns would bury the
  // questions around them; Build is where those turns belong, so Build draws them.

  // Where a row arriving now sits in the merged order. Null when Build is reading its own log
  // alone, which is the split view and the fallback: there is nothing to interleave with, so the
  // row's position in the log is its position on screen, exactly as it has always been.
  let buildSeq = null;

  // What Build renders, from what it holds. EVERY writer of `buildHistory` ends here — a load, a
  // poll, an echoed prompt, each event of a running turn — because a writer that set
  // `buildMessages` alone would leave the transcript on screen pointing at the list before it.
  function applyBuildTranscript() {
    state.buildMessages = buildHistoryToMessages(state.buildHistory);
    state.buildTranscript = state.conversationChat.length
      ? state.conversationChat.concat(state.buildMessages).sort((a, b) => a.order - b.order)
      : state.buildMessages;
  }

  // One row onto the end of the log. The order stamp is what keeps a turn happening NOW below the
  // Chat turns it came after: without it `buildHistoryToMessages` falls back to the row's index in
  // this app's log, which is a number from a different scale entirely.
  function appendBuildRow(ev) {
    if (buildSeq !== null) ev.order = buildSeq++;
    state.buildHistory = state.buildHistory.concat([ev]);
    applyBuildTranscript();
  }

  // One read, whichever view is on. Unified wants the whole Conversation — the Chat turns and the
  // build turns of every app it drove — and that is the read #56 already built, so this asks it for
  // that rather than deriving a second one. Split asks the question Build has always asked: this
  // app's log, for this Conversation.
  //
  // A merged read that fell over drops to the split read rather than to nothing. Build without its
  // Chat turns is half the story; Build without its own turns is a blank screen, and a blank screen
  // is the failure this ticket was filed about.
  //
  // Both keys always come back, one of them null, so the caller reads which read it got rather than
  // guessing from which key happens to exist.
  async function readBuildTranscript(conversation) {
    if (SW.prefs.get('conversationView') === 'unified') {
      const merged = await SW.api.conversation(conversation).catch(() => null);
      if (merged) return { merged, history: null };
    }
    const own = await SW.api.history(conversation).catch(() => ({ history: [] }));
    return { merged: null, history: own.history || [] };
  }

  // One read, applied. The load and the mid-build poll both come through here, so a tick during a
  // running turn cannot quietly swap the merged transcript for this app's half alone.
  async function applyBuildRead(read) {
    if (read.merged) {
      // After the app list, never before it: which app is selected decides which build turns are
      // this pane's.
      const halves = splitConversationHalves(read.merged, state.activeApp && state.activeApp.id);
      // The Chat half is read by the reader that knows how — but its handoff OFFER is Chat's alone.
      // It offers a way over to Build, and this is Build. Dropped on the block rather than the
      // message id, because the offer arrives in two shapes: the live callout appended at the end,
      // and a suggestion persisted mid-history. Both draw the same control.
      const chat = await historyToMessages(halves.chat, state.thread && state.thread.handoff);
      state.conversationChat = chat.filter(
        (m) => !(m.blocks || []).some((b) => b.type === 'plan_suggestion')
      );
      state.buildHistory = halves.build;
      buildSeq = read.merged.length;
    } else {
      state.conversationChat = [];
      state.buildHistory = read.history || [];
      buildSeq = null;
    }
    applyBuildTranscript();
  }

  // How many turns this tab currently has open, per mode — running or still waiting in line (#79).
  // The `*Running` flags are project-wide facts polled off the server's lock, and a tab has to keep
  // them honest between polls: with a queue it can have several turns alive at once, and the first
  // one to unwind used to clear a flag the others were still relying on.
  let liveBuildTurns = 0;
  let liveChatTurns = 0;

  // A turn this tab asked for that has not started yet (#79). Kept out of the transcript on
  // purpose: the transcript is the receipt, and nothing has happened yet to write one for.
  // `kind` and the conversation are captured HERE rather than read when the row draws: the row is
  // a record of what was asked and where, and the rail can move while it waits (#126). One shared
  // Composer draws these in both modes, so without them a queued Chat question appears above the
  // Build box looking like a queued build.
  function queueTurn(ev, kind) {
    state.queuedTurns = [...state.queuedTurns,
                         { ticket: ev.ticket, text: ev.prompt || '', message: ev.message || '',
                           kind, conversation: (state.thread && state.thread.id) || '' }];
  }

  function dropQueuedTurn(ticket) {
    if (!ticket) return;
    state.queuedTurns = state.queuedTurns.filter((q) => q.ticket !== ticket);
  }

  // Is the turn holding the lock the one on THIS screen? Both halves have to match: a Chat turn and
  // a Build turn in one Conversation are both yours, and only one of them is what you are looking at
  // (#126). False for a wedge and for a publish, which is right — neither is a turn to Stop.
  function runningTurnHere(kind, conversationId) {
    const t = state.runningTurn;
    return !!(t && t.kind === kind && conversationId && t.conversation === conversationId);
  }

  // The other side of the same question, for the line a mode shows INSTEAD of Stop. Only ask it
  // when the Project is busy — it describes the lock, not whether anything holds it.
  //
  // `href` is null when the holder has no identity. Publish, reset and the other raw-lock callers
  // never queued, so there is nowhere to send anyone and nothing to stop; saying the workspace is
  // busy is the honest answer, and it is also what stops a Stop button rendering over a publish.
  function runningTurnElsewhere(kind, conversationId) {
    const t = state.runningTurn;
    if (!t) return { text: 'The workspace is busy.', href: null };
    if (runningTurnHere(kind, conversationId)) return null;
    const row = (state.threads || []).find((x) => x.id === t.conversation);
    const what = t.kind === 'chat' ? 'Chat is answering' : 'Build is running';
    return { text: `${what} in ${(row && row.title) || 'another conversation'}`,
             href: `#/${t.kind}/${t.conversation}` };
  }

  // What `/build/state` says, folded into the flags that render. The server's lock is the authority
  // on whether a turn is running; this tab's own open turns are the authority on whether it may
  // stop showing one. A turn waiting in line holds neither — it is queued behind a lock somebody
  // else has, and between two queued turns that lock is free for an instant, which is long enough
  // for a poll to land on it and blank a header that is about to fill straight back up.
  function applyTurnState(payload) {
    const turn = payload || {};
    state.turnWedged = !!turn.wedged;
    state.turnPending = turn.pending || 0;
    // The same gap this function's OR already guards, one field along: between two queued turns the
    // lock is free for an instant, and a poll landing there reports no running turn. Blanking on it
    // would drop the Stop bar — or swap it to the other mode and back — as the queue drains. So a
    // named turn is always believed, and only a NAMELESS answer is held back while this tab still
    // has a turn of its own alive to hand the lock straight on to.
    if (turn.running_turn) state.runningTurn = turn.running_turn;
    else if (liveBuildTurns === 0 && liveChatTurns === 0) state.runningTurn = null;
    return !!turn.running || liveBuildTurns > 0 || liveChatTurns > 0;
  }

  function applyBuildEvent(ev) {
    if (!ev) return;
    // A turn that never ran leaves the transcript alone. The transcript is the receipt and there is
    // nothing here to give one for: `pending` is a composer row, and both ways a queued turn can end
    // without running hand the question back to the composer instead of recording it (#79).
    if (ev.type === 'pending') return;
    if (ev.contextChanged
        || (ev.type === 'done' && (ev.decision === 'cancelled' || ev.decision === 'context changed'))) {
      state.buildTyping = null;
      return;
    }
    if (ev.type === 'user') return;
    if (ev.type === 'active' || (ev.type === 'agent' && ev.kind === 'tool')) {
      // The command a bash step ran can be a whole pipeline, so bash shows the verb; every other
      // tool shows its subject — the file, the search pattern — which is shorter and says more.
      // The verb is what a tool with no subject falls back to, so this line stops reading "glob".
      const labels = TOOL_LABELS[ev.tool] || {};
      state.buildTyping = (ev.tool === 'bash' ? labels.doing : (ev.detail || labels.doing)) || 'Working';
    } else if (ev.type === 'typecheck-start') {
      state.buildTyping = 'Typechecking…';
    } else if (ev.type === 'iterate') {
      state.buildTyping = ev.reason || 'Fixing errors…';
    } else if (ev.type === 'agent' && ev.kind === 'text') {
      state.buildTyping = null;
    } else if (ev.type === 'plan-proposed' || ev.type === 'done' || ev.type === 'error' || ev.type === 'stopped') {
      state.buildTyping = null;
    }
    // A plan appearing and a plan being consumed are the two events the pin exists to show. Fired
    // and not awaited: applyBuildEvent is called once per SSE frame and must not block the stream.
    if (ev.type === 'plan-proposed' || ev.type === 'done') {
      refreshProjectPlan().then(notify, () => {});
    }
    if (ev.type === 'stopped') return;
    if (ev.type === 'active' || ev.type === 'phase' || ev.type === 'typecheck-start' || ev.type === 'iterate') return;
    // The buttons on a reset offer belong to the offer the user is looking at, not to every copy of
    // it the transcript keeps. Marking the live frame is what separates the two — the server row a
    // reload returns has no `live`, so it replays as text (see buildHistoryToMessages).
    if (ev.type === 'reset-offer' || ev.type === 'incoming-changes'
        || ev.type === 'build-stalled') ev.live = true;
    appendBuildRow(ev);
  }

  async function refreshBindings(ticket = appScopeTicket()) {
    const body = await SW.api.bindings().catch(() => ({ bindings: [] }));
    applyAppScope(ticket, { bindings: body.bindings || [] });
  }

  // Every build of the selected app, read on demand (#88). Not folded into `loadBuild`: the log
  // reaches megabytes on a long-lived app (~68KB per user turn), Build already reads the slice it
  // draws, and paying for the whole file on every app switch would buy a list nobody had asked to
  // see.
  //
  // Ticketed like the reads beside it, and for the sharper version of the same reason: the route
  // carries no app id, so its answer is only ever "the app that was selected when it was asked".
  // A read that resolves after the creator has moved is answering about an app that is no longer
  // on screen, and it loses here rather than painting (#101).
  // A failure is REPORTED rather than flattened to an empty list, which is the same rule
  // `loadAppList` states 780 lines up about `apps()`: `[]` answers a 500 as readily as it answers
  // an app nobody has built in, and the drawer's empty state is a confident claim about the app.
  // Made on a failed read it is simply false, and it hands the person a dead end — the log is on
  // disk and their builds are fine.
  async function loadAppHistory(ticket = appScopeTicket()) {
    const read = await SW.api.appHistory().then(
      (rows) => ({ rows, failed: false }),
      () => ({ rows: [], failed: true })
    );
    applyAppScope(ticket, { appHistory: read });
  }

  // The plan the panel pins. Two moments move it and nothing else does: a gate turn or a Chat
  // handoff proposes one, and an approve consumes it (the server archives the plan the moment a
  // build reads it, which is what flips the pin from "Plan" to "Working from").
  //
  // `projectPlan`, not `activePlan`: this is plan.md's `{title, markdown, status, steps}`, and
  // `activePlan` is the plan document `loadPlan` fetches. They used to share a key and only got
  // away with it because nothing ever set `thread.planId`, so `loadPlan` never ran.
  async function refreshProjectPlan() {
    const plan = await SW.api.projectPlan();
    state.projectPlan = plan && plan.markdown ? plan : null;
  }

  async function probePreview() {
    const url = `./preview/?t=${Date.now()}`;
    try {
      const res = await fetch(url, { cache: 'no-store' });
      if (res.ok) {
        state.previewSrc = url;
        state.previewStatus = 'ok';
      } else if (res.status === 502) {
        state.previewStatus = 'starting';
      } else {
        state.previewStatus = 'err';
      }
    } catch (err) {
      state.previewStatus = 'starting';
    }
    notify();
  }

  async function deliverTurn(turn, meta = {}, values = {}) {
    // A turn that waits parks itself until the user actually attaches
    // something. That is what makes the manual path feel like a conversation.
    if (turn.waitsForAttachment) {
      state.pendingTurn = { turn, meta };
      notify();
      return;
    }

    state.typing = turn.thinkingLabel || 'Thinking…';
    notify();
    await SW.util.sleep(turn.delayMs || 700);

    for (const attachSpec of turn.attaches || []) {
      await store.attach(attachSpec.resourceId, attachSpec.addedBy || 'sage', attachSpec.rationale, {
        silent: true,
      });
    }
    for (const resourceId of turn.installs || []) {
      await SW.api.addToProject(state.scope.id, resourceId);
    }
    if ((turn.installs || []).length || (turn.attaches || []).length) {
      await loadScopeData();
    }

    state.typing = null;
    state.assistantTurns += 1;

    const blocks = fillTemplate(turn.blocks, values);
    pushMessage({
      id: `local_${Date.now()}`,
      role: 'assistant',
      at: new Date().toISOString(),
      turnId: turn.id,
      blocks,
    });

    if (state.thread) {
      SW.api.appendMessage(state.thread.id, blocks, 'assistant').catch(() => {});
    }

    if (meta.suggestPlan && !state.activePlanId) {
      pushMessage({ id: `sug_${Date.now()}`, role: 'system', blocks: [{ type: 'plan_suggestion' }] });
      if (state.thread) SW.api.patchThread(state.thread.id, { planSuggested: true }).catch(() => {});
    }

  }

  // Both ways out of this builder — switching Project and creating one — end the same way: a URL
  // in another container, and a wait while its session comes up. One Sage Builder is bound to one
  // project volume, so there is no version of this that stays on the page.
  //
  // The builder being left stays running. Stopping it would have to commit, pull, resolve and push
  // first, and could cut off a build mid-turn; coming back is then a resume instead of a reuse.
  async function handOver({ title, detail, failure, start }) {
    const modal = antd.Modal.info({
      title,
      content: detail,
      okButtonProps: { style: { display: 'none' } },
      closable: false,
      maskClosable: false,
    });
    try {
      const opened = await start();
      const projectId = (opened.project && opened.project.id) || opened.project_id;
      let url = opened.running ? opened.open_url : null;
      // A launched or resumed workspace reports Started while its session is still booting, and the
      // builder inside binds its port later still — Domino's proxy answers 502 until it does. The
      // status route waits for both, so this wait is the longer one: ~6 minutes, then say so.
      for (let i = 0; !url && i < 120; i++) {
        await new Promise((r) => setTimeout(r, 3000));
        const s = await SW.api.projectStatus(projectId, opened.workspace_id).catch(() => null);
        if (s && s.running && s.open_url) url = s.open_url;
      }
      if (!url) throw new Error('The workspace is taking longer than expected to start.');
      modal.destroy();
      window.location.replace(url);
      return opened;
    } catch (err) {
      modal.destroy();
      antd.Modal.error({ title: failure, content: String((err && err.message) || err) });
      return null;
    }
  }

  const store = {

    get: () => state,
    getConversationId: conversationId,

    subscribe(fn) {
      listeners.add(fn);
      return () => listeners.delete(fn);
    },

    set(patch) {
      Object.assign(state, patch);
      notify();
    },

    async setBuildMode(mode) {
      if (!['auto', 'ask', 'plan', 'implement'].includes(mode)) return;
      state.buildMode = mode;
      notify();
      try {
        const status = await SW.api.setBuildMode(mode);
        applyModelStatus(status);
        notify();
      } catch (err) {
        antd.message.error(String((err && err.message) || err));
      }
    },

    // Build's override, which the router honours in Plan and Implement and ignores everywhere else
    // (llm_router: Ask is pinned, and Auto follows the phase). `null` clears it and puts the mode
    // back on its pinned slot — which is the "(default)" row in the menu.
    async setBuildModel(pick) {
      const previous = state.buildModel;
      state.buildModel = pick || '';
      notify();
      try {
        const status = await SW.api.setBuildModel(pick || null);
        applyModelStatus(status);
        notify();
      } catch (err) {
        // Put back, unlike the two above it. A refused mode change is visible in the next turn's
        // behaviour; a refused model is not, so a control left showing the pick would name the
        // wrong model for every build after it.
        state.buildModel = previous;
        notify();
        antd.message.error(String((err && err.message) || err));
      }
    },

    // The panel's read. Also its re-verify: a save calls this again, so the reachability check runs
    // against the assignment that just landed rather than against a cached one (ADR-0017).
    async loadAssignments() {
      state.assignmentsLoading = true;
      notify();
      try {
        const panel = await SW.api.modelAssignments();
        state.assignments = panel;
        state.assignmentsError = panel.error || '';
      } catch (err) {
        state.assignmentsError = String((err && err.message) || err);
      } finally {
        state.assignmentsLoading = false;
        notify();
      }
    },

    openAssignments(open) {
      state.assignmentsOpen = Boolean(open);
      notify();
      if (open) this.loadAssignments();
    },

    // Saves immediately and verifies afterwards (ADR-0017): blocking the write on a live gateway
    // call would make a setting refusable for a reason outside the person's control, and the greyed
    // rows already stop the common case at draw time.
    async setAssignment(slot, model) {
      try {
        const status = await SW.api.setModelAssignment(slot, model);
        applyModelStatus(status);
        notify();
        await this.loadAssignments();
      } catch (err) {
        antd.message.error(String((err && err.message) || err));
        // Re-read rather than patch back: the refusal may have been the turn lock, in which case
        // nothing changed, and guessing which of the three rows to revert is how the panel comes to
        // disagree with the catalog.
        await this.loadAssignments();
      }
    },

    async setChatModel(alias, effort) {
      state.model = alias || '';
      state.reasoningEffort = effort || null;
      notify();
      try {
        const status = await SW.api.setChatModel(alias, effort);
        applyModelStatus(status);
        notify();
      } catch (err) {
        antd.message.error(String((err && err.message) || err));
      }
    },

    async init() {
      const [me, projects, charts, starters, notifications, brand, health] = await Promise.all([
        SW.api.me(),
        SW.api.projects(),
        SW.api.charts(),
        SW.api.starters(),
        SW.api.notifications(),
        SW.api.brand().catch(() => state.brand),
        // Extra options for Build's picker, not a health check. Caught, because a gateway that
        // cannot answer this still has four working slots and a picker that must open.
        SW.api.health().catch(() => null),
      ]);
      state.me = me;
      if (brand) {
        state.brand = brand;
        applyBrandChrome(brand);
      }
      state.projects = projects;
      state.canProvision = !!(projects[0] && projects[0].provisioning);
      state.scope = projects[0] || state.scope;
      applyModelStatus(projects[0]);
      state.charts = charts;
      state.starters = starters;
      state.notifications = notifications;
      state.ready = true;
      state.openWeightModels = (health && health.open_weight_models) || [];
      state.resourcesLoading = true;
      notify();
      // A reload during a turn lands here with no stream and no memory of one. Ask the lock, so
      // the composer opens disabled with a Stop beside it rather than taking a question the
      // server is about to refuse.
      await Promise.all([loadScopeData(), loadThreadList(), store.refreshTurnState()]);
    },

    // Scope --------------------------------------------------------------

    // Scope survives mode switches. This is the continuity mechanism.
    async setScope(project, options = {}) {
      if (state.scope.id === project.id) return;
      // A conversation that belongs to the project we are moving into stays
      // open. Opening a deep link resolves the app and the conversation at the
      // same time, and whichever settled the scope used to wipe the other.
      const keepThread =
        state.thread && (state.thread.projectId || NO_SCOPE.id) === project.id;

      state.scope = project;
      state.activePlanId = null;
      state.activePlan = null;
      // The apps belong to the Project being left, and a stale list is worse than none: the rail
      // would offer rows that select an app this Builder is not attached to.
      state.apps = [];
      // Through the gate, so a `/bindings` or `/project` read still in flight for the Project being
      // LEFT cannot land afterwards and describe an app this Builder is no longer attached to. This
      // happens now, so it takes the newest place in the queue and everything outstanding loses.
      applyAppScope(appScopeTicket(), { activeApp: null });
      state.railAppFilter = null;
      if (!keepThread) {
        state.thread = null;
        state.pendingConversation = false;
        state.messages = [];
        state.attachments = [];
        state.touched = [];
        state.assistantTurns = 0;
        state.pendingTurn = null;
      }
      state.scopeFlash = true;
      notify();
      setTimeout(() => { state.scopeFlash = false; notify(); }, 500);

      if (!options.silent) {
        antd.message.info(`Switched scope to ${project.name}`);
      }
      await Promise.all([loadScopeData(), loadThreadList()]);
    },

    // Switching Project means LEAVING this container (#47). One Sage Builder is bound to one
    // project volume, so the viewer's work in another Project lives in their builder there — this
    // attaches it (reuse, resume, or create) and hands the browser over, the same move the door
    // makes. A collaborator's builder in that Project is never taken over.
    async attachProject(project) {
      if (!project || project.current) return;
      await handOver({
        title: `Opening ${project.name}`,
        detail: 'Starting your workspace there. This takes about a minute if it was stopped.',
        failure: SW.brand.text("{assistantName} couldn't open {name}", { name: project.name }),
        start: () => SW.api.openProject(project.id),
      });
    },

    // New project is a real verb (#46): a private sage-* repo, the template seeded and pushed, a
    // git-based Domino project, then this viewer's builder in it. The name typed here becomes the
    // chip there — it rides into the repo, because the Domino project has to be named sage-<slug>
    // for Sage to find it again.
    async createProject(name) {
      const trimmed = String(name || '').trim();
      if (!trimmed) return null;   // the picker disables Create, so this is only belt-and-braces
      return handOver({
        title: `Creating ${trimmed}`,
        detail: 'Setting up the repository and starting your workspace. This takes about a minute.',
        failure: SW.brand.text("{assistantName} couldn't create {name}", { name: trimmed }),
        start: () => SW.api.createProject(trimmed),
      });
    },

    // Opening a thread adopts its project, so you never attach a resource
    // from the wrong project to an old conversation.
    async adoptThreadScope(thread) {
      if (!thread.projectId) return;
      const target = state.projects.find((p) => p.id === thread.projectId);
      if (target && target.id !== state.scope.id) {
        await store.setScope(target);
      }
    },

    // Same rule for an app, so a link to one lands in the project that owns it
    // rather than in whatever scope happened to be open.
    async adoptAppScope(app) {
      const target = state.projects.find((p) => p.id === app.projectId);
      if (target && target.id !== state.scope.id) {
        await store.setScope(target, { silent: true });
      }
      return target;
    },

    async reloadProjects() {
      state.projects = await SW.api.projects();
      notify();
    },

    // Dock ---------------------------------------------------------------

    toggleDock(tab = 'resources') {
      state.dockTab = state.dockTab === tab ? null : tab;
      if (!state.dockTab) state.panelFilter = null;
      notify();
    },

    openDock(tab = 'resources') {
      state.dockTab = tab;
      notify();
    },

    toggleRail() {
      state.railHidden = !state.railHidden;
      notify();
    },

    // Called when a script turn has opensPanel. Sage asking you to pick a kind
    // of thing is a browse task, so it opens the catalogue scoped to that kind
    // rather than filtering a panel that may not contain the answer yet.
    focusPanel(kind) {
      state.dockTab = 'resources';
      state.panelFilter = kind;
      state.catalogOpen = true;
      state.catalogKind = kind;
      notify();
    },

    clearPanelFilter() {
      state.panelFilter = null;
      notify();
    },

    openCatalog(kind) {
      state.catalogOpen = true;
      state.catalogKind = kind || null;
      notify();
    },

    closeCatalog() {
      state.catalogOpen = false;
      state.catalogKind = null;
      state.panelFilter = null;
      notify();
    },

    previewResource(resourceId) {
      state.previewResourceId = resourceId;
      notify();
    },

    // Project membership ---------------------------------------------------

    // The only way anything becomes usable here. Everything else — chips,
    // @-mentions, an app's Bindings — points at something that already
    // went through this.
    async addToProject(resource, options = {}) {
      const result = await SW.api.addToProject(state.scope.id, resource);
      await loadScopeData();
      if (!options.silent && result.added) {
        antd.message.success(`${resource.name} is now in ${state.scope.name}`);
      }
      return result;
    },

    async removeFromProject(resource) {
      const scopeName = state.scope.name;
      return new Promise((resolve) => {
        antd.Modal.confirm({
          title: `Remove ${resource.name} from ${scopeName}?`,
          content: SW.brand.text(
            'It leaves this project. You can add it again from Browse {platformName}.'
          ),
          okText: 'Remove',
          okButtonProps: { danger: true },
          onOk: async () => {
            try {
              await SW.api.removeFromProject(state.scope.id, resource.id);
            } catch (err) {
              // A Built App still binds it, so the removal is refused. A toast saying so is a
              // dead end — name the apps that still bind it and the source that still uses it,
              // because that is the change the creator has to make first, and the app refusing is
              // often not the one on screen. (Removing the record is not removing the code: an app
              // whose Summarise button calls an Alias it no longer has keeps the button.)
              const apps = (err.payload && err.payload.apps) || [];
              const refs = (err.payload && err.payload.refs) || [];
              if (apps.length) {
                const subject = SW.brand.text(
                  apps.length > 1 ? '{builtAppPlural}' : 'one {builtApp}'
                );
                const fix = refs.length
                  ? ` Used in: ${refs.join(', ')}. Remove those uses in Build, then remove it here.`
                  // The code word this used to say names the app-scoped pair in `service.py` and
                  // never on screen, and the act it points at is now a control of its own (#96).
                  : ' Remove it from that app in Build, then remove it here.';
                antd.Modal.info({
                  title: `${resource.name} is still used by ${subject}`,
                  content: `${err.message}, so it can't leave ${scopeName} yet.` + fix,
                  okText: 'Got it',
                });
              } else {
                antd.message.error(err.message);
              }
              resolve(false);
              return;
            }
            const tid = conversationId();
            const drop = (state.attachments || []).filter(
              (a) => a.resourceId === resource.id || a.parentId === resource.id
            );
            if (tid) {
              await Promise.all(
                drop.map((a) => SW.api.removeFromConversation(tid, a.id).catch(() => null))
              );
            }
            state.attachments = (state.attachments || []).filter(
              (a) => a.resourceId !== resource.id && a.parentId !== resource.id
            );
            await loadScopeData();
            antd.message.info(`${resource.name} is out of ${scopeName}`);
            resolve(true);
          },
          onCancel: () => resolve(false),
        });
      });
    },

    // Out of the selected Built App ---------------------------------------
    //
    // The third of the three removal scopes. Both acts live here — beside the list that owns the
    // scope (ADR-0011) — and the Build header keeps its pointers rather than growing a second copy
    // of either guard.
    //
    // Neither touches the Conversation's chips: the app stops being allowed to reach the Resource
    // while the chip stays on the composer. That is the mirror of the sentence
    // `removeFromConversation` draws below, and it is correct rather than a leak.

    async removeBindingFromApp(binding) {
      // The app this act NAMES, captured where the question is asked. Neither removal route carries
      // an app id — both resolve through whatever the server has selected — so a confirm left open
      // while the selection moved would take the Binding out of an app this modal never mentioned.
      const asked = state.activeApp;
      const where = appScopeName();
      const name = binding.display_name || binding.name || binding.id;
      const copy = UNBIND_COPY[binding.kind] || UNBIND_PLAIN;
      return new Promise((resolve) => {
        antd.Modal.confirm({
          title: `Remove ${name} from ${where}?`,
          content: `${where} ${copy.stops}, and there is no undo. ${copy.cost}`,
          // Names its scope, like every other removal label. The sibling confirm above says a bare
          // "Remove"; that one predates the rule (ADR-0011) and is not this ticket's to move.
          okText: `Remove from ${where}`,
          okButtonProps: { danger: true },
          onOk: async () => {
            let result;
            // The app the act is issued against, read here rather than where the confirm opened:
            // the race this answers is the length of the REQUEST, and a modal can sit open far
            // longer than that (#101).
            const gen = appGen;
            // Refuse rather than act on the wrong app. The title is a promise about which app loses
            // the Binding, and a modal can sit open for as long as somebody leaves it there.
            //
            // This NARROWS the window to one request round trip; it does not close it, because the
            // server still resolves the app itself and could be moved between this check and the
            // handler. Closing it means the route naming its app, which is a route-shape change
            // rather than a guard.
            if (!asked || !state.activeApp || state.activeApp.id !== asked.id) {
              antd.message.warning(
                `Nothing was removed. The selected app changed to ${appScopeName()} while this was `
                + `open, and this removal named ${asked ? asked.name : 'another app'}.`
              );
              resolve(false);
              return;
            }
            try {
              result = await SW.api.unbind(binding.kind, binding.id);
            } catch (err) {
              antd.message.error(`${name} could not be removed: ${err.message}`);
              resolve(false);
              return;
            }
            // The route answers with the list it just wrote, so nothing is re-read to find out
            // what happened — see ADR-0010 on what may render per app switch. That also makes it
            // newer than any read in flight, which is why the ticket is taken HERE: a `/bindings`
            // read that started before the unbind and lands after it would put the Binding back.
            applyAppScope(appScopeTicket(gen), {
              bindings: result.bindings || [],
              appRemoval: removalNotice(where, result.name || name, result.refs || []),
            });
            notify();
            resolve(true);
          },
          onCancel: () => resolve(false),
        });
      });
    },

    // No confirm, and the asymmetry is the point: re-attaching is one click on the same Dataset
    // file, while re-binding costs the Scope. A gate over the cheap one would teach that they cost
    // the same.
    async removeAttachmentFromApp(attachment) {
      const where = appScopeName();
      const name = (attachment.file || attachment.path || '').split('/').pop();
      let result;
      // The app the act is issued against — see the sibling above.
      const gen = appGen;
      try {
        result = await SW.api.detachFile(attachment.path);
      } catch (err) {
        antd.message.error(`${name} could not be removed: ${err.message}`);
        return false;
      }
      // What `detach_file` actually does: the declaration, the app's copy under public/data/ and any
      // raw copy the agent leaked into the app tree all go, and the Dataset bytes stay. The last
      // half can only be promised when there is a Dataset to name.
      //
      // Keyed on `dataset_id`, never on `dataset`. Every entry carries a `dataset`, the rehydrated
      // ones included — `_rehydrate_attached` fills it from the SYMLINK'S PARENT DIRECTORY, so for
      // those it is a path fragment that merely looks like a Dataset name. `dataset_id` is the only
      // field that says a real Dataset was recorded, and naming a directory as the source the bytes
      // are safe in is exactly the invention ADR-0011 forbids.
      const source = attachment.dataset_id
        ? `The app's copy is gone and the file stays in ${attachment.dataset}.`
        : SW.brand.text(
          "The app's copy is gone. This file records no {dataset}, so there is no source to name."
        );
      const leaked = result.removed_copies || [];
      const copies = leaked.length ? ` A copy left in ${leaked.join(', ')} went with it.` : '';
      // The route hands back no manifest, so the list is the one on screen minus what just went —
      // filtered HERE, off whatever the newest read left, and installed under the act's own ticket
      // so a `/project` read that started before the detach cannot put the file back (#101).
      applyAppScope(appScopeTicket(gen), {
        appAttachments: (state.appAttachments || []).filter((a) => a.path !== attachment.path),
        appRemoval: removalNotice(where, name, result.refs || [], `${source}${copies}`),
      });
      notify();
      return true;
    },

    dismissAppRemoval() {
      applyAppScope(appScopeTicket(), { appRemoval: null });
      notify();
    },

    // A prompt put in front of the person, unsent. A control that fired the turn itself could be
    // refused by the per-project turn lock, and would put work past a plan gate nobody read.
    seedComposer(text) {
      state.composerSeed = text;
      notify();
    },

    // Read once by the composer, which holds the text from then on. No `notify` — the box already
    // has it, and telling every listener would only redraw the screen to say the same thing.
    clearComposerSeed() {
      state.composerSeed = null;
    },

    // Conversation context ------------------------------------------------

    async attach(resourceId, addedBy = 'user', rationale, options = {}) {
      // Putting something in context is intent, the same as typing, so it opens
      // a conversation. Only navigation is free.
      if (!conversationId()) await store.newThread({ appId: options.appId });
      const attachment = await SW.api.addToConversation(
        conversationId(),
        resourceId,
        addedBy,
        rationale
      );
      if (!state.attachments.some((a) => a.id === attachment.id)) {
        state.attachments = [...state.attachments, attachment];
      }
      state.panelFilter = null;
      notify();

      // Pointing at something from the catalogue brings it into the project on
      // the way in, so the panel has to hear about its new member.
      if (attachment.joinedProject) {
        await loadScopeData();
        if (!options.silent) {
          antd.message.success(`${attachment.resourceName} added to ${state.scope.name}`);
        }
      }

      // The assistant acknowledges manual picks; this closes the loop between
      // the panel and the conversation.
      if (addedBy === 'user' && !options.silent) {
        store.acknowledgeAttachment(attachment, { quiet: options.quiet });
      }
      return attachment;
    },

    // Out of this conversation's context. The resource stays in the project, and
    // the app keeps needing it — that separation is the whole point of the split.
    //
    // Named for the call it makes, and for the scope it acts on. It used to be `detach`, which is
    // also the backend's word for removing an app Attachment — one word over the two scopes #84
    // and the glossary's **Session context** entry exist to keep apart (ADR-0011).
    async removeFromConversation(attachment) {
      await SW.api.removeFromConversation(conversationId(), attachment.id);
      state.attachments = state.attachments.filter((a) => a.id !== attachment.id);
      notify();
      // The one moment the two scopes visibly disagree, said out loud so it does not read as a
      // leak: the chip is gone and the selected app goes on being allowed to reach the Resource.
      // See `SW.util.bindingId` for why this is a join rather than an id comparison.
      const app = state.activeApp;
      const stillNeeded = app
        && (state.bindings || []).some((b) => SW.util.bindingId(b) === attachment.resourceId);
      antd.message.info(
        stillNeeded
          ? `${attachment.resourceName} is out of this conversation. ${app.name} still needs it.`
          : `${attachment.resourceName} is out of context — still in ${state.scope.name}.`
      );
    },

    removeResourceFromConversation(resourceId) {
      const attachment = state.attachments.find((a) => a.resourceId === resourceId);
      if (attachment) return store.removeFromConversation(attachment);
      return Promise.resolve();
    },

    // @-mentions and panel picks land in the same place: this conversation's context.
    // Mentions pass quiet: the chip in the composer is already the feedback,
    // so Sage speaking up before the message is even sent just adds noise.
    addToContext(resource, options = {}) {
      if (!resource || !resource.id) return Promise.resolve();
      if (state.attachments.some((a) => a.resourceId === resource.id)) return Promise.resolve();
      state.resourceIndex[resource.id] = { ...(state.resourceIndex[resource.id] || {}), ...resource };
      return store.attach(resource.id, 'user', undefined, options);
    },

    acknowledgeAttachment(attachment, options = {}) {
      const pending = state.pendingTurn;
      const wantedKind = pending && pending.turn.waitsForAttachment;
      const meta = SW.util.RESOURCE_META;
      const matches =
        wantedKind &&
        (wantedKind === attachment.resourceKind ||
          (meta[wantedKind] &&
            meta[attachment.resourceKind] &&
            meta[wantedKind].group === meta[attachment.resourceKind].group));

      if (matches) {
        state.pendingTurn = null;
        notify();
        // The wait is over, so the flag has to come off before the turn goes
        // back through delivery — otherwise its own guard parks it again and
        // the thread sits on "waiting to attach" forever.
        const { waitsForAttachment, ...turn } = pending.turn;
        deliverTurn(turn, pending.meta, { resourceName: attachment.resourceName });
        return;
      }

      if (!options.quiet && state.thread && state.messages.length) {
        pushMessage({
          id: `ack_${Date.now()}`,
          role: 'assistant',
          at: new Date().toISOString(),
          blocks: [
            {
              type: 'text',
              value: `Got it — I can see **${attachment.resourceName}**. We'll use that.`,
            },
          ],
        });
      }
    },

    // Chat ---------------------------------------------------------------

    async newThread(options = {}) {
      const thread = await SW.api.createThread(
        state.scope.id || null,
        options.title || 'New chat',
        options.appId
      );
      state.thread = thread;
      // The conversation the rail's placeholder was standing in for now exists, so the flag has
      // done its job. Clearing it HERE and not in `clearConversation` is the whole distinction:
      // this is a conversation opening, that is one closing, and only the first ends a pending
      // one. Left set, it would outlive this thread and draw a placeholder nobody asked for the
      // next time anything cleared — deleting this very conversation does exactly that.
      state.pendingConversation = false;
      state.messages = [];
      state.activePlanId = null;
      state.activePlan = null;
      state.touched = [];
      state.assistantTurns = 0;
      state.pendingTurn = null;
      state.planViewerId = null;
      // A turn still running in the conversation being left owns its own label, not this view's.
      // Leaving it set drew "Thinking…" under an empty new conversation that was doing nothing.
      state.typing = null;
      notify();
      // Starting from an app copies what that app needs into the conversation,
      // so the context it opens with is the app's rather than a stranger's.
      await Promise.all([refreshAttachments(), loadThreadList()]);
      return thread;
    },

    // What Chat draws for one Conversation, which is where the viewer's conversation view decides
    // something (#52). Split reads the Chat half, exactly as the Workbench does today. Unified
    // reads both halves merged, so a Conversation that only ever happened in Build stops opening
    // on the landing screen as if it had never happened.
    //
    // The rail's app list comes with it, because the app cards in a merged transcript read publish
    // state off it — one read for the whole transcript rather than one per card. Build already
    // loads it; Chat had no reason to until now.
    async conversationMessages(thread) {
      const chatOnly = () =>
        historyToMessages(thread.history || thread.messages || [], thread.handoff);
      if (SW.prefs.get('conversationView') !== 'unified') return chatOnly();
      // A merged read that failed must not read as a Conversation that never happened. The Chat
      // half is already in hand — it came with the thread — so the fallback is the split view,
      // which is short of the Build half rather than short of everything.
      const [history] = await Promise.all([
        SW.api.conversation(thread.id).catch(() => null),
        loadAppList().catch(() => {}),
      ]);
      return history === null ? chatOnly() : mergedHistoryToMessages(history, thread.handoff);
    },

    // Which Built App a `#/build/<id>` link means when it names none: the one this Conversation
    // bound last. Without this the link lands on whatever app the server happens to have selected,
    // which is a different app for every viewer and every visit — so one link shows two people two
    // different transcripts, and an old link stops going where it went.
    //
    // Blind to the conversation view on purpose. Which app is selected is STORED, and #52's
    // preference decides only what is RENDERED, so the app a link resolves to cannot depend on it.
    //
    // A Conversation that bound no app resolves to nothing rather than guessing, and the selected
    // app stays — which is what Build did before this ticket.
    async resolveConversationApp(threadId) {
      if (!threadId) return null;
      // ADR-0009: the Conversation's newest BOUND handoff entry names the app. Only a confirmed
      // handoff writes `appId`, so its presence is what "bound" means here. The thread is usually
      // the one already open, and reusing it is what keeps this off the network.
      const thread = state.thread && state.thread.id === threadId
        ? state.thread
        : await SW.api.thread(threadId).catch(() => null);
      const handoff = (thread && thread.handoff) || null;
      if (handoff && handoff.status === 'bound' && handoff.appId) return handoff.appId;
      // A Built App started inside Build was never handed off (#74), so no entry can name it and
      // the ADR's rule has nothing to answer with. Its turns are the only record that this
      // Conversation drove it, so they are what names it.
      const history = await SW.api.conversation(threadId).catch(() => null);
      if (!history) return null;
      let bound = '';
      for (const row of history) {
        if (row.half === 'build' && row.app) bound = row.app;
      }
      return bound || null;
    },

    async openThread(threadId) {
      // Click B then A and two of these are in flight. Unguarded, whichever server response lands
      // last wins, so the store can settle on B while the route and the rail say A — and
      // `sendMessage` reads `state.thread`, so the next message is posted into the conversation
      // nobody is looking at. Every await re-checks, and the view is written in one go afterwards
      // so a superseded open can never leave half of itself on screen.
      const gen = ++openSeq;
      const thread = await SW.api.thread(threadId);
      if (gen !== openSeq) return null;
      await store.adoptThreadScope(thread);
      if (gen !== openSeq) return null;
      const messages = await store.conversationMessages(thread);
      if (gen !== openSeq) return null;
      state.thread = thread;
      state.pendingConversation = false;
      state.messages = messages;
      state.activePlanId = thread.planId || null;
      state.touched = thread.touched || [];
      state.assistantTurns = state.messages.filter((m) => m.role === 'assistant').length;
      state.pendingTurn = null;
      state.planViewerId = null;
      state.typing = null;
      notify();
      await refreshAttachments();
      if (gen !== openSeq) return thread;
      if (thread.planId) await store.loadPlan(thread.planId);
      return thread;
    },

    // No conversation open. Not the same as an empty one — nothing is persisted
    // and nothing shows up in a list.
    //
    // `pendingConversation` is deliberately NOT cleared here. Build's route effect calls this
    // on every arrival at a conversation-less `#/build`, which is the very navigation
    // `newConversation` performs — clearing the flag here would wipe the rail's row on the way
    // in, and the button would look dead again. What ends a pending conversation is a real one
    // opening (`openThread`) or leaving the Project (`switchScope`).
    clearConversation() {
      state.thread = null;
      state.messages = [];
      state.buildHistory = [];
      state.conversationChat = [];
      buildSeq = null;
      applyBuildTranscript();
      state.attachments = [];
      state.touched = [];
      state.assistantTurns = 0;
      state.pendingTurn = null;
      state.typing = null;
      notify();
    },

    // "New conversation", from the rail's button or the palette. Two things, and the second is
    // why this is an action rather than the bare `clearConversation` both call sites used to
    // make: the clear is what makes the button WORK (see clearConversation — the route may
    // already be the one we are going to), and the flag is what makes it LOOK like it worked.
    // Without a row, a press on a conversation-less route changed nothing on screen except the
    // centre pane someone was not looking at.
    //
    // Still nothing written. The first message is what opens a conversation, here as before.
    newConversation() {
      store.clearConversation();
      state.pendingConversation = true;
      notify();
    },

    loadApps: loadAppList,

    // New app in the Build rail. The server mints, seeds and selects it, so this reloads the whole
    // of Build the way selectApp does rather than lighting a row: the transcript, the Bindings, the
    // plan pin and the preview all belong to the app, and the one being left owns none of the new
    // one's. Reloading here rather than leaving it to the route is deliberate — arriving from a
    // conversation-less `#/build` changes neither of BuildMode's effect keys, so nothing would fire.
    //
    // The route is told LAST, and carries `?app=` with no conversation segment: the app is what is
    // new and it starts with no Thread behind it. Typing opens one (see sendBuildPrompt), and the
    // plan gate fires on that first turn because the app has not been built (#74).
    async createApp() {
      if (creating) return null;
      creating = true;
      try {
        const app = await SW.api.createApp();
        store.clearConversation();
        await store.loadBuild();
        state.activePlanId = null;
        state.activePlan = null;
        notify();
        SW.router.go(`#/build?app=${app.id}`);
        return app;
      } catch (err) {
        // The one refusal worth a sentence is the turn lock's, and the server writes it.
        antd.message.warning(
          err.message || SW.brand.text('{assistantName} could not start a new {builtApp}.')
        );
        return null;
      } finally {
        creating = false;
      }
    },

    // Which app Build has in front of it. Looking is free and reversible, so this changes freely
    // and never implies a change to either app — a build already running is not stopped or refused
    // by it, it goes on in the app it started in and the rail marks that row (#77).
    async selectApp(app) {
      const id = typeof app === 'string' ? app : app && app.id;
      // Already there, or already on the way there. `selecting` is what keeps a second asker from
      // racing the first — see where it is declared.
      if (!id || id === selecting || (state.activeApp && state.activeApp.id === id)) {
        return state.activeApp;
      }
      selecting = id;
      try {
        await SW.api.selectApp(id);
        // Reloads the app list with it: the transcript, the Bindings, the plan pin and the preview
        // are all the app's, so switching reloads the whole of Build, not one row's flag.
        await store.loadBuild();
        const selected = state.activeApp;
        state.activePlanId = (selected && selected.planId) || null;
        state.activePlan = null;
        notify();
        if (selected && selected.planId) await store.loadPlan(selected.planId);
        return selected;
      } catch (err) {
        antd.message.warning(
          err.message || SW.brand.text('{assistantName} could not switch to that {builtApp}.')
        );
        return state.activeApp;
      } finally {
        selecting = null;
      }
    },

    // The name is the mutable half of an app's identity; its id names the directory and cannot
    // move, because a published App's entry point is fixed when the App is created.
    async renameApp(id, name) {
      await SW.api.patchApp(id, { name });
      // The conversation rail names this app too — a tag on every conversation that changed it —
      // and the server has just relabelled those. Read the rail back with the app list, or the
      // chips go on saying the old name until something else happens to reload it.
      await Promise.all([loadAppList(), loadThreadList()]);
    },

    // Delete a Built App (#76). Nothing here decides anything: the offer was made in the rail and
    // `deleteDominoApp` is the answer the person gave, and what actually happened comes back from
    // the server rather than being assumed from what was asked.
    //
    // The whole of Build is reloaded, not one row: the server moves Build onto the app that is left
    // when the deleted one was in front of you, and the transcript, Bindings, plan pin and preview
    // all belong to whichever app that is. The route is told LAST and names the new app, because
    // the one in `?app=` no longer exists — left alone, BuildMode's effect would ask to select a
    // deleted app and get a 404 for its trouble.
    async deleteApp(id, { deleteDominoApp = false } = {}) {
      const out = await SW.api.deleteApp(id, { deleteDominoApp });
      await store.loadBuild();
      const selected = state.activeApp;
      state.activePlanId = (selected && selected.planId) || null;
      state.activePlan = null;
      notify();
      if (selected && selected.planId) await store.loadPlan(selected.planId);
      // Through the rail's own route grammar, so the conversation on screen survives: deleting an
      // abandoned app is not a reason to close the Thread somebody is talking in.
      // Delete the ONLY app and there is no app left to name. Leaving `?app=<deletedId>` in the
      // hash sends BuildMode's effect off to select an app the server no longer has, so the 404
      // toast lands right after the delete succeeded.
      if (selected) SW.router.go(SW.appRoute(selected));
      else SW.router.go(`#/build${state.thread ? `/${state.thread.id}` : ''}`);
      return out;
    },

    // Ship the selected Built App as a live Domino App (#89). Nothing is passed, and nothing here
    // decides which app: the server publishes the one Build is pointed at, which is the one the
    // confirm named. An id sent from here would be a second answer to that question, and shipping
    // one app's code over another's URL is the failure #70 exists to stop.
    //
    // The list is re-read rather than patched from the response, because three things the row
    // carries move on a publish — `published`, `publishedAt`, and the URL `Open app` opens — and
    // the row is where the header reads every one of them. `loadAppList` rather than `loadBuild`:
    // the selection has not moved, so the Bindings and the attachments are the same two answers
    // they were a moment ago and are not worth asking for again.
    //
    // Failures are thrown on rather than reported here. The caller is a confirm that has to stay
    // open on a refusal, and it is the only thing that knows the app's name to say it with.
    async publishApp(asked) {
      // The app this act NAMES, handed in by the confirm that named it. Refuse rather than ship
      // the other one: the title is a promise about which app goes out, and a modal can sit open
      // for as long as somebody leaves it there — long enough for the 30-second app poll to move
      // the selection under a question nobody has answered yet.
      //
      // This NARROWS the window to one request round trip rather than closing it, for the reason
      // `removeBindingFromApp` gives: the server still resolves the app itself and could be moved
      // between this check and the handler. Closing it means the route naming its app, which is a
      // route-shape change (#100) rather than a guard.
      if (asked && (!state.activeApp || state.activeApp.id !== asked.id)) {
        const moved = new Error(
          `Nothing was published. The selected app changed to ${appScopeName()} while this was `
          + `open, and this publish named ${asked.name}.`
        );
        // Told apart from a server failure by the caller: this question is void and its modal
        // should go, where a refusal is worth reading with the modal still open behind it.
        moved.moved = true;
        throw moved;
      }
      try {
        return await SW.api.publish();
      } finally {
        // In a `finally`, because a publish can fail AFTER it has succeeded: `record_domino_app`
        // and `mark_published` are written before the response is built, so a 502 on the way out
        // still leaves a live Domino App recorded. Re-reading only on success would leave the
        // header offering "publish it first" for an app that is already deployed.
        await loadAppList();
      }
    },

    clearApp() {
      applyAppScope(appScopeTicket(), { activeApp: null });
      state.activePlanId = null;
      state.activePlan = null;
      notify();
    },

    // A Build turn belongs to a conversation: it opens that conversation's own OpenCode
    // session, and its events are tagged with it in `.sage/history.jsonl`. Typing is intent, so
    // it opens one, the same way Chat does.
    // `skipResetGate` is only ever set by a button on a reset offer (#36), and only for the prompt
    // that offer was about: the gate already stopped this request once and the user answered it, so
    // re-matching it would hand back the same offer forever. `skipIncomingGate` says the same of an
    // incoming-changes offer (#78), and is set by both of that offer's buttons.
    // A second send no longer bounces off `state.buildRunning` (#79). The server takes the turn and
    // holds it in line on this request's own connection, so this promise stays alive for as long as
    // the wait plus the turn — and a tab can have several of them at once.
    async sendBuildPrompt(text, { skipResetGate = false, skipIncomingGate = false } = {}) {
      if (!text.trim()) return null;
      if (!state.thread) await store.newThread();
      state.buildTurnMode = state.buildMode;
      // Echo what the server will write to the transcript, so live and reloaded read the same. For a
      // click that is the click, not the request — the request is already a bubble above the offer,
      // and repeating it would say the user asked twice (see build_stream's `user_text`).
      const bubble = skipResetGate || skipIncomingGate ? 'Build it.' : text;
      // The app this turn is for. Switching Built App mid-build is allowed and the build carries on
      // server-side (#77), so the events below have to be checked against this before they are
      // appended — otherwise one app's build writes itself into another app's transcript. The
      // rail's Building mark is what reports the turn once the person has moved on.
      const turnApp = state.activeApp && state.activeApp.id;
      const movedOn = () => state.activeApp && state.activeApp.id !== turnApp;
      // The queue's two ends of this send: `ticket` is what a Cancel would name, and `unran` is
      // whether the turn ended without ever running — which is the only case where the bubble
      // drawn just below has to come back off the screen again.
      let ticket = '';
      let unran = false;
      appendBuildRow({ type: 'user', text: bubble });
      liveBuildTurns += 1;
      state.buildRunning = true;
      state.buildTyping = 'Working…';
      notify();
      try {
        // The whole turn, not just the sentence: an @mention names something the server has to be
        // handed as a path or an identity, because the word alone reaches the agent as a word.
        const refs = collectTurnRefs(text);
        const res = await fetch('./api/project/build/stream', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({
            prompt: text, conversation: state.thread.id, skipResetGate, skipIncomingGate,
            mentions: refs.mentions, resources: refs.resources,
          }),
        });
        if (!res.ok) {
          const payload = await res.json().catch(() => ({}));
          throw new Error(payload.error || payload.message || res.statusText);
        }
        let stopped = false;
        await readSSE(res, (ev) => {
          if (!ev) return;
          if (ev.type === 'stopped') stopped = true;
          // The queue's own two rows, which belong to this send rather than to the app on screen —
          // so they are read before the rail check below, not after it.
          if (ev.type === 'pending') { ticket = ev.ticket; queueTurn(ev, 'build'); notify(); return; }
          if (ev.contextChanged) { unran = true; store.seedComposer(ev.prompt || text); }
          if (ev.type === 'done' && ev.decision === 'cancelled') unran = true;
          // Once the rail has moved on, these events describe an app that is no longer on screen.
          if (movedOn()) return;
          applyBuildEvent(ev);
          notify();
        });
        if (stopped) await store.loadBuild({ keepPreview: true });
      } catch (err) {
        applyBuildEvent({ type: 'error', message: String(err.message || err) });
      } finally {
        liveBuildTurns -= 1;
        dropQueuedTurn(ticket);
        // Not `false`: this tab can have several turns alive at once now, and the first one to
        // unwind used to clear a flag the others were still relying on. Any of them still here
        // means a turn is running in this project — its own, or the one it is queued behind.
        state.buildRunning = liveBuildTurns > 0;
        state.buildTyping = state.buildRunning ? state.buildTyping : null;
        notify();
        // Reload rather than keep a half-turn on screen: the transcript showing now is the other
        // app's, and it was deliberately never given this turn's events. A turn that never ran gets
        // the same treatment for the opposite reason — the send optimistically drew a bubble for a
        // question the server never recorded, and the text is back in the composer instead.
        if (movedOn() || unran) await store.loadBuild({ keepPreview: true });
        await Promise.all([probePreview(), refreshBindings()]);
        notify();
      }
    },

    // Starting over is its own action (#36). The three exits below are the answers to a reset offer,
    // and `resetApp` is also what the composer's own Reset app control calls. Reloading the
    // transcript afterwards is what retires the offer: the server's copy carries no `live`, so the
    // buttons go with it and the same offer can't be answered twice.
    async resetApp() {
      if (state.buildRunning) throw new Error('A build is running. Stop it first, then reset.');
      await SW.api.resetApp();
      await store.loadBuild();
    },

    async resetAndBuild(prompt) {
      await store.resetApp();
      return store.sendBuildPrompt(prompt, { skipResetGate: true });
    },

    // The two answers to an incoming-changes offer (#78). Both set `skipIncomingGate`, because both
    // ARE the answer: pulling settles the question by merging, keeping building settles it by
    // deciding to merge later. Neither is asked again until somebody pushes something new.
    //
    // A pull can take a while — it resolves conflicts with the agent — and it can fail on a repo
    // with no remote, so the build only follows a pull that worked.
    async pullAndBuild(prompt) {
      const result = await SW.api.syncProject();
      if (result.status === 'conflict-unresolved' || result.status === 'error') {
        throw new Error(
          result.detail || SW.brand.text('{assistantName} could not pull the latest changes.')
        );
      }
      await Promise.all([store.loadApps({ cascade: false }), store.loadBuild({ keepPreview: true })]);
      return store.sendBuildPrompt(prompt, { skipIncomingGate: true });
    },

    async buildWithIncoming(prompt) {
      await store.loadBuild({ keepPreview: true });
      return store.sendBuildPrompt(prompt, { skipIncomingGate: true });
    },

    async buildWithoutReset(prompt) {
      await store.loadBuild({ keepPreview: true });
      return store.sendBuildPrompt(prompt, { skipResetGate: true });
    },

    // The one answer to a build-stalled offer (#39): ask again. No gate is skipped, because none
    // was answered — a stalled turn is one that already got past them, and anything that changed
    // while it hung deserves to be asked about again.
    async retryStalledBuild(prompt) {
      await store.loadBuild({ keepPreview: true });
      return store.sendBuildPrompt(prompt);
    },

    async approveBuild(answers, planEdits, planId) {
      if (!state.thread) await store.newThread();
      // Same rule as sendBuildPrompt, twice over: an approve is a build turn, so the person can
      // move to another Built App while it streams (#77) and it waits in line rather than being
      // refused when one is already going (#79).
      const turnApp = state.activeApp && state.activeApp.id;
      const movedOn = () => state.activeApp && state.activeApp.id !== turnApp;
      let ticket = '';
      let unran = false;
      appendBuildRow({ type: 'user', text: 'Approved the plan.' });
      liveBuildTurns += 1;
      state.buildRunning = true;
      state.buildTyping = 'Building…';
      notify();
      try {
        const payload = { answers: answers || '', conversation: state.thread.id };
        if (planEdits) payload.plan_edits = planEdits;
        // Which document this card's plan came from. Without it the server has to assume the newest
        // document is the one being approved, and a plan drafted by hand since then breaks that.
        if (planId) payload.plan_id = planId;
        const res = await fetch('./api/project/build/approve', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify(payload),
        });
        if (!res.ok) {
          const body = await res.json().catch(() => ({}));
          throw new Error(body.error || body.message || res.statusText);
        }
        let stopped = false;
        await readSSE(res, (ev) => {
          if (!ev) return;
          if (ev.type === 'stopped') stopped = true;
          if (ev.type === 'pending') { ticket = ev.ticket; queueTurn(ev, 'build'); notify(); return; }
          if (ev.contextChanged || (ev.type === 'done' && ev.decision === 'cancelled')) unran = true;
          if (movedOn()) return;
          applyBuildEvent(ev);
          notify();
        });
        if (stopped) await store.loadBuild({ keepPreview: true });
      } catch (err) {
        applyBuildEvent({ type: 'error', message: String(err.message || err) });
      } finally {
        liveBuildTurns -= 1;
        dropQueuedTurn(ticket);
        state.buildRunning = liveBuildTurns > 0;
        state.buildTyping = state.buildRunning ? state.buildTyping : null;
        notify();
        // `unran` reloads for the same reason `movedOn` does: an approve that never ran left an
        // "Approved the plan." bubble the server has no record of, and the plan is still waiting.
        if (movedOn() || unran) await store.loadBuild({ keepPreview: true });
        await Promise.all([probePreview(), refreshBindings(), loadThreadList()]);
        notify();
      }
    },

    // The one path that stops a plan, whichever word the card puts on the button — Cancel on a
    // plan the Build gate wrote, Undo on one a handoff carried in. `planId` names the document so
    // the server can record it against this Conversation, which is what lets an Undo still read as
    // undone tomorrow (#60). Safe to press twice: with no live plan the server archives nothing
    // and records nothing.
    async cancelBuildPlan(planId) {
      const conversation = (state.thread && state.thread.id) || '';
      await SW.api.cancelPlan({ conversation, planId: planId || '' });
      for (const msg of state.buildMessages) {
        (msg.blocks || []).forEach((b) => {
          if (b.type !== 'build_plan') return;
          b.pending = false;
          // Locally too, so the sentence about what Undo did NOT take is on screen before the
          // reload that would fetch it back.
          if (b.crossed && (!planId || b.planId === planId)) b.cancelled = true;
        });
      }
      await refreshProjectPlan();
      notify();
    },

    // Change on the plan card: the crossing again, with different answers (#60). It rewrites what
    // crossed and nothing else — no second plan card, and no chance to re-target, because the app
    // was chosen once on the sheet and a Project holds many (ADR-0008).
    // `planId` is the card's own document, so a Conversation that handed off more than once
    // changes the crossing the person is actually looking at rather than its newest one.
    async recrossHandoff(include, planId) {
      const id = state.thread && state.thread.id;
      // Thrown rather than returned as null: the caller closes its sheet on the way back, and a
      // quiet null would tell the person the crossing was redone when nothing was sent.
      if (!id) throw new Error('No conversation is open.');
      const result = await SW.api.recrossHandoff(id, include, planId);
      for (const msg of state.buildMessages) {
        (msg.blocks || []).forEach((b) => {
          // Matched on the document, the way the reader matches the row this call appended: a
          // Conversation that handed off to the same app twice has two cards, and only the
          // newest handoff is the one being changed.
          if (b.type !== 'build_plan' || !b.crossed) return;
          if (result.planId && b.planId !== result.planId) return;
          b.crossed = { ...b.crossed, ...(result.crossed || {}) };
        });
      }
      // The crossing selected the app it wrote into, exactly as a confirm does — and under the
      // merged transcript a card can be read while a different app is selected. Without this the
      // rail would go on highlighting the app the person left, and the next build would land
      // somewhere they were not looking.
      // Unconditional, not the id-guarded cascade: a recross rewrites this app's records whether
      // or not it moved the selection, so "the app did not change" is not "nothing changed".
      await loadAppList({ cascade: false });
      // Passed the app the read above just settled on, not left to default: `refreshAppScope`
      // assigns whatever it is given, so calling it bare puts the selection down — on the one path
      // whose whole point is that the crossing's app stays selected.
      await refreshAppScope(state.activeApp);
      notify();
      return result;
    },

    // The selected app's builds, asked for (#88). Two lines, and the second one is the point: the
    // list is cleared on the way in, so opening always reads. Keeping the last look would mean a
    // build that finished since is missing from a list whose whole job is to hold every build.
    //
    // Through the gate rather than by hand, because that is what claims a place in the queue: a
    // read still out for this app from a previous look must not land on top of the fresh one.
    openBuildHistory() {
      applyAppScope(appScopeTicket(), { appHistory: null });
      state.buildHistoryOpen = true;
      notify();
    },

    closeBuildHistory() {
      state.buildHistoryOpen = false;
      notify();
    },

    // The one reader, called by the drawer when it is open and holds nothing for the app on
    // screen. Two moments answer that description and they are the whole of the behaviour: opening
    // it, and the selection moving underneath it while it is open — which needs nobody to click,
    // since a second tab choosing another app moves it here too (#95).
    readAppHistory: loadAppHistory,

    // Stop ends ONE turn, and whatever was queued behind it starts (#79). So the flags are not
    // cleared here any more: `loadBuild` re-reads the server's lock, which is the only thing that
    // knows whether stopping this turn left the project idle or handed it straight to the next
    // question. Clearing them first showed an idle header over a build that had just begun.
    runningTurnHere,
    runningTurnElsewhere,

    async stopBuild() {
      state.buildTyping = 'Stopping…';
      notify();
      await SW.api.stopBuild({ kind: 'build',
                               conversation: (state.thread && state.thread.id) || '' });
      await store.loadBuild({ keepPreview: true });
    },

    // Cancel is not Stop. Stop interrupts the turn that is RUNNING; this drops one that is still
    // waiting in line and leaves the running one alone — "I have changed my mind about asking" and
    // "I have seen enough of this answer" are different sentences, and a single control for both
    // would make one of them throw away the other's work (ADR-0013).
    //
    // The pending turn's own stream is what actually ends: the server wakes it, it yields a
    // cancelled `done`, and the send that has been awaiting it all along clears its own row.
    async cancelQueuedTurn(ticket) {
      await SW.api.cancelTurn(ticket);
    },

    async loadBuild(options = {}) {
      // One ticket for the whole load, not one per read: the attachments, the Bindings and the
      // selected app are three parts of one answer taken at one moment, and a newer answer has to
      // beat all three of them or none (#101).
      const ticket = appScopeTicket();
      const project = await SW.api.project().catch(() => ({}));
      applyModelStatus(project);
      // Off the read that was already happening. The header's row renders per app switch, so it
      // has to answer out of the store rather than fetch (ADR-0010) — and `loadBuild` is what
      // `selectApp` already runs, so the switch reloads it with everything else app-scoped.
      applyAppScope(ticket, { appAttachments: project.attached || [] });
      // No conversation open means a new one: nothing to replay. Asking for the whole project
      // here is what used to make "New conversation" look dead — the transcript never changed.
      const conversation = state.thread && state.thread.id;
      const [hist, running] = await Promise.all([
        conversation
          ? readBuildTranscript(conversation)
          : Promise.resolve({ merged: null, history: [] }),
        SW.api.buildState().catch(() => ({ running: false })),
        refreshBindings(ticket),
        refreshProjectPlan(),
        // The cascade would be this function's own work done twice: `attached` is off the read
        // above and `/bindings` is in this very list.
        loadAppList({ cascade: false, ticket }),
      ]);
      await applyBuildRead(hist);
      state.buildRunning = applyTurnState(running);
      state.buildTyping = state.buildRunning ? 'Working…' : null;
      if (!options.keepPreview) state.previewStatus = 'starting';
      notify();
      await probePreview();
      if (state.buildRunning) store._watchBuild();
    },

    async refreshPreview() {
      state.previewStatus = 'starting';
      notify();
      await probePreview();
    },

    // Nothing answered on the preview port for as long as Build was prepared to wait, so it stops
    // checking (#90). Giving up is right — 1.5s polling forever costs something and buys nothing
    // after the first minute — but giving up while the screen still says `starting` left a person
    // waiting on a message that had stopped meaning anything, with nothing checking behind it.
    //
    // A state of its own rather than `err`. `err` is the preview answering with something bad,
    // this is it never answering at all, and the two have different causes: a first build
    // installing dependencies is slow, a broken one is broken. `refreshPreview` is the way back,
    // and it starts the wait over.
    previewGaveUp() {
      if (state.previewStatus !== 'starting') return;
      state.previewStatus = 'stalled';
      notify();
    },

    _watchBuild() {
      if (store._watchTimer) return;
      // Ticks overlap: each awaits three calls and they are scheduled every 2s regardless. On a
      // large transcript a tick's `history` can land after a later tick's, and installing it
      // rolls the Build transcript back to an older snapshot until the next poll — losing the
      // newest tool cards, and the `live: true` flag the reset-offer buttons render from.
      let polled = 0;
      let settled = 0;
      const tick = async () => {
        const mine = ++polled;
        const running = await SW.api.buildState().catch(() => ({ running: true }));
        // The rail rides along: which app a build is running in is a row's state, and someone who
        // switched away from that app has no other way to see the turn still going (#77).
        await loadAppList();
        // Same scope as loadBuild: polling the whole project here would pull other
        // conversations' turns into the one on screen.
        const watched = state.thread && state.thread.id;
        // The same read the load makes, so a tick mid-build refreshes this app's turns without
        // dropping the Chat turns above them. Under the split view this is the read it always was.
        const hist = watched ? await readBuildTranscript(watched) : { merged: null, history: [] };
        // An answer that arrived out of order is stale by definition. Drop it; the next tick is
        // 2s away and carries everything this one would have.
        if (mine < settled) return;
        settled = mine;
        await applyBuildRead(hist);
        state.buildRunning = applyTurnState(running);
        if (!state.buildRunning) {
          state.buildTyping = null;
          clearInterval(store._watchTimer);
          store._watchTimer = null;
          await Promise.all([probePreview(), refreshBindings()]);
        }
        notify();
      };
      store._watchTimer = setInterval(tick, 2000);
    },

    async sendMessage(text) {
      if (!text.trim()) return;
      // A second question used to be dropped here, because the server would only have refused it
      // and said so in the transcript — which read as Sage answering a question about data with a
      // complaint about a build. The server queues it now (#79), so the composer takes it: the
      // second question was never the thing worth refusing, the dead composer was.
      let thread = state.thread;
      if (!thread) thread = await store.newThread();
      // The conversation this turn belongs to. Everything below writes to the view only while it
      // is still the one on screen: a turn keeps running when you open another conversation or
      // start a new one, and its answer used to land in whichever Thread you had moved to.
      const turnThread = thread.id;
      // A latch, not a live test. Coming back to a conversation you left mid-turn re-reads the
      // transcript from the server, so a stream that resumed writing here would be appending to a
      // list that already contains what it wrote. Once it lets go, it stays let go, and the
      // `finally` re-reads the Thread for whoever is looking at it by then.
      let left = false;
      const mine = () => {
        if (left) return false;
        if (state.thread && state.thread.id === turnThread) return true;
        left = true;
        return false;
      };

      const attachments = state.attachments.map((a) => ({
        resourceId: a.resourceId,
        name: a.resourceName,
        kind: a.resourceKind,
        addedBy: a.addedBy,
      }));
      pushMessage({
        id: `u_${Date.now()}`,
        role: 'user',
        at: new Date().toISOString(),
        blocks: [{ type: 'text', value: text }],
        attachments,
      });
      // The queue's two ends of this send (#79): `ticket` is what a Cancel would name, and `unran`
      // is whether it ended without ever running — the one case where the bubble above has to come
      // back off the screen, because the server recorded nothing to replace it with.
      let ticket = '';
      let unran = false;
      liveChatTurns += 1;
      state.typing = 'Thinking…';
      state.chatRunning = true;
      state.chatTurnThread = turnThread;
      notify();

      const assistant = {
        id: `a_${Date.now()}`,
        role: 'assistant',
        at: new Date().toISOString(),
        blocks: [],
      };
      const ensurePushed = () => {
        if (!state.messages.some((m) => m.id === assistant.id)) pushMessage(assistant);
      };

      // The answer as it is being written. `liveIndex` is where that block sits in this message,
      // or -1 when no block is open — a turn can write more than one, and each `final` closes the
      // one it completes so the next fragment starts a fresh one.
      //
      // Deltas arrive faster than the screen refreshes, and every one of them re-renders the whole
      // Thread — including earlier messages' charts, which stringify their options to decide
      // whether to redraw. So fragments accumulate and repaint once a frame. The text is identical
      // either way; the difference is a Thread that scrolls smoothly and one that stutters.
      let liveIndex = -1;
      let streamed = '';
      let painting = false;
      const flush = () => {
        painting = false;
        if (liveIndex < 0) return;
        const blocks = [...assistant.blocks];
        blocks[liveIndex] = { type: 'text', value: streamed, streaming: true };
        assistant.blocks = blocks;
        notify();
      };
      const paint = () => {
        if (painting) return;
        painting = true;
        requestAnimationFrame(flush);
      };

      try {
        const res = await fetch(`./api/threads/${thread.id}/chat/stream`, {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ prompt: text }),
        });
        if (!res.ok) {
          const payload = await res.json().catch(() => ({}));
          throw new Error(payload.error || payload.message || res.statusText);
        }
        await readSSE(res, async (ev) => {
          if (!ev || ev.type === 'user') return;
          // The queue's rows belong to this send wherever the reader has moved to, so they are read
          // before the `mine()` check rather than after it: a Cancel has to be able to find its
          // ticket, and a question handed back has to reach a composer.
          if (ev.type === 'pending') { ticket = ev.ticket; queueTurn(ev, 'chat'); notify(); return; }
          if (ev.contextChanged) {
            unran = true;
            store.seedComposer(ev.prompt || text);
            antd.message.warning(ev.message);
            return;
          }
          if (ev.type === 'done' && ev.decision === 'cancelled') { unran = true; return; }
          // Moved on. The turn is still running and the server is still writing its transcript, so
          // nothing is lost — reopening the conversation replays it. What is not wanted is this
          // answer appearing under a different question.
          if (!mine()) return;
          if (ev.type === 'delta') {
            state.typing = null;
            ensurePushed();
            if (liveIndex < 0) {
              liveIndex = assistant.blocks.length;
              assistant.blocks = [...assistant.blocks, { type: 'text', value: '', streaming: true }];
              streamed = '';
            }
            if (ev.final) {
              // The whole text rather than the last fragment. The stream cannot be replayed, so
              // this is what repairs a live copy that dropped a frame — and it closes the block.
              streamed = ev.text || '';
              flush();
              liveIndex = -1;
            } else {
              streamed += ev.text || '';
              paint();
            }
          } else if (ev.type === 'agent' && ev.kind === 'text' && ev.text) {
            state.typing = null;
            ensurePushed();
            // What streamed was the turn happening. This is the record of it, and it is the only
            // part the server keeps, so the live blocks go: a Thread has to look the same on
            // reload as it did while it ran. Intermediate "let me read that file" text is not in
            // the transcript and so is not in the Thread either way. A queued repaint is harmless
            // once liveIndex is -1.
            liveIndex = -1;
            assistant.blocks = [...assistant.blocks.filter((b) => !b.streaming),
                                { type: 'text', value: ev.text }];
            notify();
          } else if (ev.type === 'agent' && ev.kind === 'tool') {
            state.typing = SW.util.activityLabel(ev);
            notify();
          } else if (ev.type === 'artifacts' || (ev.type === 'done' && ev.artifacts && ev.artifacts.length)) {
            state.typing = null;
            ensurePushed();
            const items = ev.items || ev.artifacts;
            const have = new Set(
              assistant.blocks.filter((b) => b.type === 'image' || b.type === 'table' || b.type === 'file')
                .map((b) => b.src || b.path || b.title)
            );
            const fresh = (items || []).filter((a) => !have.has(fileUrl(a.path)) && !have.has(a.path) && !have.has(a.title));
            if (fresh.length) {
              assistant.blocks = [...assistant.blocks, ...(await blocksForArtifacts(fresh))];
            }
            if (state.thread && items && items.length) {
              const have = new Set((state.thread.artifacts || []).map((a) => a.path));
              const extra = items.filter((a) => a.path && !have.has(a.path));
              if (extra.length) {
                state.thread = {
                  ...state.thread,
                  artifacts: [...(state.thread.artifacts || []), ...extra],
                };
              }
            }
            notify();
            refreshAttachments();
          } else if (ev.type === 'error' || ev.type === 'stopped') {
            state.typing = null;
            ensurePushed();
            // A status line, not a text block: this is Sage reporting on the turn, and it should
            // not read like the answer to the question. Same shape the reload path builds. Whatever
            // had streamed stays above it — a stopped turn's half-answer is still worth reading.
            assistant.blocks = [...assistant.blocks, {
              type: 'status',
              ok: false,
              value: ev.message || (ev.type === 'stopped' ? 'Stopped.' : 'The turn failed.'),
            }];
            notify();
          } else if (ev.type === 'done') {
            state.typing = null;
            notify();
          } else if (ev.type === 'handoff-suggest') {
            state.typing = null;
            pushMessage({
              id: `sug_${Date.now()}`,
              role: 'system',
              at: new Date().toISOString(),
              blocks: [{ type: 'plan_suggestion', reason: ev.reason }],
            });
          }
        });
      } catch (err) {
        if (mine()) {
          state.typing = null;
          ensurePushed();
          assistant.blocks = [...assistant.blocks, { type: 'text', value: String(err.message || err) }];
        }
        notify();
      } finally {
        liveChatTurns -= 1;
        dropQueuedTurn(ticket);
        // The turn is over wherever the reader is now. Not `false` outright: this tab can have
        // several turns alive at once now, and the flag says "this project is busy", not "this
        // conversation is busy" — so it stays true while any of them is still here.
        state.chatRunning = liveChatTurns > 0;
        if (!state.chatRunning) state.chatTurnThread = null;
        if (mine()) state.typing = null;
        notify();
      }
      // Back on the conversation this turn ran in, but the stream stopped writing to the view when
      // it was left. Re-read it, so the answer is there rather than in a Thread nobody reloaded.
      // A turn that never ran re-reads for the opposite reason: the bubble it drew optimistically
      // has to go, because the server recorded nothing to replace it with and the text is back in
      // the composer instead.
      if ((left || unran) && state.thread && state.thread.id === turnThread) {
        await store.openThread(turnThread).catch(() => {});
      }
      await loadThreadList();
      await refreshAttachments();
    },

    // Stop is the answer to a turn that will not end. Chat already caps a turn at ten minutes, and
    // the comment that chose that number said it was generous "because by then the person can
    // press Stop" — which was true of Build and of nothing in Chat.
    //
    // Same endpoint Build uses, and it used to be the same endpoint because there was only ever one
    // thing to interrupt. That is no longer why: the endpoint stops the turn HOLDING the lock,
    // whichever mode started it, and the questions queued behind it are not interrupted — they run
    // (#79). Dropping one of those is Cancel's job, which is a different control.
    async stopChat() {
      state.typing = 'Stopping…';
      notify();
      try {
        await SW.api.stopBuild({ kind: 'chat',
                                 conversation: (state.thread && state.thread.id) || '' });
      } catch (err) {
        antd.message.error(String((err && err.message) || err));
        state.typing = null;
        notify();
        return;
      }
      // A turn this tab is streaming reports its own stop and clears the flag as it unwinds. The
      // watcher is for the turn it is not — after a reload, or one another tab started — and for
      // the stop the stream never hears, so the turn bar never stays up over a freed lock.
      store._watchTurn();
    },

    // Whether the project is mid-turn, straight from the server's turn lock. The one place the
    // answer is authoritative — a tab that reloaded mid-turn has no stream and no memory of it, and
    // used to offer a composer that the server would then refuse. It queues now, so what this
    // decides is the turn bar and Stop rather than whether anything can be typed; `wedged` rides
    // along, because a wedged workspace is the one that still refuses (#79).
    async refreshTurnState() {
      const turn = await SW.api.buildState().catch(() => ({ running: false }));
      const was = state.chatRunning;
      const running = applyTurnState(turn);
      state.chatRunning = running;
      if (running) {
        notify();
        store._watchTurn();
        return;
      }
      // The lock is free, so nothing is running whatever this tab still believes.
      state.chatTurnThread = null;
      state.typing = null;
      notify();
      // It finished while nothing here was listening, so the transcript on screen is behind.
      if (was && state.thread) await store.openThread(state.thread.id).catch(() => {});
    },

    // Poll the lock so the turn bar goes away by itself. The lock is the only thing that knows:
    // a stream can still be reading an SSE the server has finished with, and a Stop can land on a
    // turn that never says a word back — both look like "still running" from in here, and both end
    // with a free lock. Errs towards running, so a poll that fails never claims a project is idle.
    _watchTurn() {
      if (store._turnWatchTimer) return;
      store._turnWatchTimer = setInterval(async () => {
        const turn = await SW.api.buildState().catch(() => ({ running: true }));
        if (applyTurnState(turn)) return;
        clearInterval(store._turnWatchTimer);
        store._turnWatchTimer = null;
        await store.refreshTurnState();
      }, 2000);
    },

    async chooseOption(option) {
      if (option.opensPanel) store.focusPanel(option.opensPanel);
      pushMessage({
        id: `uc_${Date.now()}`,
        role: 'user',
        at: new Date().toISOString(),
        blocks: [{ type: 'text', value: option.label }],
      });
      const reply = await SW.api.advance(state.thread.id, option.next);
      state.scriptMeta = { planTemplate: reply.planTemplate };
      await deliverTurn(reply.message, { suggestPlan: reply.suggestPlan });
    },

    dismissNudge() {
      state.nudgeDismissed = true;
      state.messages = state.messages.filter(
        (m) => !m.blocks.some((b) => b.type === 'graduation_nudge')
      );
      notify();
    },

    dismissPlanSuggestion() {
      state.messages = state.messages.filter(
        (m) => !m.blocks.some((b) => b.type === 'plan_suggestion')
      );
      if (state.thread) {
        SW.api.patchThread(state.thread.id, { handoff: 'suppress' }).catch(() => {});
        state.thread = { ...state.thread, handoff: { ...(state.thread.handoff || {}), suppressed: true, status: 'suppressed' } };
      }
      notify();
    },

    async draftHandoffPlan(threadId) {
      const id = threadId || (state.thread && state.thread.id);
      if (!id) return null;
      if (!state.thread || state.thread.id !== id) await store.openThread(id);
      state.typing = 'Writing a plan…';
      notify();
      try {
        const draft = await SW.api.draftHandoffPlan(id);
        state.thread = { ...state.thread, handoff: draft.handoff };
        state.messages = state.messages.filter(
          (m) => !(m.blocks || []).some((b) => b.type === 'plan_suggestion')
        );
        state.handoffDraft = draft;
        state.handoffOpen = true;
        notify();
        return draft;
      } catch (err) {
        antd.message.error(String((err && err.message) || err));
        return null;
      } finally {
        state.typing = null;
        notify();
      }
    },

    // `target` names the Built App the sheet picked, or is empty for a new one. Empty is passed
    // through rather than resolved here: the default is the server's, so the app a confirm lands
    // in cannot be changed by a change to the sheet's markup (#73).
    async confirmHandoff(include, target) {
      const id = state.thread && state.thread.id;
      if (!id) return null;
      const result = await SW.api.confirmHandoff(id, include, target || {});
      state.handoffOpen = false;
      state.handoffDraft = null;
      state.thread = { ...state.thread, handoff: result.handoff };
      // The confirm made or reselected an app, so the rail and `activeApp` are both stale.
      await loadAppList();
      // Build lands on the app this handoff bound — a Project holds many, so "#/build/<thread>"
      // alone would leave which one to chance. Through `appRoute`, which owns that grammar.
      const bound = state.apps.find((a) => a.id === ((result.handoff && result.handoff.appId) || ''));
      SW.router.go(bound ? SW.appRoute(bound) : `#/build/${id}`);
      return result;
    },

    // An upload is both things at once: it adds the file to the project and
    // puts it in context for this conversation.
    async uploadFile(file) {
      let thread = state.thread;
      if (!thread) thread = await store.newThread();
      const name = (file && file.name) || String(file);
      const res = await fetch(`./api/project/upload?name=${encodeURIComponent(name)}`, {
        method: 'POST',
        body: file instanceof Blob ? file : undefined,
      });
      const body = await res.json().catch(() => ({}));
      if (!res.ok) throw new Error(body.error || 'Upload failed');
      await loadScopeData();
      const resource = {
        id: `file:${body.path}`,
        name: name,
        kind: 'file',
        path: body.path,
        source: body.source || 'scratch',
      };
      state.resourceIndex[resource.id] = resource;
      await store.attach(resource.id, 'user', 'Uploaded in this conversation.', { silent: true });
      antd.message.success(`${name} added to this context`);
      return resource;
    },

    async pinLeaf(parent, pin) {
      if (!parent || !parent.id) return null;
      await store.addToProject({ ...parent, pin }, { silent: true });
      return true;
    },

    async unpinLeaf(parent, pin) {
      await SW.api.unpinFromProject(parent.id, pin);
      await loadScopeData();
      return true;
    },

    async addScratchToDataset(resource, datasetId) {
      if (!resource || !resource.path || !datasetId) return null;
      const res = await SW.api.promoteScratch(resource.path, datasetId);
      const oldId = resource.id;
      await loadScopeData();
      const tid = conversationId();
      const old = (state.attachments || []).find(
        (a) => a.resourceId === oldId || a.path === resource.path
      );
      if (old && tid) {
        await SW.api.removeFromConversation(tid, old.id).catch(() => null);
        state.attachments = state.attachments.filter((a) => a.id !== old.id);
        const next = {
          id: `file:${res.path}`,
          name: resource.name,
          kind: 'file',
          path: res.path,
        };
        state.resourceIndex[next.id] = next;
        await store.attach(next.id, 'user', undefined, { silent: true, quiet: true });
      }
      antd.message.success(SW.brand.text('{name} is on the {dataset}', { name: resource.name }));
      return res;
    },

    // Plans --------------------------------------------------------------

    // The plan opens beside the work, not over it. Build swaps its centre
    // pane instead of stacking a panel, so it only tracks the id.
    openPlanViewer(planId) {
      state.planViewerId = planId;
      notify();
    },

    closePlanViewer() {
      state.planViewerId = null;
      notify();
    },

    // Same artifact, different treatment per mode: Chat gets the friendly
    // editor beside the conversation, Build gets it in the IDE.
    openPlanArtifact(planId) {
      const { mode } = SW.router.get();
      if (mode === 'chat' || mode === 'build') {
        state.planViewerId = planId;
        notify();
        return;
      }
      SW.router.go(`#/plan/${planId}`);
    },

    // The plan is a document the app owns, not the thing context hangs off, so
    // loading one no longer reshuffles what the conversation can see.
    async loadPlan(planId) {
      const plan = await SW.api.plan(planId);
      state.activePlan = plan;
      state.activePlanId = plan.id;
      notify();
      return plan;
    },

    async draftPlan() {
      const plan = await SW.api.createPlan({
        threadId: state.thread && state.thread.id,
        projectId: state.scope.id,
        template: state.scriptMeta.planTemplate,
      });
      state.activePlanId = plan.id;
      state.messages = state.messages.filter(
        (m) => !m.blocks.some((b) => b.type === 'plan_suggestion')
      );
      pushMessage({
        id: `plan_${Date.now()}`,
        role: 'assistant',
        at: new Date().toISOString(),
        blocks: [{ type: 'plan_card', planId: plan.id }],
      });
      await store.loadPlan(plan.id);
      // The plan is an artifact in the project the moment it exists, so the
      // panel has to hear about it.
      await Promise.all([loadThreadList(), loadScopeData()]);
      antd.message.success('Plan drafted — it is in the panel under Artifacts');
      return plan;
    },

    async refreshPlan() {
      if (state.activePlanId) await store.loadPlan(state.activePlanId);
    },

    async saveToProject(body) {
      const result = await SW.api.saveToProject(state.thread.id, body);
      await store.reloadProjects();
      const project = state.projects.find((p) => p.id === result.projectId);
      state.nudgeDismissed = true;
      state.messages = state.messages.filter(
        (m) => !m.blocks.some((b) => b.type === 'graduation_nudge')
      );
      if (project) {
        state.scope = project;
        state.scopeFlash = true;
        notify();
        setTimeout(() => { state.scopeFlash = false; notify(); }, 500);
        await Promise.all([loadScopeData(), loadThreadList()]);
      }
      state.thread = await SW.api.thread(state.thread.id);
      notify();
      return result;
    },

    // Shared refresh helpers ---------------------------------------------

    reloadScopeData: loadScopeData,
    reloadThreads: loadThreadList,
    reloadAttachments: refreshAttachments,

    // The panel's pin names the plan document, so renaming one on the plan page has to reach the
    // pin without a reload. `refreshProjectPlan` does not notify on its own — its other callers
    // fold it into a bigger read that does — so this one says so itself.
    reloadProjectPlan: () => refreshProjectPlan().then(notify, () => {}),

    async reloadNotifications() {
      state.notifications = await SW.api.notifications();
      notify();
    },
  };

  SW.store = store;

  // The Build log cut into runs — a user row and the agent rows that followed it — for anything
  // that LISTS builds rather than replaying them (#88). The same grouping Chat's merged view folds
  // with, exported rather than copied: one answer to "where does a build start and stop", and two
  // surfaces that draw it differently.
  SW.buildRuns = buildRunMessages;

  // The Workbench half of orchestrator/brand.py's `text()`. Substitution is author-time
  // (ADR-0014): a user-visible string is a template resolved when it is read, so a new string is
  // branded because whoever wrote it wrote it that way. Never a filter over what the server sent —
  // by then provenance is gone, and a filter cannot tell our word for the platform from a Resource
  // a user named after the company.
  //
  // What an absent key falls back to is BRAND_DEFAULT, at the top of this file — the pack's
  // documented defaults, held once. The shell paints before /api/brand answers, so those are what
  // a person reads until it does.
  const BRAND_TOKEN = /\{([A-Za-z][A-Za-z0-9]*)\}/g;

  function brandTokens() {
    const pack = store.get().brand || {};
    const table = {};
    for (const source of [BRAND_DEFAULT, pack]) {
      for (const [key, value] of Object.entries(source)) {
        if (typeof value === 'string' && value) table[key] = value;
      }
      const nouns = (source.nouns && typeof source.nouns === 'object') ? source.nouns : {};
      for (const [key, forms] of Object.entries(nouns)) {
        if (!forms || typeof forms !== 'object') continue;
        if (typeof forms.singular === 'string' && forms.singular) table[key] = forms.singular;
        if (typeof forms.plural === 'string' && forms.plural) table[key + 'Plural'] = forms.plural;
      }
    }
    return table;
  }

  SW.brand = {
    assistant() {
      return brandTokens().assistantName;
    },
    product() {
      return brandTokens().productName;
    },
    platform() {
      return brandTokens().platformName;
    },
    // `values` fill the rest of the sentence, so the whole sentence stays one literal that the lint
    // over marked positions can read. A substituted value is NOT scanned again, which is what lets
    // a Resource a user named after the company survive being interpolated into one of ours.
    //
    // An unknown token is left as it was written rather than throwing: a typo in a string must
    // never stop the Workbench booting, and a passed-through platform error carries braces of
    // its own.
    text(template, values) {
      if (!template || template.indexOf('{') < 0) return template;
      const table = brandTokens();
      if (values) {
        for (const [key, value] of Object.entries(values)) table[key] = String(value);
      }
      return String(template).replace(BRAND_TOKEN, (raw, key) =>
        Object.prototype.hasOwnProperty.call(table, key) ? table[key] : raw
      );
    },
  };
})();
