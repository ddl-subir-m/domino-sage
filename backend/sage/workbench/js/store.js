window.SW = window.SW || {};

(function () {
  // Until /project answers, the chip has nothing real to name. Not a sandbox: scratch is not
  // offered as a Project any more (ADR-0004) — every Thread lives in a git-backed sage-* Project.
  const NO_SCOPE = {
    id: '',
    name: 'Default',
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
      // ADR-0026's seven, in step with `brand.DEFAULT` on the server. Left out of here, a
      // `{project}` in the shell's own chrome paints as the literal `{project}` for as long as
      // /api/brand takes to answer, which is the window this table exists to cover.
      llmGateway: { singular: 'LLM Gateway', plural: 'LLM Gateways' },
      hostedGenaiEndpoint: {
        singular: 'Hosted GenAI Endpoint', plural: 'Hosted GenAI Endpoints',
      },
      project: { singular: 'Project', plural: 'Projects' },
      resource: { singular: 'Resource', plural: 'Resources' },
      // The prose word is Table (ADR-0037); the key stays `scope`, because ADR-0014 renames what a
      // person reads and never the identifier behind it. In step with `brand.DEFAULT`.
      scope: { singular: 'Table', plural: 'Tables' },
      chat: { singular: 'Chat', plural: 'Chats' },
      turn: { singular: 'Turn', plural: 'Turns' },
    },
    // In step with `brand.DEFAULT_THEME`, and here for the reason the nouns above are: it is read
    // before /api/brand answers, and the shell paints in whatever it says until then.
    theme: 'domino',
    colors: {
      primary: '#543FDE',
      primaryDark: '#311EAE',
      primaryLight: '#EEEBFC',
    },
  };

  const state = {
    ready: false,
    // `'waiting'` while the boot is sitting out a proxy that is not serving yet, so the boot screen
    // can say what it is doing instead of spinning. Same shape and the same job as `previewStatus:
    // 'starting'`, which already covers Vite's own warm-up.
    bootStatus: null,
    me: null,
    brand: BRAND_DEFAULT,
    projects: [],
    // Whether this container can create or attach Projects at all (Domino + a git host).
    // False on a laptop run, where New project has nothing to create against.
    canProvision: false,
    scope: NO_SCOPE,
    scopeFlash: false,

    // Two doors out of the Workbench, both built by the server from this deployment's own hosts so
    // neither names an environment. `manageUrl` is the Manage App the platform bar opens;
    // `costUrl` is the gateway's Usage & Cost dashboard, deep-linked to this project's spend.
    // Null means this deployment has neither, and a null one draws no link rather than a dead one.
    manageUrl: null,
    costUrl: null,

    // Dock and Rail. Both start closed, and both are seeded from the viewer's preferences in
    // `init` rather than here (#150): prefs.js loads after this file and keys its record by
    // `state.me`, so there is nothing to read until the store knows who is looking.
    dockTab: null,        // null = collapsed
    // null = never dragged; the stylesheet sizes the open dock. An integer is a
    // choice made on the resize handle, seeded from prefs in `init` with the rest.
    dockWidth: null,
    panelFilter: null,    // resource kind the assistant asked the user to pick
    railHidden: true,
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
    peopleOpen: false,
    paletteOpen: false,
    scopePickerOpen: false,
    // The Build header's own add door, open. Controlled from here rather than left to antd because
    // something else in the Workbench asks for it — the refusal card's credential repair, which
    // points at this door and no longer at the panel (#143, ADR-0021).
    addToAppOpen: false,
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
    // The last listing read off the platform, whole and unfiltered — what `/api/resources` and
    // `/api/assets` answered together. Browse Domino is drawn from this rather than reading the
    // platform again per keystroke (#159). `null` means Sage has not looked yet, which nothing may
    // draw as "there is nothing to add".
    resourceListing: null,
    // Which project the listing above was read under. The listing itself describes the platform
    // rather than the project, but the membership it is read against does not, so a scope change
    // is the one thing that has to drop it. Held here rather than dropped on every load, because
    // a reload of the SAME scope would then blank an open catalogue for the length of a round
    // trip.
    resourceListingScope: null,
    gatewayAliases: [],
    resourcesLoading: true,
    members: [],
    directory: [],
    // The three states `/api/members` keeps apart, held apart here too. `connected` false is "not
    // running against the platform"; `error` set is "the read failed"; neither set with no members
    // is a Project genuinely worked on alone. Collapsing any two of them would tell a creator their
    // colleagues do not exist when what is true is that Sage could not look.
    membersConnected: false,
    membersError: '',
    membersLoading: true,
    // The two rows the People modal must not offer Remove on, in Domino's id space. `state.me`
    // cannot stand in for `selfId`: it is read off the viewer JWT, whose subject is the identity
    // provider's id and does not join against a collaborator row.
    ownerId: '',
    selfId: '',
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
    // plan.md, for the app the preview is showing. Not the plan document above. The panel reads
    // its `planId` to mark which row in `plans` the selected app is being built from right now.
    projectPlan: null,
    // Every plan document in the Project, newest first — `GET /api/plans`, which had no caller
    // until the panel grew a Plans group. The documents are the Project's and each one names the
    // app it belongs to (`appId`), which is what lets a project-scoped list hold an app-scoped
    // artifact honestly: the row says whose it is.
    plans: [],

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
    // Which slot pinned the whole session, or '' (ADR-0032). Server-computed: the picker
    // restates the router's precedence below, and the signing pin is the one rule it cannot see.
    signingSlot: '',
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

    // The sensitivity lock (ADR-0043): `{ enabled, locked, group, approved, datasets, refusal, model }`.
    // Null until the first read lands, and that is not the same as `enabled: false` — a picker that
    // greyed rows out on a read it never got would refuse models on a lock that may not exist. Every
    // reader below therefore asks `locked` and treats null as "no lock", which is what the
    // overwhelming majority of deployments are.
    //
    // Read on a scope load and after a Binding changes, and nowhere else. A declaration is a live
    // fact about the Datasets bound RIGHT NOW, and those are the only two moments that set moves.
    sensitivity: null,
    // Which switches the notice has already been read for — the keys the composer builds, one per
    // sentence it can say. Kept here rather than in prefs because "once" means once per switch and
    // not once per person: an administrator moving the group tomorrow moves the session onto a
    // different model, and that is a new thing to say.
    //
    // A LIST and not one slot, because the sentence is per surface as well as per switch: Build and
    // Chat are pinned to different sovereign slots, so toggling between them alternates between two
    // keys, and a single slot would have each toggle re-raise the notice the other one dismissed.
    // Bounded by the switches one session can be told about, which is a handful.
    sensitivityNoticeFor: [],

    // Every standing [[Problem]] GET /api/health reported, as it came (ADR-0027): `{ id, message,
    // fix, owner, body }`, the server's own sentences, rendered and never rewritten here. Empty is
    // the normal state and the chip draws nothing on it, which is the whole reason a standing fault
    // can have a permanent home in a crowded bar.
    //
    // Empty is also what a read that never landed leaves behind, and that is deliberate: the route
    // answers 200 even when all five of its own reads failed, so a rejection here is the route
    // itself being gone. Reporting THAT as a Problem would be a client composing a sentence.
    problems: [],
    problemsOpen: false,

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
    // Whether the app dependencies modal is on screen. Same reasoning as `buildHistoryOpen`: not in
    // `APP_SCOPED`, because switching app changes what the modal lists, never whether somebody
    // opened it.
    appDependenciesOpen: false,
    // The Data Source Binding whose Scope is being chosen, and the ladder it is standing on (#142).
    // `{ id, name, levels, database, schema, items, error }` — `items` is the names at the current
    // level, `null` while the read is out. Null when no Scope door is open.
    //
    // In the store rather than in the control, unlike the Resource Browser's cascade, and for the
    // reason everything else async here is: this walk POSTS at the end of it. The cascade only ever
    // reads, so its position can live and die with the element drawing it; a Scope outlives the
    // menu that chose it, and the act that writes one belongs beside the act that binds.
    //
    // Not in `APP_SCOPED`: it is a control somebody opened, not a record read for an app. The door
    // is drawn per Binding, so a selection that moves takes the Binding — and the door — with it.
    scopePick: null,
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
    if ('signing_slot' in m) state.signingSlot = m.signing_slot || '';
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
  // Which platform listing read is the current one (#159). Two are routinely in the air — the one
  // a scope load defers and the one Browse Domino fires when it opens — and the older answering
  // last would put a row somebody just deleted back in the rail. A generation counter for the same
  // reason the two below have one: the newest read is the one that gets to write.
  let listingRead = 0;
  // The scope load whose listing has already spent its one retry, so a leg that goes on refusing
  // costs one extra read and not a poll.
  let listingRetryFor = -1;
  const LISTING_RETRY_MS = 4000;
  // Which read of what the PROJECT holds is the current one — its membership and its `/project`
  // record together, which is the pair every refresh below takes. Same shape as the listing counter
  // above and needed for the same reason since #162: a working-set refresh no longer bumps
  // `scopeLoad`, so a scope load is no longer cancelled by a mutation landing under it and the two
  // can now write the same fields in either order.
  //
  // CLAIMED AT ENTRY by both writers, which is the whole of what makes it order them. The scope
  // load issues its `/project` read in a second phase, behind the membership read — ticketing it
  // there would put it AFTER a refresh that started later, and the scope load would then throw
  // away the very Add that fired the refresh. So the ticket is taken where the function starts,
  // and both of its phases stand or fall on it: applying half of one read and half of another is
  // how the Uploads group ends up empty with nothing on the way to refill it.
  let projectRead = 0;
  // Which open is the current one. `selectApp` guards with `selecting`, which makes a second
  // asker bail — right there, because you are already going where it asked. Wrong here: clicking
  // A after B must land on A, not be ignored. So this is a generation counter like `scopeLoad`,
  // and a superseded open drops its answer instead of writing it.
  let openSeq = 0;
  // Which listing the open Scope door is waiting on (#142). A generation counter for the reason the
  // two above have one: stepping into B while A's listing is still out has to land on B, however
  // the two resolve. Everything that SHUTS the door bumps it too — closing it by hand, and the
  // selection moving — so a listing that arrives for a door nobody is looking at is dropped rather
  // than reopening one.
  let scopePickLoad = 0;

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
        // The Scope door goes with it (#142). It is keyed on the BINDING, and two Built Apps in one
        // Project can bind the same Data Source — so a walk left half-finished under the app you
        // came from would still match here, and `saveScope` posts to a route carrying no app id:
        // the server would scope whichever app is selected NOW. Cleared here for the reason the
        // notice above is, which is that this is the one place that sees the selection move.
        scopePickLoad += 1;
        state.scopePick = null;
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
    // The shell's half of a theme is one attribute: `[data-theme]` in tokens.css answers it, and
    // everything a theme changes that the pack does not carry — the top bar's own colours, the two
    // type faces, what a heading weighs, whether a chip is a pill — is written there.
    //
    // Ant Design's half cannot be reached from a stylesheet and is `SW.themeFromBrand` instead.
    if (brand.theme) document.documentElement.setAttribute('data-theme', brand.theme);
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

  // The kinds whose listing is complete by construction, so absence from one that answered means
  // Domino no longer holds the row. `model_predictive` is deliberately not here: `list_model_apis`
  // fans out over the creator's member projects, SKIPS any non-home project that fails and caps the
  // fan-out at twenty-five — so a Model API can be permanently absent from a listing that reports
  // complete success. Absence there is not evidence. The cost is that a deleted Model API goes on
  // looking live, and we took it knowingly: a false death is a new harm where a false life is an
  // old one (ADR-0034). #163 is the provider-side repair that would upgrade the kind.
  const CHECKABLE_KINDS = ['dataset', 'datasource', 'model_llm'];

  // The one group holding rows a level below the listing: a warehouse table and a pinned Dataset
  // path, both of which `groupsFromMembership` puts in `pin` whatever their kind. They take their
  // parent's word.
  const CHILD_GROUPS = ['pin'];

  // Whether Domino still holds what each working-set row names. Members MINUS the listing — the
  // other subtraction `catalogueParents` is one half of, over the same two collections, which is
  // why it costs nothing here and why it is written here and nowhere else.
  //
  // Computed on read and never written to the membership file, for the reason `usedBy` is not: a
  // stored copy is wrong the moment anybody deletes anything.
  //
  // Three values, because two would state a fact Sage does not have. `unchecked` is not a residue:
  // `state.resourceListing` is null until the deferred read lands, a kind that refused has its
  // previous rows carried forward by `keepUnreadKinds` — present, stale and wrong at once — and one
  // kind can never be checked at all.
  function stampLiveness(groups, listing) {
    const next = { ...groups };
    const byParent = {};
    SW.util.MEMBERSHIP_PARENT_KINDS.forEach((kind) => {
      // The error is asked BEFORE the rows, because a refused leg arrives here holding the rows it
      // could not re-read. Reading those as the platform's answer would mark nothing dead and call
      // the rest alive off a listing nobody got.
      //
      // And the group has to BE an array. `fetchDominoListing` writes all four keys on every real
      // answer, so a kind with none never came from a read: `refreshResourceListing` writes a
      // synthetic `{ errors: { listing }, groups: {} }` when the read itself faults, and that error
      // is keyed on the whole listing rather than on any kind. Without this the next working-set
      // change would re-apply it, find no per-kind error and an empty group, and mark every Dataset,
      // Data Source and Alias in the project dead at once — the false death ADR-0034 gave up a whole
      // kind to avoid.
      const group = (listing && listing.groups && listing.groups[kind]) || null;
      const checkable = CHECKABLE_KINDS.indexOf(kind) !== -1
        && Array.isArray(group)
        && !(listing.errors || {})[SW.api.LISTING_ERROR_KEY[kind]];
      // An Alias is matched on its id OR its name, because that kind's id space is not stable.
      // `join_aliases` keys a row on the record's id when `/api/aliases` has a record for it and on
      // the BARE NAME when it does not — and a gateway answering 200 with no records raises nothing,
      // so the kind still reads as checkable while every id in it has just changed shape. On ids
      // alone that would mark every language model in the project dead at once, which is the false
      // death this whole function is arranged to avoid. Both halves are carried on both sides: the
      // membership row keeps `alias`, and the listing row sets it from the gateway's name.
      const held = new Set(
        (group || []).flatMap((r) => [r.id, r.alias]).filter(Boolean)
      );
      next[kind] = (groups[kind] || []).map((row) => {
        const known = held.has(row.id) || (!!row.alias && held.has(row.alias));
        const liveness = !checkable ? 'unchecked' : (known ? 'live' : 'missing');
        byParent[row.id] = liveness;
        return { ...row, liveness };
      });
    });
    // A Table under a missing Data Source is certainly unreachable, so this direction is sound. The
    // converse is not covered: a Table dropped from a Data Source that still exists stays live, and
    // finding it would cost a cascade call per row on every scope load.
    CHILD_GROUPS.forEach((kind) => {
      next[kind] = (groups[kind] || []).map(
        (row) => (byParent[row.parentId] ? { ...row, liveness: byParent[row.parentId] } : row)
      );
    });
    return next;
  }

  // Everything a fresh platform listing decides, in one place, because two writers for these
  // fields is how the rail and the catalogue end up disagreeing about what Domino holds. Called
  // once on a scope load and again each time Browse Domino opens.
  function applyListing(read) {
    // A leg that refused reports an error and no rows. Keeping the rows it could not re-read is
    // what stops an open of Browse Domino during an outage from emptying the promote picker and
    // the @ menu's catalogue half.
    const listing = SW.api.keepUnreadKinds(state.resourceListing, read);
    state.resourceListing = listing;
    state.resourceListingScope = state.scope && state.scope.id;
    applyResourceGroups(
      stampLiveness(SW.api.overlayResourceListing(state.resourceGroups, listing), listing),
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
  }

  // Read the platform once more when a leg of the listing refused. Nothing else on the panel does:
  // the next listing arrives with a scope change or with somebody opening Browse Domino, and until
  // then a refusal stands as a group note over rows that are the LAST good answer carried forward.
  // A gateway that refuses one read — a 40x while the token sidecar is still warming, measured at
  // boot — therefore leaves "the gateway answered 400" under a group visibly full of models, and
  // the only thing that clears it is an unrelated act. One re-read, once per scope load, so a
  // platform that is really down costs one extra call rather than a poll.
  function retryFailedListing(listing, gen) {
    if (listingRetryFor === gen) return;
    if (!Object.values((listing && listing.errors) || {}).some(Boolean)) return;
    listingRetryFor = gen;
    setTimeout(() => {
      // Not into a scope nobody is looking at any more. That load fires its own listing read.
      if (gen === scopeLoad) store.refreshResourceListing();
    }, LISTING_RETRY_MS);
  }

  async function loadScopeData() {
    const scope = state.scope;
    const gen = ++scopeLoad;
    const projectGen = ++projectRead;

    const [resources, activity] = await Promise.all([
      SW.api.resources(scope.id),
      SW.api.activity(scope.id),
    ]);
    if (gen !== scopeLoad) return;
    // Unless a working-set refresh has read the Project since this load started. Its answer is
    // newer than this one by exactly the Add or the Remove that fired it, and writing this
    // membership over it would put back the row the viewer has just taken out.
    if (projectGen === projectRead) {
      applyResourceGroups(resources.groups, { aliases: resources.aliases, errors: {} });
    }
    // Both halves of the last project's answer, dropped together and on the SCOPE changing rather
    // than on which membership read turned out to be newest. `catalogueParents` is the complement
    // of the members: a resource that IS a member of the scope just picked but was not a member of
    // the last one would otherwise sit in the @ menu captioned `not in {project}` — the opposite of
    // true — until the deferred listing landed. A refresh that superseded the write above cleared
    // neither, so tying the clear to that write would leave the catalogue describing the project
    // the viewer has just left.
    //
    // Only when the project actually changed. A scope change invalidates the membership the
    // listing is read against, not the platform's rows, so a reload of the same scope keeps what
    // it holds rather than replacing an open catalogue with a spinner for the length of a round
    // trip — the flicker the rows-from-the-store change exists to remove (#159).
    if (state.resourceListingScope !== scope.id) {
      state.resourceListing = null;
      state.catalogueParents = [];
    }
    // The membership written above is unstamped, and on a SAME-scope reload the listing to stamp it
    // against is still in hand — kept deliberately, three lines up. This load is not only the
    // project switch: an attach and a part-finished detach both call it, and without this
    // every `missing` mark and every group note would vanish for the length of the deferred read
    // below — 2.5-3.3 s on a real deployment — and then come back. The same re-apply
    // `refreshWorkingSet` ends on, for the same reason (#161).
    if (state.resourceListing) applyListing(state.resourceListing);
    state.resourcesLoading = false;
    state.activity = activity;
    notify();

    // The Plans group, for both modes. `loadBuild` refreshes these too, and only Build calls it —
    // so without this line the panel's Plans group was empty in Chat, where a plan is drafted.
    // Deferred and unawaited for the same reason as the listing below: the panel paints on the
    // read above, and two more routes are not a reason to hold it.
    refreshProjectPlan().then(
      () => { if (gen === scopeLoad) notify(); },
      () => {},
    );

    const appTicket = appScopeTicket();
    const listingGen = ++listingRead;
    Promise.all([
      SW.api.project().catch(() => ({ attached: [] })),
      SW.api.resourceListing(),
    ]).then(([project, listing]) => {
      if (gen !== scopeLoad) return;
      // On the ticket this load took at entry, the same one its membership stood on. A refresh
      // that has written since holds the newer answer here too, by exactly the Upload that fired
      // it. The listing below is a separate read on a separate ticket, so dropping this half of
      // the answer does not drop that one.
      if (projectGen === projectRead) applyProjectRead(appTicket, project);
      // Unless a later read has already landed — the files above are this load's own and are
      // written either way, but the platform's answer is only the newest one's to write.
      if (listingGen === listingRead) {
        applyListing(listing);
        retryFailedListing(listing, gen);
      }
      notify();
    }).catch(() => {});

    // Deferred and unawaited beside the listing above, and for the same reason: the panel paints on
    // the read already done, and a lock that has held all session can arrive a beat later without
    // anything being wrong. The badge it explains is on Datasets, which the listing above has yet
    // to deliver either.
    refreshSensitivity(gen);

    const read = await SW.api.members();
    if (gen !== scopeLoad) return;
    applyMembers(read);
    notify();
  }

  // The sensitivity lock (ADR-0043), read from the moments it can move: a scope load, a Binding
  // change, a mode change, and opening a Conversation. Never polled — a declaration is a live fact
  // about the Datasets bound RIGHT NOW, and nothing else in a session changes which those are.
  //
  // The Conversation is in that list, and is sent with the read, because half the lock is the
  // Conversation's own: once a turn of it has run under the lock the transcript holds the rows, so
  // unbinding the Dataset leaves the lock standing and the Bindings say nothing about it. Ask
  // without naming one and the picker un-greys every model the next turn will refuse.
  //
  // The mode is in that list only since the state started carrying `model`/`chat_model`, where the
  // lock moves a barred turn to. `nearest_approved` prefers the sovereign slot for the MODE, so
  // switching to Ask can move the answer where the sovereign Ask slot is a different approved model
  // — and a chip naming the model of the mode you just left is the defect these fields exist to fix.
  //
  // The picked model is deliberately NOT in the list, and that only holds because the server sends
  // where the lock MOVES a turn rather than what `resolve` would run: an approved pick is named by
  // the pick itself, right here in the browser, and `nearest_approved` reads no pick at all. Send
  // `resolve`'s answer instead and this comment becomes false — that answer IS the pick when the
  // pick is approved, so it would go stale the moment somebody picked a barred model next.
  //
  // A failed read leaves the last answer standing rather than clearing it, and the asymmetry is
  // deliberate in one direction: dropping a lock the UI is drawing would put non-approved models
  // back in the picker on a network wobble, and the picker is the surface a person acts from.
  // Nothing is enforced here, so a stale "locked" costs an explanation that is a beat out of date,
  // while a stale "unlocked" is a model somebody picks and the router then refuses under them.
  // A session lock belongs to ONE conversation (ADR-0043), so it does not survive leaving that one.
  // Dropped synchronously wherever the open conversation changes, and BEFORE the read that replaces
  // it: `refreshSensitivity` leaves the last answer standing when a read fails, which is right for a
  // Bindings lock and wrong for this one — it would draw conversation A's lock, and A's sentence
  // about rows A read, over conversation B for the rest of the session.
  //
  // Only `reason === 'session'`. That is sent only when no declared Dataset is bound, so there is no
  // live half underneath it to lose, and B's own taint arrives with the read. Clearing a Bindings
  // lock here would put non-approved models back in the picker while the Dataset barring them is
  // still attached, which is the stale-unlocked direction and the dangerous one.
  // Notifies when it actually drops one, because two of its three call sites have already rendered
  // by the time they reach it and the third is about to. Left to the read that follows, a failed
  // request would leave the stale lock on screen — which is the case this exists for.
  function dropSessionLock() {
    if (!state.sensitivity || state.sensitivity.reason !== 'session') return;
    state.sensitivity = null;
    notify();
  }

  function refreshSensitivity(gen) {
    const asked = (state.thread && state.thread.id) || '';
    return SW.api.sensitivity(asked).then(
      (read) => {
        if (gen !== undefined && gen !== scopeLoad) return;
        // The answer is about the Conversation that was open when it was asked. Opening B while A
        // is still in flight lands two of these in either order, and the older one carries A's
        // sticky lock — which is a picker greying rows out for a Conversation nobody is looking at.
        if (((state.thread && state.thread.id) || '') !== asked) return;
        state.sensitivity = read;
        notify();
      },
      () => {},
    );
  }

  // Everything a read of `/project` writes. Both refreshes take that read, and two copies of this
  // mapping would be two answers about what an Upload row carries.
  function applyProjectRead(appTicket, project) {
    // Off the same read, because this is the other moment the app's manifest changes: adding a
    // scratch file to a Dataset attaches it, and the panel refreshes through here rather than
    // through `loadBuild`. Without this the Build header would go on saying the app ships
    // nothing until the next app switch (#92).
    applyAppScope(appTicket, { appAttachments: project.attached || [] });
    // The Project's Uploads, and ONLY those. An Attachment is a record the selected app keeps,
    // so it is listed under that app and nowhere else — one row per scope, which is the rule
    // ADR-0011 already held for every other kind (#148). Both lists fed this group before, so
    // after a crossing (#147) one file was drawn three times in Build: an Upload under the
    // Project, an Attachment under the Project, and the same Attachment under the app.
    //
    // The `public/data/…` row was load-bearing rather than decorative — `collectTurnRefs` walks
    // these groups to turn "@data.csv" into a path the turn can carry — and that read is off
    // `appAttachments` now, which is where the record lives.
    const files = (project.scratch || []).map((e) => ({
      id: `file:${e.path}`,
      name: e.name || (e.path || '').split('/').pop(),
      kind: 'file',
      path: e.path,
      source: 'scratch',
    }));
    applyResourceGroups({ ...state.resourceGroups, file: files });
  }

  // Everything a working-set change can move, and nothing it cannot (#162). `loadScopeData` ends
  // every call with a platform listing read that measures 5.1 s on a real deployment (#160), and
  // putting a Resource into the Project — or taking one out — cannot change what Domino holds.
  // Membership is a local file that answers in 145 ms, so the mutation callers take this instead
  // and the listing read stays with the scope changes that are the only thing it answers.
  //
  // The listing already in hand is re-APPLIED rather than re-read, because membership is the other
  // half of what `applyListing` computes: `catalogueParents` is the platform's rows MINUS the
  // working set, so a row that just joined has to leave the @ menu's catalogue half, and one that
  // just left has to reappear there. Both without a fetch to say so. That is also why the listing
  // is not DROPPED here the way a scope change drops it — nothing is on the way to refill it, so
  // the catalogue would blank for good rather than for one round trip.
  //
  // `members` is not re-read either: who is in the Project is not something an Add can change.
  async function refreshWorkingSet() {
    const scope = state.scope;
    // Read rather than bumped, unlike the scope load: this is not a new scope, so a scope load
    // already in flight still has the newest word and must not be cancelled by an Add landing
    // under it. Same guard `reloadMembers` takes, for the same reason — it writes the same fields.
    const gen = scopeLoad;
    const appTicket = appScopeTicket();
    const projectGen = ++projectRead;
    const [resources, activity, project] = await Promise.all([
      SW.api.resources(scope.id),
      SW.api.activity(scope.id),
      SW.api.project().catch(() => ({ attached: [] })),
    ]);
    // Both reads are taken together and both are written together, so one ticket covers both.
    // Applying half of this read and half of a newer one is how the Uploads group ends up empty:
    // the membership write replaces the whole group map, and `applyProjectRead` is what puts the
    // `file` group back into it.
    if (gen !== scopeLoad || projectGen !== projectRead) return;
    applyResourceGroups(resources.groups, { aliases: resources.aliases, errors: {} });
    applyProjectRead(appTicket, project);
    state.activity = activity;
    state.resourcesLoading = false;
    // In the window between a project switch and its deferred listing landing there is nothing to
    // re-apply. That load will apply its own answer against the membership written just above.
    if (state.resourceListing) applyListing(state.resourceListing);
    notify();
  }

  // Split out of the scope load because the People modal re-reads it on its own: a Retry after a
  // failed read, and a refresh after an add or a remove. One writer for these fields, so the modal
  // and the plan page can never end up looking at different answers.
  function applyMembers(read) {
    state.members = read.members || [];
    state.directory = read.directory || [];
    state.ownerId = read.ownerId || '';
    state.selfId = read.self || '';
    state.membersConnected = read.connected === true;
    state.membersError = read.error || '';
    state.membersLoading = false;

    // Anything that renders a name or avatar looks the person up here, so
    // author IDs on plans and comments resolve even for non-members.
    state.userIndex = {};
    [...state.directory, ...state.members].forEach((user) => {
      state.userIndex[user.id] = user;
    });
    if (state.me) state.userIndex[state.me.id] = state.me;
  }

  // Under the same generation guard as the scope load, because it writes the same fields. A Retry
  // in flight when the creator switches Project would otherwise land the old Project's people on
  // the new one — and these particular fields decide who a Remove button is offered for.
  async function reloadMembers() {
    const gen = scopeLoad;
    state.membersLoading = true;
    notify();
    const read = await SW.api.members();
    if (gen !== scopeLoad) return;
    applyMembers(read);
    notify();
  }

  async function loadThreadList() {
    // Caught here rather than at each caller, because every caller has the same answer and one of
    // them is `init`. A reject out of this reaches `app.js` as a boot failure, and its error branch
    // wins over `ready` — so the Workbench painted and was then replaced by the full-page "The
    // workspace could not load", which is the wall the eight caught boot reads above exist to
    // prevent. A dead Thread index costs the conversation list, not the ability to build.
    //
    // Falls back to the list already on screen, which is `[]` at boot and, mid-session, the rows
    // somebody is looking at. Blanking the rail because one re-read failed would take rows away
    // that are still true.
    state.threads = await SW.api.threads(state.scope.id).catch(() => state.threads);
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

  // Whether the Rail on screen is one the Rail opened for itself. `railHidden` is one value
  // carrying two meanings — a choice somebody made, and a panel the UI moved on its own behalf —
  // and everything awkward about it comes from those two being indistinguishable once set. The
  // preference tells them apart on DISK (`toggleRail` is the only writer). Nothing told them apart
  // in memory, so an auto-expand had no end: `expandRail` opens the Rail to show that a press
  // worked, and only a row click ever undid it.
  //
  // Not state and not a preference. Nothing draws it, nobody chose it, and it must not survive a
  // reload — it is true only for the few seconds between a press and the conversation it starts.
  let railAutoExpanded = false;

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
      cost: 'Pick it again from Project resources and you will choose its table again — the table '
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

  // The newest thing the person asked, for the optimistic half of a decline. The decline route
  // ignores it and reads the question off the Thread, so this is never what decides anything.
  //
  // It used to decide: the same walk stopped dead at the first message that was not the person's,
  // on the theory that an answer between the question and the offer means nothing is owed. Live,
  // that read a card as an answer. "Build a dashboard from @<Dataset>" draws the Dataset file
  // picker, which is an assistant MESSAGE on screen — so the walk gave up, the client suppressed
  // the offer without ever calling the route, and "Answer it here" did nothing at all.
  //
  // The server never agreed: `handoff.unanswered_ask` blocks on the EVENT types that carry an
  // answer (agent, artifacts, stopped, error), and a `dataset-files` card is none of them. Two
  // rules for one question, disagreeing exactly where a card sits between the two. There is one
  // rule now, and it is the server's — which arm the card is decides whether to ask it.
  function lastUserText() {
    const messages = state.messages || [];
    for (let i = messages.length - 1; i >= 0; i -= 1) {
      if (messages[i].role !== 'user') continue;
      const text = (messages[i].blocks || []).find((b) => b.type === 'text');
      return (text && text.value) || '';
    }
    return '';
  }

  function summarise(text) {
    const trimmed = text.trim();
    return trimmed.length > 48 ? `${trimmed.slice(0, 48)}…` : trimmed;
  }

  function fileUrl(path) {
    return `./api/project/file/raw?path=${encodeURIComponent(path)}`;
  }

  // sage-chat writes `.table.json` in several near-contract shapes. A string is the documented
  // column; a JSON Table Schema field is `{name, type}`; pandas Index sometimes dumps as
  // `{0: "date", 1: "tokens"}`. Using any of those as a `row[column]` key left every cell null
  // while the header row and "Show all N rows" still looked like a table — the blank grid a
  // warehouse visualisation reloaded as.
  function tableColumnName(value) {
    if (value == null) return '';
    if (typeof value === 'string' || typeof value === 'number' || typeof value === 'boolean') {
      return String(value);
    }
    if (Array.isArray(value)) return value.map(tableColumnName).filter(Boolean).join(' ');
    if (typeof value === 'object') {
      const inner = value.name ?? value.title ?? value.field ?? value.key;
      if (inner != null && inner !== value) return tableColumnName(inner);
    }
    return '';
  }

  function tableColumnList(raw) {
    if (Array.isArray(raw) && raw.length) return raw.map(tableColumnName);
    if (raw && typeof raw === 'object') {
      const vals = Object.values(raw);
      if (vals.length) return vals.map(tableColumnName);
    }
    return [];
  }

  function tableKeyFold(name) {
    return String(name).toLowerCase().replace(/[\s_-]+/g, '');
  }

  function tableRecordCell(row, name) {
    if (!row || typeof row !== 'object') return null;
    if (Object.prototype.hasOwnProperty.call(row, name)) return row[name];
    const want = tableKeyFold(name);
    if (!want) return null;
    for (const key of Object.keys(row)) {
      if (tableKeyFold(key) === want) return row[key];
    }
    return null;
  }

  function tableIndexKeys(keys) {
    return keys.length > 0 && keys.every((k) => /^-?\d+$/.test(String(k)));
  }

  function tableSeriesOfScalars(value) {
    return !!value && typeof value === 'object' && !Array.isArray(value)
      && Object.keys(value).length > 0
      && Object.values(value).every((x) => x === null || typeof x !== 'object');
  }

  // pandas `df.to_json()` with no orient (columns) or `orient="index"`. Neither keeps an array,
  // so the recoveries that look for `rows` / `data` / `records` / a bare list all fall through and
  // the card says "This table came through with no rows" under the filename title. The two
  // orients are transposes of each other: columns names the frame's columns at the top level
  // with the row index inside; index names the row index at the top level with column names
  // inside. Numeric-looking keys pick which is which; a date index is columns-orient too.
  function pandasOrientedTable(obj) {
    if (!obj || typeof obj !== 'object' || Array.isArray(obj)) return null;
    const entries = Object.entries(obj).filter(([, v]) => tableSeriesOfScalars(v));
    if (!entries.length) return null;
    const objects = Object.values(obj).filter((v) => v && typeof v === 'object' && !Array.isArray(v));
    if (objects.length !== entries.length) return null;
    const keys = entries.map(([k]) => k);
    const values = entries.map(([, v]) => v);
    const inner0 = Object.keys(values[0]);
    if (tableIndexKeys(keys) && !tableIndexKeys(inner0)) {
      return {
        columns: inner0,
        rows: values.map((v) => inner0.map((c) => (Object.prototype.hasOwnProperty.call(v, c) ? v[c] : null))),
      };
    }
    const indexKeys = [];
    const seen = new Set();
    for (const v of values) {
      for (const k of Object.keys(v)) {
        if (!seen.has(k)) {
          seen.add(k);
          indexKeys.push(k);
        }
      }
    }
    // A RangeIndex is pandas' own counter and says nothing, but a labelled index is half the
    // answer, and on a square matrix it IS the answer: `df.corr().to_json()` writes the tickers
    // across the top and the same tickers down the side, and dropping the side left nine rows of
    // floats under a correct header with no way to read which pair any cell belonged to. The
    // label has no name to recover — `to_json` writes an index's values and never its name — so
    // the column heads blank, which is what a matrix's top-left corner wants anyway.
    const labelled = !tableIndexKeys(indexKeys);
    return {
      columns: labelled ? ['', ...keys] : keys,
      rows: indexKeys.map((i) => {
        const cells = values.map((v) => (Object.prototype.hasOwnProperty.call(v, i) ? v[i] : null));
        return labelled ? [i, ...cells] : cells;
      }),
    };
  }

  async function blocksForArtifacts(items) {
    const blocks = [];
    for (const art of items || []) {
      const path = art.path || '';
      const lower = path.toLowerCase();
      if (art.kind === 'chart' || lower.endsWith('.png')) {
        blocks.push({ type: 'image', title: art.title || art.name, src: fileUrl(path), path });
      } else if (art.kind === 'table' || lower.endsWith('.table.json')) {
        try {
          const res = await fetch(`./api/project/file?path=${encodeURIComponent(path)}`);
          const body = await res.json();
          if (!res.ok) throw new Error((body && body.error) || res.statusText);
          const data = JSON.parse(body.content || '{}');
          // The contract is `{title, columns, rows}` with a positional array per row. sage-chat
          // misses it two ways, and both showed a chart that plotted fine next to a table that
          // did not: pandas-style record rows (an object keyed by column name) inside the
          // wrapper, which `TableBlock`'s numeric `dataIndex` lookup rendered as blank cells
          // under a correct-looking header; and `df.to_dict("records")` dumped with no wrapper at
          // all, where `columns` and `rows` both fell through to empty and antd painted its
          // "No data" placeholder. Recover the wrapper from the records rather than trust every
          // turn's Python to have followed the contract.
          const bare = Array.isArray(data) ? data : null;
          const wrapper = bare ? {} : data;
          // A third way to miss it, and the one that reads worst: the wrapper is there, `title`
          // is there, and the rows are under some other key. A turn thinking in pandas rather
          // than in the contract reaches for `df.to_json(orient=…)`, and every orient that keeps
          // a wrapper puts its rows under `data`; a turn half-remembering the contract writes
          // `records`. Reading only `rows` left a correct title over an antd table with no
          // columns and no rows — a captioned blank box.
          let source = bare || ['rows', 'data', 'records'].map((k) => wrapper[k]).find(Array.isArray) || [];
          const head = source[0];
          // `orient="table"` names its columns in a JSON Table Schema `fields` list and nowhere
          // else. Its `index` field is one pandas synthesised, not one of the frame's own.
          const fields = Array.isArray(wrapper.schema && wrapper.schema.fields)
            ? wrapper.schema.fields.map((f) => (f || {}).name).filter((n) => n && n !== 'index')
            : [];
          // Only a record row names its columns. Reading them off a positional row would header
          // the table "0", "1", … — worse than the empty header that shape renders today.
          const named = tableColumnList(wrapper.columns);
          let columns =
            (named.some(Boolean) ? named : null) ||
            (fields.length ? fields : null) ||
            (head && !Array.isArray(head) ? Object.keys(head) : []);
          if (!source.length) {
            // The dump is not always the whole file. A turn that half-remembers the wrapper
            // writes `{title, data: df.to_dict()}`, or fills `rows` with the dump instead of a
            // list of rows, and the ladder above only looks for an ARRAY under those keys — so
            // both fell through to the blank box with a correct title still sitting above it.
            // The wrapper itself is tried first because that is the plain dump; the rest are the
            // keys a wrapper would have used.
            const dump = [wrapper, wrapper.data, wrapper.rows, wrapper.records]
              .map((o) => pandasOrientedTable(o)).find(Boolean);
            if (dump) {
              columns = dump.columns;
              source = dump.rows;
            }
          }
          blocks.push({
            type: 'table',
            title: data.title || art.title,
            // Carried so a table that still recovers nothing can hand over the file instead of
            // painting the blank box that started this.
            path,
            columns,
            rows: source.map((row) =>
              Array.isArray(row) ? row : columns.map((name) => tableRecordCell(row, name))),
          });
        } catch (err) {
          blocks.push({ type: 'file', name: art.name || path, path });
        }
      } else if (lower.endsWith('.html') || lower.endsWith('.htm')) {
        // Chat's contract is a PNG or a `.table.json` (chat/AGENTS.md), and a page is neither. But a
        // turn that wrote one anyway had already done the work, and handing back a link to a file
        // nobody can open in place threw that work away — the person asked to see their data and
        // got a filename. Shown, not run as an app: an app is what Build is for, and the offer to
        // cross over is already on the Thread.
        blocks.push({ type: 'page', title: art.title || art.name, path });
      } else if (path) {
        blocks.push({ type: 'file', name: art.name || path, path });
      }
    }
    return blocks;
  }

  // Which row in a transcript still holds a LIVE offer to start the model over, or -1 (ADR-0022).
  // Shared by both transcripts, because the ladder is one ladder and the two halves disagreeing
  // about which rung a Conversation is on would be worse than either answer.
  //
  // A `recall-cleared` retires every offer above it: the person acted, and the session those rungs
  // were counted against is gone. `dismissedRecallOffers` is the other retirement — "Not now",
  // which is this tab's alone and is deliberately not written to the transcript, because hiding a
  // card is not an answer worth keeping.
  // Keyed by surface as well as position. Under the split view the two transcripts number their own
  // rows, so a Chat offer and a Build offer can both be row 3 — and one "Not now" would hide the
  // other, on the other side of the Workbench, for a refusal nobody had seen yet.
  const dismissedRecallOffers = new Set();
  const recallOfferKey = (surface, pos) => `${surface}:${pos}`;

  function recallOfferIndex(history, surface) {
    let live = -1;
    for (const [i, ev] of (history || []).entries()) {
      if (ev.type === 'recall-suggest') live = i;
      else if (ev.type === 'recall-cleared') live = -1;
    }
    if (live < 0) return -1;
    const row = history[live];
    const pos = row.order === undefined ? live : row.order;
    return dismissedRecallOffers.has(recallOfferKey(surface, pos)) ? -1 : live;
  }

  // Which carriers this Conversation has already stopped sending (ADR-0022). Read off the
  // transcript rather than held, for the reason the withhold itself is: the poison survives a
  // restart and so must the answer to it.
  //
  // What it is FOR is retiring the card above it. A `withhold-found` row whose carriers have all
  // been withheld is a question already answered, and redrawing it leaves a person looking at an
  // offer to do the thing they just did. The `recall-withheld` row renders in its place and names
  // the same file, so nothing is lost by dropping it — the same trade `recall-cleared` makes
  // against the offer above it in `recallOfferIndex`.
  //
  // A later refusal in the same Conversation names DIFFERENT carriers, so its card is untouched.
  // "Not now" for a search card, remembered rather than only filtered out of the drawn list. On
  // Chat a filter would last until the next `openThread`; on Build only until the next two-second
  // poll rebuilt the transcript. Same lesson, same cure, as `dismissedRecallOffers` above.
  //
  // Keyed by what the card FOUND, exactly as `liveCardKey` is: it is the only field telling two
  // cards apart, and a dismissal must not silence a later refusal that names something else.
  const dismissedWithholds = new Set();
  const withholdCardKey = (ev) => (ev.carriers || []).map((c) => c && c.key).join(',');

  function withheldKeys(history) {
    const keys = new Set();
    for (const ev of history || []) {
      if (ev.type === 'recall-withheld') for (const k of ev.keys || []) keys.add(k);
    }
    return keys;
  }

  const isAnswered = (ev, keys) => {
    const carriers = ev.carriers || [];
    return carriers.length > 0 && carriers.every((c) => keys.has(c && c.key));
  };

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
    // Only the newest offer is live, for the same reason. Unlike a Build offer there is no
    // suppressed flag to consult: declining is not permanent (ADR-0022), so a later refusal
    // re-offers and the older card is simply the dead one.
    //
    // A clear retires the offer above it. Without that clause the reduce kept pointing at the last
    // suggestion whatever followed it, so the re-read that runs straight after `clearRecall` drew
    // the card again, buttons and all — asking someone to start over from a session they had just
    // started over. The next refusal writes its own suggestion and lights that one instead.
    const liveRecall = recallOfferIndex(history, 'chat');
    const withheld = withheldKeys(history);
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
      } else if (ev.type === 'table-candidates' && ev.message) {
        // The tables a search found in Chat, for the click that records one (#188). `live` is set
        // only on a frame that arrived over SSE this session, and a reload replaces the history
        // with plain server rows that never carry it — so a replayed card renders as its sentence
        // with the buttons gone, rather than writing a table and running a turn out of a message
        // somebody is only scrolling back through.
        ensureAssistant().blocks.push({
          type: 'table_candidates',
          message: ev.message,
          prompt: ev.prompt || '',
          sourceId: ev.sourceId || '',
          sourceName: ev.sourceName || '',
          // What tells the click which door to write through: with a Thread the record goes on the
          // conversation, without one it goes on the Built App's Binding.
          threadId: ev.threadId || '',
          groups: ev.groups || [],
          allGroups: ev.allGroups || [],
          total: ev.total || 0,
          matched: ev.matched || 0,
          live: !!ev.live,
        });
      } else if (ev.type === 'dataset-files' && ev.message) {
        // What a Dataset holds, asked about in Chat (#196). The click writes a `dsfile:` chip
        // rather than an Attachment — Chat has no Built App — and `threadId` is what says so.
        ensureAssistant().blocks.push({
          type: 'dataset_files',
          message: ev.message,
          prompt: ev.prompt || '',
          datasetId: ev.datasetId || '',
          datasetName: ev.datasetName || '',
          threadId: ev.threadId || '',
          rows: ev.rows || [],
          allRows: ev.allRows || [],
          total: ev.total || 0,
          listed: ev.listed || 0,
          matched: ev.matched || 0,
          truncated: !!ev.truncated,
          live: !!ev.live,
        });
      } else if (ev.type === 'withhold-found' && !isAnswered(ev, withheld)
                 && !dismissedWithholds.has(withholdCardKey(ev))) {
        // Replayed settled, never searching. `live` is absent on a server row, so the buttons do
        // not come back — the house rule for every card that can run a turn. A `withhold-search`
        // row is skipped entirely here: replaying a spinner gives one that never stops.
        ensureAssistant().blocks.push({
          type: 'withhold',
          searching: false,
          carriers: ev.carriers || [],
          complete: !!ev.complete,
          surviving: ev.surviving || 0,
          prompt: !!ev.prompt,
          stopped: ev.stopped || '',
          surface: 'chat',
          live: false,
        });
      } else if (ev.type === 'recall-withheld') {
        // Chat's half of Build's branch, and the same block: what it says does not depend on which
        // transcript it was read off, only where the click that wrote it had to go.
        assistant = null;
        messages.push({
          id: `rw_${messages.length}`,
          role: 'system',
          order: pos,
          blocks: [{ type: 'recall_withheld', labels: ev.labels || [],
                     prompt: !!ev.prompt, surface: 'chat' }],
        });
      } else if (ev.type === 'recall-cleared') {
        assistant = null;
        messages.push({
          id: `rc_${messages.length}`,
          role: 'system',
          order: pos,
          blocks: [{ type: 'recall_cleared', scope: ev.scope }],
        });
      } else if (ev.type === 'recall-suggest' && i === liveRecall) {
        assistant = null;
        messages.push({
          id: `ro_${messages.length}`,
          role: 'system',
          order: pos,
          blocks: [{ type: 'recall_offer', scope: ev.scope, offerKey: recallOfferKey('chat', pos) }],
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
    let failed = false;
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
            const ev = JSON.parse(line.slice(6));
            if (endedBadly(ev)) failed = true;
            // Withdrawn again by a `done` that says nothing about the platform. `endedBadly` reads
            // the decision, but the `error` frame carrying the sentence arrives BEFORE the `done`
            // and is a failure by frame type alone, so this has to take the flag back rather than
            // stop it being set.
            else if (ev.type === 'done' && NO_PLATFORM_FAULT[ev.decision]) failed = false;
            try { await onEvent(ev); } catch (err) { /* keep-alive or partial */ }
          }
        }
      }
    }
    // Here rather than in the three turn functions, because this is the one place every mode's
    // frames pass through and a deployment fault does not care which mode asked. Once per stream,
    // after it closes: a turn that failed on ten tool calls is one failed turn.
    if (failed) store.refreshProblems();
  }

  // Whether this frame says the turn went wrong, as opposed to stopping the way it was asked to.
  // The second Preflight ADR-0027 allows hangs off this: a turn that has just failed is the one
  // moment after boot when the answer is worth paying a gateway listing for, and it is very often
  // the reason the turn failed in the first place.
  //
  // Every deliberate ending is excluded, and there are more of them than there are failures. A
  // Stop, a Cancel, a context change and all five gate decisions are turns that ended ON PURPOSE,
  // and re-asking after one of those is the background poll again — arrived at by somebody pressing
  // buttons instead of by a timer, which makes it no less a poll.
  //
  // Read off `decision` rather than off the frame type, because a Stop arrives as BOTH: a `stopped`
  // frame, which is not a failure, and the `done` that closes it, which carries `ok: false` like
  // every other unhappy ending and would otherwise be one. `ASKED_FOR` is the whole list, and it is
  // declared below with the gate decisions it is built from.
  function endedBadly(ev) {
    if (!ev || ev.contextChanged) return false;
    if (ev.type === 'error') return true;
    return ev.type === 'done' && ev.ok === false && !ASKED_FOR[ev.decision];
  }

  // Decisions whose own card already says what happened and what to do next. A red "Stopped —"
  // line under one of those reads as a failure the user has to fix, when the turn stopped exactly
  // as designed and the thing above it is asking them a question.
  const GATE_DECISIONS = {
    'awaiting approval': true,
    'architecture ready': true,
    'reset offered': true,
    'incoming changes': true,
    // A turn refused because the model it would run on will not answer (#125). Here for the same
    // reason the four above are: the turn never ran, so the plan card must keep its Approve button.
    // Without it the refusal names a remedy — change the model, approve again — that the person is
    // left with no button to take. The `error` frame beside it already carries the whole sentence,
    // so a "Stopped — model unavailable" line under it would only say it worse, twice.
    'model unavailable': true,
    // The planner read the request and found no app in it (#150). Here for the same reason: the
    // `error` frame beside it already asks what the app should do and names the other way to run
    // the request, and this turn did exactly what it was designed to do — a red "Stopped — no app
    // described" under a question reads as a failure the person has to go and fix.
    'no app described': true,
    // The candidate cards (#183, #185, #188). Same reason again, and the cards say it loudest: each
    // one is a list of buttons asking the person to choose, and a red "Stopped —" line under it
    // reads as the app having broken rather than as a question waiting for an answer.
    'table candidates': true,
    'data source candidates': true,
    // The fifth gate, and the last of them to ship (#196, ADR-0039). Missing here since the day it
    // landed: the comment above counts five gate decisions and this list held four, so the one card
    // that asks which file to read was the one card reported as a failure. Three behaviours came
    // off that, because the two objects below are built from this one — the red line said the app
    // had broken, `KEEPS_THE_PLAN_CARD` took the Approve button off a plan the turn never touched,
    // and `ASKED_FOR` bought an ADR-0027 preflight listing to explain a question.
    'dataset files': true,
  };

  // Every ending that was ASKED FOR, which is every ending `endedBadly` above must not treat as a
  // failure. Below GATE_DECISIONS rather than beside `endedBadly`, because it spreads that object
  // and a `const` cannot be read before the line that makes it.
  const ASKED_FOR = { stopped: true, cancelled: true, 'context changed': true,
                      'plan moved on': true, ...GATE_DECISIONS };

  // Endings after which a plan card still waiting for approval keeps its Approve and Cancel. Every
  // gate decision is one, for the reason written beside them: the turn never ran, so the plan it
  // left waiting is still the plan the server would build.
  //
  // A separate list from GATE_DECISIONS rather than more entries in it, because the two answer
  // different questions — that one is "does this ending need a status line of its own", this one is
  // "does the card survive it" — and `answered` splits them. An answer-only turn is a question the
  // person asked BESIDE the card, not instead of it: it is read-only, it never touches plan.md, and
  // the server skips `plan-stale` for exactly that reason. So the card must survive it. But it also
  // has no card of its own, so the "Answered" chip is the only thing on screen saying the question
  // was heard, and suppressing that is what a GATE_DECISIONS entry would have done (#178).
  const KEEPS_THE_PLAN_CARD = { ...GATE_DECISIONS, answered: true };

  // Endings after which a gateway listing could not be the answer, so `readSSE` must not pay for
  // one. Deliberately not every ASKED_FOR decision: `model unavailable` also arrives as an `error`
  // beside a gate decision, and there the listing IS the answer — that turn was refused BECAUSE of
  // the platform, and the chip is how the person finds out (#125, ADR-0027). Here the planner read
  // the request, found no app in it and said so; nothing was asked of the platform at all, and
  // every stray note or shell command would otherwise buy a listing for a turn that worked (#150).
  // `queries failed` is here for the same reason (#203): the turn ran, the model answered, and a
  // Data Source refused a query — nothing a listing of models can say anything about.
  const NO_PLATFORM_FAULT = { 'no app described': true, 'queries failed': true };

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
  // Where a Chat upload lives: at the Project root, outside every app. `_SCRATCH_PREFIX` in
  // `sage/orchestrator/service.py` is the same string, and the two are held together by
  // `test_the_composer_reads_the_same_two_lists_the_refusal_reads`.
  const SCRATCH_PREFIX = '.sage/scratch/';

  function collectTurnRefs(text) {
    const groups = state.resourceGroups || {};
    const mentions = [];
    const resources = [];
    const seen = new Set();
    // The Project's groups, and beside them the selected app's Attachments — which the Project no
    // longer lists (#148), so without this line they would be offered by the @ menu and resolved
    // by no turn. Derived on the read rather than held in state: `appAttachments` is already the
    // record, and a second copy would need invalidating at every door that attaches or detaches.
    // Read off the text ONCE. This runs on every composer keystroke (`unusableMentions`), and it
    // asks its question of every candidate token of every row — which after a folder attach is a
    // few hundred rows times the depth of each path. Compiling a regex per question made that the
    // cost of typing.
    const typed = SW.util.mentionTokensIn(text);
    // Through `attachmentPeers`, so the folder rows the @ menu draws above the threshold are read
    // back too (ADR-0030). A folder token is not a tail of any file's own path, so without the
    // folders beside them a picked folder row would put a word in the box that the turn carries
    // nothing for — which is the silence this function ends. Files still answer a FOLDER token
    // that outlived its row (`mentionTokens` adds the parent tails), so a `@2024` kept in the
    // box after the count dropped below the threshold still names those files.
    [...Object.values(groups), SW.util.attachmentPeers(state.appAttachments)].forEach((rows) => {
      (rows || []).forEach((row) => {
        // Every token this row could have been GIVEN, not the one it would be given now. Text
        // already sitting in the composer keeps its token while the Attachment list moves under it,
        // in both directions: an attach makes `@data.csv` answer `@2026/data.csv`, and a detach
        // collapses it back. Reading only today's answer is how a mention comes to carry NOTHING —
        // no refusal, no warning — which is the one outcome ADR-0030 rules out. A token that names
        // several rows names all of them, and `_ambiguous_mentions` says so on the turn.
        if (!SW.util.mentionTokens(row).some((token) => typed.has(token))) return;
        // A bindingKey IS the Binding identity, so the rows that carry one are exactly the rows the
        // server can honor as Resources. No second list of kinds to keep in step with that one.
        if (row.bindingKey && row.bindingKey.length === 2) {
          const ref = { kind: row.bindingKey[0], id: row.bindingKey[1], name: row.name || '' };
          if (row.scope && row.scope.table) ref.table = row.scope.table;
          // The store a table row sits inside, carried beside the token rather than instead of it.
          // `name` is the word the person typed and every sentence quotes it back, so it stays the
          // TABLE's — but "@DIM_ACCOUNT. This app doesn't use it yet" then named a table where the
          // act below binds a warehouse, and the button beside it said neither. `pinRow` puts the
          // parent's name on `subtitle`; this is the one reader that needs it.
          if (ref.table && row.subtitle) ref.sourceName = row.subtitle;
          const key = `${ref.kind}:${ref.id}:${ref.table || ''}`;
          if (seen.has(key)) return;
          seen.add(key);
          resources.push(ref);
          return;
        }
        // Everything else names a path rather than a Binding, and this one list carries all of
        // them: an Attachment, which the build CAN read, and a Chat upload or a pinned Dataset
        // file, which it cannot. The server decides which is which — `_resolve_mentions` honors
        // exactly the paths in the app's own manifest — so nothing is dropped here: the turn
        // reports what it could not use, and a mention nobody hears about is one nobody can fix.
        const path = row.path || row.datasetRelPath || row.name;
        if (path && !mentions.includes(path)) mentions.push(path);
      });
    });
    // A folder row and the files it stands for both answer the same token once files grow parent
    // tails. Sending both would inline the files AND the folder summary — the bloat ADR-0029
    // took out of the block, back through this door. Keep the parent; drop what sits under it.
    // Sibling folders (a month that split into days) are not under one another, so they all stay
    // and the turn says the name matched several (`_ambiguous_mentions`).
    const nested = new Set();
    mentions.forEach((p) => {
      mentions.forEach((other) => {
        if (p !== other && p.startsWith(`${other}/`)) nested.add(p);
      });
    });
    return { mentions: mentions.filter((p) => !nested.has(p)), resources };
  }

  // Which cards THIS TAB watched arrive (#209).
  //
  // `live` is not a server fact. It is stamped on an SSE frame as the frame lands (see
  // `applyBuildEvent`) and it rides on the row `appendBuildRow` keeps — but `applyBuildRead`
  // replaces `state.buildHistory` wholesale with rows read back from the server, and those never
  // carry it. So a 2s poll, an app-rail click or a route change landing between a card arriving and
  // the person clicking took the buttons off a card this tab had watched arrive. The way forward off
  // one of these cards IS the click, so there was none.
  //
  // The distinction being drawn is "this tab watched this arrive" against "this tab is reading it
  // back", which is NOT "live against history": the rows are identical either way, and the whole of
  // the difference is what this tab saw happen. So it is held here rather than written onto a row,
  // and the reload rule survives untouched — clearing an in-memory Set is exactly what a page reload
  // does, so a replayed card is still a record of a decision rather than a button that writes a
  // Binding and rebuilds an app for somebody scrolling back through a transcript.
  //
  // KEYED ON WHAT THE CARD SAYS, because a card has no row id. `type` + `sourceId` names the two
  // cards that are about a store, and the four offers that name none have only their own words to be
  // known by; `ev.order` is no help, because `appendBuildRow` stamps one only when Build is reading
  // the merged Conversation. Two identical offers in one conversation therefore share a key and go
  // live together, which costs nothing: they are the same question about the same request, and a
  // click on either does the same thing.
  const liveCards = new Set();
  // Whose cards those are. Watching something arrive is a memory of one conversation and one Built
  // App, and switching either makes what is on screen something being read back. Compared where the
  // transcript is drawn rather than cleared at each door, so a way of moving that nobody thought of
  // here cannot leave the keys behind — `switchScope`, `createApp`, `clearConversation`, the app
  // rail and every route change move one of these two, and a door missed off a list would leave an
  // offer answerable from somewhere it does not belong.
  let liveCardsOwner = '';
  const liveCardsHere = () => [(state.thread && state.thread.id) || '',
                               (state.activeApp && state.activeApp.id) || ''].join('\u0000');
  // Joined on a character no field can hold, so no two cards can be confused by where one
  // field ends: a message and the prompt under it are both free text, and on any typeable
  // separator one pair of them could spell out another pair exactly.
  const liveCardKey = (ev) => [ev.type, ev.sourceId || ev.datasetId || '',
                               ev.prompt || '', ev.message || '',
                               // The guardrail search's card carries none of the fields
                               // above, and what it found is the only thing telling two of
                               // them apart. Without this a second refusal in the same
                               // Conversation would light the buttons back up on the first.
                               (ev.carriers || []).map((c) => c && c.key).join(',')]
                               .join('\u0000');

  // Thrown away and re-owned the moment the conversation or the app underneath them changes. It has
  // to CLEAR rather than merely stop matching: a reader who steps into another conversation and back
  // is reading the first one back, the same as after a reload, and keys that only went quiet while
  // they were away would light the buttons up again on their return.
  function syncLiveCards() {
    const here = liveCardsHere();
    if (here === liveCardsOwner) return;
    liveCards.clear();
    liveCardsOwner = here;
  }

  function rememberLiveCard(ev) {
    syncLiveCards();
    liveCards.add(liveCardKey(ev));
  }

  // What the derivation asks instead of reading `ev.live` off the row. The flag still wins where it
  // is set, so nothing about the frame itself depends on the memory being right.
  function cardIsLive(ev) {
    return !!ev.live || liveCards.has(liveCardKey(ev));
  }

  // A card is answered by clicking it, and every one of those clicks either starts a build turn or
  // resets the app — so both of those forget first. Reloading the transcript is what RETIRES an
  // offer (see `sendBuildPrompt`, `resetApp`, `chooseTableAndBuild`): the server's copy carries no
  // `live`, so the buttons go with the reload and the same card cannot be answered twice. A memory
  // that outlived the answer would have taken that rule away without anything saying so.
  function forgetLiveCards() {
    liveCards.clear();
  }

  // One table card per Data Source per turn, replaced in place as the search fills it in (#186).
  //
  // The transcript is derived from the whole event list every time a frame lands, so a search that
  // reports three databases pushes three frames through here — and pushing a block each would draw
  // one card per database under a sentence asking the person to pick a table from one warehouse.
  // Keyed on the source rather than on position, because two unscoped stores in one turn are two
  // questions and the second is asked on the turn after the first (see `named_source`).
  // Searched across every message rather than inside the current one, the way `dropTableCard` is.
  // Today the frames of one search always land in one message — the gate runs before anything that
  // would start a new one — but that is a fact about the order events happen to arrive in, and if
  // it ever stops holding, the settled card is pushed into a NEW message while the searching one
  // stays behind in the old: two cards for one search, one of them permanently reading.
  function putTableCard(messages, fallback, block) {
    for (const message of messages) {
      const at = (message.blocks || []).findIndex(
        (b) => b.type === 'table_candidates' && b.searching && b.sourceId === block.sourceId);
      if (at >= 0) { message.blocks[at] = block; return; }
    }
    fallback.blocks.push(block);
  }

  // The guardrail search's card, replaced where it stands so the spinner becomes the answer in the
  // same frame rather than a second card under the first. One search per turn, so unlike
  // `putTableCard` there is nothing to key on.
  //
  // Only ever replaces a card still marked `searching`: a settled one is the turn's answer.
  // The question the failed turn asked, so a withhold that leaves data behind can re-run it.
  // Read back off the transcript rather than held: the turn that failed may have been several
  // frames ago, and the transcript is the only copy that survives a reload.
  function lastUserPrompt(messages) {
    for (let i = (messages || []).length - 1; i >= 0; i -= 1) {
      const message = messages[i];
      if (!message || message.role !== 'user') continue;
      const text = (message.blocks || []).find((b) => b.type === 'text' && b.value);
      if (text) return text.value;
    }
    return '';
  }

  function putWithholdCard(messages, fallback, block) {
    for (const message of messages) {
      const at = (message.blocks || []).findIndex((b) => b.type === 'withhold' && b.searching);
      if (at >= 0) { message.blocks[at] = block; return; }
    }
    fallback.blocks.push(block);
  }

  // The spinner with no answer to put in its place — the same backstop `dropTableCard` is, for the
  // same end. `withhold-found` is written even when the search throws, so the only way here is a
  // stream that stopped between the two, and a card left saying "finding" over a finished turn is
  // a state nobody can answer their way out of.
  function dropWithholdCard(messages) {
    for (const message of messages) {
      const at = (message.blocks || []).findIndex((b) => b.type === 'withhold' && b.searching);
      if (at >= 0) { message.blocks.splice(at, 1); return; }
    }
  }

  // Only ever the card still saying "reading". A settled card is the turn's answer and stays.
  // A null `sourceId` means every one of them, which is what the end of a turn says: whatever the
  // search was reading, it is not reading it now.
  //
  // Over the messages already built, and never through `ensureAssistant`: a searching card can only
  // exist in a message that exists, and asking for one here would MAKE an empty assistant message
  // on every `done` — a blank row in the transcript, and a miscount for anything reading rows.
  function dropTableCard(messages, sourceId) {
    for (const message of messages) {
      for (let i = (message.blocks || []).length - 1; i >= 0; i -= 1) {
        const b = message.blocks[i];
        if (b.type === 'table_candidates' && b.searching
            && (sourceId === null || b.sourceId === sourceId)) message.blocks.splice(i, 1);
      }
    }
  }

  function buildHistoryToMessages(history) {
    const messages = [];
    let assistant = null;
    let pendingPlan = null;
    let pos = 0;
    // Only the newest offer keeps its buttons, the same rule the Chat transcript follows. An older
    // one is a record that the ladder was climbed here once; clicking it would clear a session that
    // has been replaced since, and the rung it was offered at is no longer the rung this
    // Conversation is on.
    const liveRecall = recallOfferIndex(history, 'build');
    const withheld = withheldKeys(history);
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
          // Where to go and read what this tool was called with, when the read that produced the
          // row left it out (`api.appHistory`). Absent on every other read, and the card takes
          // that absence as "there is nothing more" — which is what an empty `detail` has always
          // meant here. Only the drawer's read sets it.
          detailRow: ev.detailRow,
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
        // Deliberately nothing, and named here rather than left to fall off the end of the chain so
        // that an unread type reads as a decision instead of the oversight #94 was about.
        //
        // It used to close the plan card. It is the one event that did and is never persisted —
        // neither sender calls `persist` — so it was the only thing on that card drawn from state a
        // reload discards: the buttons went on a change request and came back on F5 (#178).
        //
        // Closing the card was also redundant. The card draws no actions at all while a turn is
        // running, and the turn that yields this ends with a persisted `done` whose decision settles
        // the card for good — so the honest ending always arrived a moment later anyway. Where the
        // two disagreed, this one was the wrong one: a typed approval yields this and can then be
        // refused for an unbound model, and that refusal tells the person to pick another model and
        // approve again (#125). Taking the Approve button first is what made that a dead end.
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
        // A turn is over, so nothing is still reading a warehouse (#186). The search takes its own
        // card back when it ends, and this is the backstop for the ends it does not reach — a
        // stream cut mid-walk, or an exception past the first frame. Without it a spinner and
        // "…is reading what X holds…" sit over a finished turn until the page is reloaded, which
        // is the one state on this card nobody can answer their way out of.
        dropTableCard(messages, null);
        dropWithholdCard(messages);
        // Two questions, read off two different things. They used to be one list, which is why
        // asking a question took the plan card's buttons away for good (#178).
        //
        // `readOnly` is the server saying this turn was never offered edit tools, so it cannot be
        // what changed the app and the plan under it is still the plan it would build. A decision
        // can't answer that on its own: `gateway error` and `stalled` end a gated turn that wrote
        // nothing and a half-finished build alike, and only the first should keep the card.
        if (pendingPlan && !KEEPS_THE_PLAN_CARD[ev.decision] && !ev.readOnly) {
          pendingPlan.pending = false;
        }
        if (!GATE_DECISIONS[ev.decision]) {
          ensureAssistant().blocks.push({
            type: 'status',
            ok: ev.ok,
            value: ev.decision === 'answered'
              ? 'Answered'
              : (ev.ok ? 'Done — build is clean' : `Stopped — ${ev.decision}`),
          });
        }
      } else if (ev.type === 'error' && ev.message) {
        // Same backstop as `done` above: a turn that failed is not still reading a warehouse.
        dropTableCard(messages, null);
        ensureAssistant().blocks.push({ type: 'status', ok: false, value: ev.message });
      } else if (ev.type === 'withhold-search') {
        // Build drew nothing for this frame, and Chat has drawn it since the search shipped. Live,
        // that is how it read: the refusal went up instantly, then fourteen seconds of nothing, then
        // a card naming files. Nothing on screen tied the second to the first, so the card read as
        // the refusal's guess repeated rather than as the answer to it — and a person who looked
        // away came back to a finding with no sign that anything had been checked.
        //
        // `live: false`, because a spinner has no buttons. It is replaced where it stands by the
        // branch below (`putWithholdCard`), and a replay walks both rows in order, so a reload
        // lands on the answer and never on the spinner.
        putWithholdCard(messages, ensureAssistant(), {
          type: 'withhold',
          searching: true,
          surface: 'build',
          live: false,
        });
      } else if (ev.type === 'withhold-found' && !isAnswered(ev, withheld)
                 && !dismissedWithholds.has(withholdCardKey(ev))) {
        // Build's half of the Chat branch above, and deliberately identical to it: the same block
        // type, the same component, the same live-vs-replay rule. Only `surface` differs, because
        // the click has to know which door to write through.
        putWithholdCard(messages, ensureAssistant(), {
          type: 'withhold',
          searching: false,
          carriers: ev.carriers || [],
          complete: !!ev.complete,
          surviving: ev.surviving || 0,
          prompt: !!ev.prompt,
          stopped: ev.stopped || '',
          surface: 'build',
          live: cardIsLive(ev),
        });
      } else if (ev.type === 'withhold-found') {
        // Found, but the card is not being drawn: already answered by a `recall-withheld` row, or
        // dismissed. The spinner above it still has to go.
        dropWithholdCard(messages);
      } else if (ev.type === 'recall-withheld') {
        // The receipt for a click, and the only thing on screen that says it worked. A divider for
        // the same reason the clear below is one: the transcript above it is still true and still
        // says what it said — what changed is what leaves for the gateway from here on.
        assistant = null;
        messages.push({
          id: `bw_${messages.length}`,
          role: 'system',
          order: pos,
          blocks: [{ type: 'recall_withheld', labels: ev.labels || [],
                     prompt: !!ev.prompt, surface: 'build' }],
        });
      } else if (ev.type === 'recall-cleared') {
        // A divider, not a status line: the transcript above it is still true, and what changed is
        // only what the model can remember of it (ADR-0022).
        assistant = null;
        messages.push({
          id: `brc_${messages.length}`,
          role: 'system',
          order: pos,
          blocks: [{ type: 'recall_cleared', scope: ev.scope }],
        });
      } else if (ev.type === 'recall-suggest' && i === liveRecall) {
        assistant = null;
        messages.push({
          id: `bro_${messages.length}`,
          role: 'system',
          order: pos,
          // `surface` is what sends the button to Build's clear rather than Chat's. The card is
          // one component on both sides because it says the same thing; the session it empties is
          // filed per (Conversation, app) here and per Thread there, and only the caller knows
          // which of those it is standing in.
          blocks: [{ type: 'recall_offer', scope: ev.scope, surface: 'build',
                     offerKey: recallOfferKey('build', pos) }],
        });
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
            "{file} calls {platformName}'s {llmGateway} directly — rewriting it to use askModel",
            { file: ev.file }
          ),
        });
      } else if (ev.type === 'gateway-alias-unbound' && ev.message) {
        // The half of that the agent cannot finish: only a person can bind an Alias (ADR-0010), so
        // this sentence is the whole point of the event and is written server-side, already
        // addressed to the creator.
        ensureAssistant().blocks.push({ type: 'status', ok: false, value: ev.message });
      } else if (ev.type === 'data-source-unasked' && ev.message) {
        // A plain line, not the refusal's red one: the build worked, and what it produced may be
        // exactly what was wanted. What it cannot be is silent — this turn ended green over an app
        // whose numbers came from the model rather than the store the person picked.
        //
        // No `ok` key at all, deliberately, as `mentions-ambiguous` does below. `ok: false` would
        // read as a failed turn, and an `{ type: 'error' }` frame would be one: `endedBadly` keys
        // on the frame type alone and would go and fetch a gateway listing over a clean build.
        ensureAssistant().blocks.push({ type: 'status', value: ev.message });
      } else if (ev.type === 'data-source-failed' && ev.message) {
        // Red, unlike the grey line above it, and that is the whole difference between the two: an
        // app nobody queried may be exactly what was wanted, and an app whose queries the store
        // refused is broken on every screen that waits on one. The `done` under this carries
        // `ok: false` to match, so the two cannot say different things about one turn.
        ensureAssistant().blocks.push({ type: 'status', ok: false, value: ev.message });
      } else if (ev.type === 'mentions-unresolved' && ev.message) {
        // Sits above the turn it belongs to rather than beside the composer: what the build could not
        // use is part of the record of that build, and a toast would be gone by the time the app it
        // built came back wrong.
        //
        // `entries` is what turns the sentence into a card (#135): one row per drop somebody can
        // close in one act, each carrying the Binding identity, the label they picked, and the app
        // the act would land in. Same `live` rule as the three offers below — a replayed refusal has
        // none, and the card draws the sentence alone. An event written before this shipped carries
        // no entries at all and reads identically, which is why no new event type was coined.
        ensureAssistant().blocks.push({
          type: 'mentions_unresolved',
          message: ev.message,
          entries: ev.entries || [],
          // What the buttons send once they have written the record (#213). A row written before
          // that shipped carries none, and its card draws the bind-only buttons it always drew.
          prompt: ev.prompt || '',
          live: cardIsLive(ev),
        });
      } else if (ev.type === 'mentions-ambiguous' && ev.message) {
        // A plain line, not the refusal's red one: this turn used everything the name matched, so
        // nothing failed and nothing is missing. What is worth knowing is that the name reached
        // more files than the person probably meant, and the sentence says which (ADR-0030).
        ensureAssistant().blocks.push({ type: 'status', value: ev.message });
      } else if (ev.type === 'reset-offer' && ev.message) {
        // `live` is set only on the frame that arrived over SSE this session (see applyBuildEvent),
        // and a reload replaces buildHistory with plain server rows that never carry it. So a
        // replayed offer renders as text with no buttons: an old message must not be able to reset
        // the app on a page load nobody connected it to.
        ensureAssistant().blocks.push({
          type: 'reset_offer',
          message: ev.message,
          prompt: ev.prompt || '',
          live: cardIsLive(ev),
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
          live: cardIsLive(ev),
        });
      } else if (ev.type === 'table-search' && ev.message) {
        // The same card while the warehouse is still being read (#186). One block that fills in
        // rather than one per frame: the search says what it has after each database lands, and a
        // transcript that grew a card per database would read as four searches instead of one.
        //
        // No `prompt`, so nothing on it is answerable — that is the server's rule and the reason is
        // about turns: a click sends the request again, and a request sent while the walk is still
        // running queues behind the turn doing the walking.
        putTableCard(messages, ensureAssistant(), {
          type: 'table_candidates',
          searching: true,
          message: ev.message,
          prompt: '',
          sourceId: ev.sourceId || '',
          sourceName: ev.sourceName || '',
          answered: {},
          groups: ev.groups || [],
          allGroups: [],
          total: ev.total || 0,
          matched: 0,
          live: false,
        });
      } else if (ev.type === 'table-search-ended') {
        // The search gave up — the store stopped answering, or it held nothing to offer — so the
        // sentence saying it is reading goes with it. Left standing it would say "reading" for the
        // rest of the session, over a turn that had already moved on to the ordinary build.
        //
        // A line in its place, because names appearing and then vanishing is the one thing
        // streaming can do that silence could not: the assistant's own "which table?" a moment
        // later does not say whether the store failed or held nothing. A Stop carries no message —
        // the person who pressed it knows why the card went.
        dropTableCard(messages, ev.sourceId || '');
        if (ev.message) ensureAssistant().blocks.push({ type: 'status', value: ev.message });
      } else if (ev.type === 'table-candidates' && ev.message) {
        // The tables a search found, for the click that records one (#183). Same `live` rule as the
        // offers around it, and the sharper reason: a replayed card would write a record and start
        // a build from a message somebody is only reading back.
        putTableCard(messages, ensureAssistant(), {
          type: 'table_candidates',
          searching: false,
          message: ev.message,
          prompt: ev.prompt || '',
          sourceId: ev.sourceId || '',
          sourceName: ev.sourceName || '',
          // Whether this card is the merged one (#206), drawn before the store was bound, so its
          // click has to declare the Binding as well as the Scope. It rides on the card rather
          // than being worked out here: the browser cannot see whether a Binding exists at the
          // moment the click lands, and guessing wrong writes the wrong record.
          bindFirst: !!ev.bindFirst,
          // The gates this turn was already past. Without them the replay walks back into a gate
          // the person has answered — most sharply the reset offer, which is a prompt match with
          // nothing remembered, so it would offer to throw the app away a second time.
          answered: ev.answered || {},
          groups: ev.groups || [],
          allGroups: ev.allGroups || [],
          total: ev.total || 0,
          matched: ev.matched || 0,
          live: cardIsLive(ev),
        });
      } else if (ev.type === 'dataset-files' && ev.message) {
        // What a bound Dataset holds, for the click that attaches one (#196). Same `live` rule as
        // the cards around it and the same reason: a replayed card would attach a file and start a
        // build out of a message somebody is only reading back.
        ensureAssistant().blocks.push({
          type: 'dataset_files',
          message: ev.message,
          prompt: ev.prompt || '',
          datasetId: ev.datasetId || '',
          datasetName: ev.datasetName || '',
          // A row is a file or a folder, and which it is decided the click as well as the label:
          // above the collapse threshold the folder is the row, so one click carries what would
          // otherwise be two hundred (ADR-0029, ADR-0030).
          rows: ev.rows || [],
          allRows: ev.allRows || [],
          // TWO COUNTS, and the button reads the first of them: `total` is what this card carries
          // and `listed` is what the listing found, which is more of them whenever the row cap cut
          // the tail. The gap reaches the person through the message rather than through a button
          // promising rows it cannot open (#200).
          total: ev.total || 0,
          listed: ev.listed || 0,
          matched: ev.matched || 0,
          truncated: !!ev.truncated,
          answered: ev.answered || {},
          live: cardIsLive(ev),
        });
      } else if (ev.type === 'source-candidates' && ev.message) {
        // The Data Sources a caller can reach, for the click that records one (#185). Same `live`
        // rule as the card below it, and the same reason: a replayed card would record a Binding
        // and start a build out of a message somebody is only reading back.
        ensureAssistant().blocks.push({
          type: 'source_candidates',
          message: ev.message,
          prompt: ev.prompt || '',
          // Empty is a state and not a missing field: it is the caller the platform offers
          // nothing, and the card says so in words rather than drawing an empty row of buttons.
          sources: ev.sources || [],
          // How many of them the request named. Zero is the card that recommends nothing.
          named: ev.named || 0,
          answered: ev.answered || {},
          live: cardIsLive(ev),
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
          live: cardIsLive(ev),
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
    // An assistant message that ended up holding nothing is not a turn saying nothing — it is a
    // block that was taken back out (#186). A search stopped or cut mid-walk leaves exactly that:
    // the searching card was the message's only block, because the gate runs before any build
    // output. Every other message here is built by pushing a block, so an empty one can only be
    // one of those. Filtered rather than never emptied, so the retirement rules stay one rule.
    return messages.filter((m) => m.role !== 'assistant' || (m.blocks || []).length);
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

  // Chat's handoff OFFER is Chat's alone — it offers a way over to Build, and both readers below
  // are Build. See `applyBuildRead` for the two shapes it arrives in.
  function isHandoffOffer(message) {
    return (message.blocks || []).some((b) => b.type === 'plan_suggestion');
  }

  // The Chat turns this pane cut away, folded where they sat (ADR-0019). Nothing hidden is hidden
  // silently: the face carries the count and the app whose Lead-in it holds, so a gap in the
  // transcript reads as another app's work rather than as a hole.
  //
  // The name comes off the rail's app list, which Build has already read — `loadBuild` loads it
  // beside this very read — the same way a `build_run` builds its face without a request of its
  // own. Six folds in one Conversation therefore cost nothing.
  async function leadInFoldMessages(hidden, handoff, selectedAppId) {
    const out = [];
    for (const gap of hidden || []) {
      const app = (state.apps || []).find((a) => a.id === gap.appId);
      const rows = (await historyToMessages(gap.rows, handoff)).filter((m) => !isHandoffOffer(m));
      out.push({
        // Keyed on the app ON SCREEN as well as the app the fold is about. Three apps in one
        // Conversation can leave app_b's Lead-in folded at the same place under app_a and under
        // app_c, and on the id alone React would reuse the component across the switch — carrying
        // an open fold into a transcript the person has not looked at yet.
        id: `lead_in_${selectedAppId || ''}_${gap.appId}_${gap.order}`,
        role: 'system',
        order: gap.order,
        blocks: [{
          type: 'lead_in_fold',
          appId: gap.appId,
          // Named, always. An app the rail cannot name — deleted, or not in this list yet — still
          // gets a face that says another app's work is behind the fold, the same fallback
          // `runningTurnElsewhere` gives a running turn it cannot name.
          appName: (app && app.name) || 'another app',
          // TURNS, not rows. A turn is one request and the work it causes (CONTEXT), so a Lead-in
          // of two questions each with an answer under it is two turns and not four — the face is
          // a person's count of what they said, not the log's count of what it wrote down.
          count: gap.rows.filter((row) => row.type === 'user').length,
          // Ids have to survive being concatenated with the turns still on screen, and the gap's
          // position in the merged read is already unique.
          messages: rows.map((m) => ({ ...m, id: `${m.id}_${gap.order}` })),
        }],
      });
    }
    return out;
  }

  // Where a Built App's Lead-in ends: the `at` of its FIRST build row in this Conversation
  // (ADR-0019). An app with no build row, or none carrying a clock, gets no entry here — it has no
  // boundary, so it has no Lead-in to be cut down to.
  function leadInBoundaries(rows) {
    const at = new Map();
    for (const row of rows) {
      if (row.half !== 'build' || !row.app || !row.at) continue;
      if (!at.has(row.app)) at.set(row.app, row.at);
    }
    return at;
  }

  // Which app a Chat turn led to: the app of the NEXT handoff after it. ADR-0019 takes the forward
  // reading, because that is the product's own story — you think in Chat, then you hand off.
  //
  // Null means a turn we cannot place: one after every boundary (the tail), or one written before
  // there was a clock to stamp. Both are SHOWN, never hidden, which is what the build half already
  // does with a row that carries no app.
  //
  // Read off `at`, never off `order`. `order` is the row's index in the merged read, and that read
  // sorts an untimed row as "all Chat, then all Build" (ADR-0009) — so attributing by index files
  // every untimed turn under the FIRST app, the exact opposite of showing it under all of them.
  //
  // Compared as strings, which is how `conversation_history` sorts them: the same comparison the
  // rows arrived in, so the boundary can never disagree with the order they are drawn in.
  function leadInOwner(row, boundaries) {
    if (!row.at) return null;
    let owner = null;
    let next = null;
    for (const [appId, at] of boundaries) {
      if (at <= row.at) continue;
      if (next === null || at < next) { next = at; owner = appId; }
    }
    return owner;
  }

  // The merged read, cut into the two halves their own readers know how to walk. `appId` narrows
  // BOTH halves to one Built App — the build turns to that app's, the Chat turns to that app's
  // Lead-in (ADR-0019). Chat passes none, because with no preview to bind it Chat shows every app
  // the Conversation drove (#56), and with no app there is nothing to attribute against either.
  //
  // A build row with no app at all is one adopted from a log written before there were per-app logs
  // (#68), so there was only one app for it to be in. It stays, because a merged view that hid it
  // would be strictly emptier than the split view it replaces — the failure the server adopts
  // legacy history to avoid.
  //
  // `hidden` is what the cut took away, grouped one entry per GAP rather than one per app: the
  // transcript is ordered, and a fold drawn anywhere but where its turns sat would put them
  // somewhere they never were.
  function splitConversationHalves(history, appId) {
    const rows = history || [];
    const chat = [];
    const build = [];
    const hidden = [];
    // An app with no boundary cuts nothing. That is a brand-new app started inside a Conversation
    // already full of talk (#74): it has no handoff yet, and the strict reading would leave it with
    // the tail alone. It shows the whole Chat half until its first build turn gives it a boundary.
    const boundaries = appId ? leadInBoundaries(rows) : new Map();
    const cutsChat = !!appId && boundaries.has(appId);
    let gap = null;
    rows.forEach((row, i) => {
      if (row.half === 'build') {
        // A gap is a run of rows nobody DRAWS, so a build row this pane keeps ends one. Without
        // this, two hidden turns either side of a later run of the selected app would fold into a
        // single row anchored at the first — a fold sitting above a build turn that happened
        // between its own turns, which is exactly the "somewhere they never were" this is ordered
        // to avoid. A build row that was filtered out draws nothing, so it ends nothing.
        if (!appId || !row.app || row.app === appId) {
          gap = null;
          build.push({ ...row, order: i });
        }
        return;
      }
      const owner = cutsChat ? leadInOwner(row, boundaries) : null;
      if (!owner || owner === appId) {
        gap = null;
        chat.push({ ...row, order: i });
        return;
      }
      if (!gap || gap.appId !== owner) {
        gap = { appId: owner, order: i, rows: [] };
        hidden.push(gap);
      }
      gap.rows.push({ ...row, order: i });
    });
    // A gap with no request in it is not a Lead-in. Those rows are the tail of a turn whose
    // question fell the other side of the boundary, and a fold over them would say "0 turns" above
    // a control that opens an answer to nothing. They go back on screen, for the same reason every
    // other row we cannot place does: shown, never hidden. `order` is what puts them back where
    // they were — every row carries its index in the merged read.
    const folds = [];
    for (const g of hidden) {
      if (g.rows.some((row) => row.type === 'user')) folds.push(g);
      else chat.push(...g.rows);
    }
    chat.sort((a, b) => a.order - b.order);
    return { chat, build, hidden: folds };
  }

  async function mergedHistoryToMessages(history, handoff) {
    const { chat, build } = splitConversationHalves(history);
    // Each half is walked by the reader that already knows how to read it — a build turn's tool
    // cards and plan cards are not chat blocks — and `order` is what puts the two back together.
    const messages = (await historyToMessages(chat, handoff)).concat(buildRunMessages(build));
    // A plan is long, and here it lands in a transcript that already carries both halves — so the
    // card that reviews it pushed the turns either side of it off the screen. Folded it reads as a
    // row: what it is, its pitch, and the way in. Stamped here rather than read in the card,
    // because this is the one reader of the preference (#56) and the card must stay a function of
    // what it was handed.
    //
    // Chat only, deliberately, and the same asymmetry the run fold has: Build draws the plan in
    // full because Build has a pane to read it in, and `applyBuildTranscript` does not come
    // through here.
    for (const message of messages) {
      for (const block of message.blocks || []) {
        if (block.type === 'build_plan') block.folded = true;
      }
    }
    return messages.sort((a, b) => a.order - b.order);
  }

  // ---- the same Conversation, in Build (#57) ---------------------------------------------------
  //
  // Build reads the merged read above rather than a second one, and it takes the halves apart
  // instead of taking one list back, because it draws them differently to Chat.
  //
  // Chat has no preview, so it shows every Built App the Conversation drove. Build has a preview
  // bound to ONE app, so BOTH its halves are that app's: the build turns it can preview, and the
  // Chat turns that led to them — the app's Lead-in (ADR-0019). The other app's work belongs on a
  // screen that can show it, and what the cut leaves behind draws a named fold in place, because
  // losing the Conversation is the failure #57 exists to stop.
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
    // Every writer ends here, which is why the cards this tab watched arrive are checked against the
    // conversation and app they were watched in HERE rather than at each door that can move one
    // (#209). A read that stays put leaves them alone — that read stripping them was the bug.
    syncLiveCards();
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
      const handoff = state.thread && state.thread.handoff;
      const chat = (await historyToMessages(halves.chat, handoff)).filter((m) => !isHandoffOffer(m));
      // The folds carry their own `order`, so they sort into the gaps they came out of rather than
      // stacking at the top.
      state.conversationChat = chat.concat(
        await leadInFoldMessages(halves.hidden, handoff, state.activeApp && state.activeApp.id));
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

  // Which Problems this tab has already pointed a toast at (ADR-0027). Per session and per id: the
  // toast fires on a Problem's FIRST appearance and never again, so a fault that stands for an hour
  // interrupts once and then lives in the chip, which is the placement that can hold it.
  //
  // Deliberately outside `state` and never cleared. It is bookkeeping about what the reader has
  // been shown, not a fact about the deployment, and nothing renders it. A Problem that clears and
  // comes back stays quiet for the rest of the session, which is the right way round: a flapping
  // fault is the one a repeated toast would be worst for.
  const toastedProblems = new Set();

  // How long the boot leaves between its two Preflights. Long enough that a workspace whose proxy
  // was still coming up on the first ask is serving by the second — Domino reports one running
  // about a second early — and short enough that a real fault is on the chip before somebody has
  // finished reading the greeting and typed a question.
  const PREFLIGHT_SETTLE_MS = 4000;

  // What a "Build this again" turn is called in the transcript (ADR-0024). Kept in step with
  // `_BUILD_AGAIN_TEXT` on the server, which writes the row this one stands in for until the
  // history reloads.
  const BUILD_AGAIN_TEXT = 'Build this again from the edited plan.';

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
  // `appId` is the third axis and only Build passes one: the Build transcript is ONE app's log for
  // one Conversation, so a build you switched the rail away from is as invisible as one in another
  // conversation (#126, #77). A running turn with no app — every Chat turn, and any build whose app
  // could not be read — matches whatever is on screen, so the Stop bar fails towards being offered.
  function runningTurnHere(kind, conversationId, appId) {
    const t = state.runningTurn;
    if (!t || t.kind !== kind || !conversationId || t.conversation !== conversationId) return false;
    return !t.app || !appId || t.app === appId;
  }

  // The other side of the same question, for the line a mode shows INSTEAD of Stop. Only ask it
  // when the Project is busy — it describes the lock, not whether anything holds it.
  //
  // `href` is null when the holder has no identity. Publish, reset and the other raw-lock callers
  // never queued, so there is nowhere to send anyone and nothing to stop; saying the workspace is
  // busy is the honest answer, and it is also what stops a Stop button rendering over a publish.
  function runningTurnElsewhere(kind, conversationId, appId) {
    const t = state.runningTurn;
    if (!t) return { text: 'The workspace is busy.', href: null };
    if (runningTurnHere(kind, conversationId, appId)) return null;
    const what = t.kind === 'chat' ? 'Chat is answering' : 'Build is running';
    // Name the axis that actually DIFFERS. A build running in this very conversation on another
    // Built App, described by its conversation, names the place you are already standing and links
    // you back to it — a sentence that answers nothing and a link that goes nowhere.
    if (t.conversation === conversationId && t.app && t.app !== appId) {
      const app = (state.apps || []).find((a) => a.id === t.app);
      return { text: `${what} in ${(app && app.name) || 'another app'}`,
               href: `#/build/${t.conversation}?app=${t.app}` };
    }
    const row = (state.threads || []).find((x) => x.id === t.conversation);
    return { text: `${what} in ${(row && row.title) || 'another conversation'}`,
             href: `#/${t.kind}/${t.conversation}` };
  }

  // A turn this tab is streaming, named by the tab itself (#126). `applyTurnState` was the only
  // writer of `state.runningTurn`, and nothing polls `/build/state` while a send is holding its own
  // stream open — so a build started here had no named turn to match for its whole length, and the
  // Stop bar under the composer stayed missing until a mode switch reloaded the state behind it.
  // A streaming turn can answer the question itself: it knows its kind, its conversation and its
  // app, which is every field the bar compares. Claimed on the first frame that is neither the
  // queue's `pending` nor one of the two ways a turn ends without ever running.
  function claimRunningTurn(kind, conversationId, appId) {
    const claim = { kind, conversation: conversationId || '', app: appId || '' };
    state.runningTurn = claim;
    return claim;
  }

  // Forget it again as the send unwinds — but only while this tab's own claim is still the one
  // standing. A poll, or a later turn in this tab, may have replaced it with a different answer,
  // and that one outlives the turn that is ending here.
  function releaseRunningTurn(claim) {
    if (claim && state.runningTurn === claim) state.runningTurn = null;
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
    // The server named this Conversation off the first thing typed into it, at the top of the turn,
    // and says so here. Applied rather than fetched: nothing on this path re-reads the Thread index
    // — `sendBuildPrompt` re-reads the preview and the Bindings and never the list — so before this
    // the rail kept the words "New conversation" for the whole build and for every build after it.
    //
    // Both halves, because they answer the same question in two places and a rail disagreeing with
    // its own header is worse than a rail that is merely behind. Guarded on the id: a turn streams
    // on after you open another Conversation (#77), and its name belongs to the row it named, not
    // to whichever row you have since moved to.
    //
    // Returns, like the other events that are pure state: there is no block to add to a transcript
    // for a rename, and a reload reads the name off the Thread row where it has always been.
    if (ev.type === 'conversation_named') {
      const rename = (row) => (row && row.id === ev.conversation ? { ...row, title: ev.title } : row);
      state.thread = rename(state.thread);
      state.threads = state.threads.map(rename);
      notify();
      return;
    }
    if (ev.type === 'stopped') return;
    if (ev.type === 'active' || ev.type === 'phase' || ev.type === 'typecheck-start' || ev.type === 'iterate') return;
    // The buttons on a reset offer belong to the offer the user is looking at, not to every copy of
    // it the transcript keeps. Marking the live frame is what separates the two — the server row a
    // reload returns has no `live`, so it replays as text (see buildHistoryToMessages).
    //
    // A refusal joins them for the sharper version of the same reason (#135): its buttons act on the
    // app selected NOW, while the refusal names the app that was selected then, and a gap reported
    // six weeks ago has probably been closed since.
    if (ev.type === 'reset-offer' || ev.type === 'incoming-changes'
        || ev.type === 'build-stalled' || ev.type === 'mentions-unresolved'
        || ev.type === 'table-candidates' || ev.type === 'source-candidates'
        || ev.type === 'dataset-files' || ev.type === 'withhold-found') {
      ev.live = true;
      // And remembered past this row's own life (#209). The next transcript read replaces the row
      // with the server's copy of it, which carries no flag — so the mark on the row alone lasted
      // only until the next poll, and the buttons went with it.
      rememberLiveCard(ev);
    }
    appendBuildRow(ev);
  }

  async function refreshBindings(ticket = appScopeTicket()) {
    const body = await SW.api.bindings().catch(() => ({ bindings: [] }));
    applyAppScope(ticket, { bindings: body.bindings || [] });
    // Binding a Dataset is what arms the lock, and unbinding the last one is what drops it — so
    // every caller that changes a Binding has to re-ask, and putting it here is how none of them
    // has to remember. Unawaited: the Bindings are written above and the lock is drawn beside them
    // rather than by them.
    refreshSensitivity();
  }

  // The names at whichever level the Scope door is standing on (#142). One request, off the same
  // three routes the Resource Browser's cascade walks — the two surfaces ask the same store the
  // same questions, they just do different things with the answer.
  //
  // A store that will not answer is not a Scope the creator has lost: the levels they already
  // chose still stand, and what is missing is the list of what is under them. So the error is
  // recorded beside the position rather than in place of it, and the door goes on offering the
  // position as an answer — the same rule `_write_bound_schema` follows when the columns fail.
  async function loadScopeLevel() {
    const pick = state.scopePick;
    if (!pick) return;
    const gen = ++scopePickLoad;
    // A ladder with no rungs is not a level that came back empty. Either Sage has no dialect for
    // this connector, or the Project row that carries the levels is not on hand — and asking the
    // table route with nothing above it would ask a question neither case has an answer to. The
    // door says so, which is what `DataSourceCascade` does with the same fact.
    if (!(pick.levels || []).length) {
      state.scopePick = { ...pick, items: [], error: null, unreadable: true };
      notify();
      return;
    }
    const stage = SW.util.cascadeStage(pick.levels, pick.database, pick.schema);
    let items = [];
    let error = null;
    try {
      const body = stage === 'database'
        ? await SW.api.dataSourceDatabases(pick.id)
        : stage === 'schema'
          ? await SW.api.dataSourceSchemas(pick.id, pick.database)
          : await SW.api.dataSourceTables(pick.id, pick.database, pick.schema);
      items = body.items || [];
    } catch (err) {
      error = err.message || '';
    }
    if (gen !== scopePickLoad || !state.scopePick) return;
    state.scopePick = { ...state.scopePick, items, error };
    notify();
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
  // Both reads, always together. The panel draws ONE list of plans and marks the row the selected
  // app is being built from, and the two facts come from two routes — `/project/plan` knows which
  // document plan.md belongs to, `/plans` knows what documents there are. Refreshed apart, a
  // freshly drafted plan would be marked live before its row existed, or keep the mark after
  // another app's build took it. Every caller of this already fires at the moments that move
  // either one, so folding the second read in here is what keeps them in step.
  //
  // The listing is caught rather than awaited into the failure: a Project whose plan documents
  // cannot be read still has a pin, and a panel that renders no Plans group is a better answer
  // than a panel that renders nothing.
  async function refreshProjectPlan() {
    // Both plan reads are the Project's, so a Project switch while they are out makes both answers
    // somebody else's. Every other deferred read here is guarded on `scopeLoad`; these two were
    // not, and only got away with it while `loadBuild` was the sole caller — the panel's Plans
    // group is now refreshed from `loadScopeData`, which is exactly the read a switch supersedes.
    const gen = scopeLoad;
    const [plan, plans] = await Promise.all([
      SW.api.projectPlan(),
      SW.api.plans().catch(() => []),
    ]);
    if (gen !== scopeLoad) return;
    state.projectPlan = plan && plan.markdown ? plan : null;
    state.plans = plans || [];
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
      await refreshWorkingSet();
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
      // The index is a view of the groups, and two readers ask it whether a Resource is in the
      // working set at all — the drawer's `inProject`, and `bindToApp` deciding whether a bind
      // just changed membership. Assigning the groups through here without rebuilding it left
      // those two answering from a list nobody had updated.
      if ('resourceGroups' in patch) state.resourceIndex = indexResources(state.resourceGroups);
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
        // Only while the lock is actually holding: an opted-out deployment must not pay a round
        // trip per mode toggle to be told again that nothing narrows. Generation-tagged like the
        // scope load's own read, so a slow answer for the project somebody just left cannot land
        // on top of the one they are now looking at.
        if (SW.util.isLocked(state.sensitivity)) refreshSensitivity(scopeLoad);
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
      // Beside the panel read, because this drawer is the one surface that draws every closed row
      // at once (ADR-0043): an administrator who has just added a model to the group is most likely
      // to be looking here, and a lock read on the last scope load would still be showing it out.
      if (open) refreshSensitivity();
    },

    // Saves immediately and verifies afterwards (ADR-0017): blocking the write on a live gateway
    // call would make a setting refusable for a reason outside the person's control, and the greyed
    // rows already stop the common case at draw time.
    async setAssignment(slot, model) {
      try {
        const status = await SW.api.setModelAssignment(slot, model);
        applyModelStatus(status);
        // The POST answered with the whole catalog, so the saved value is already known here — and
        // writing it into the panel's own rows is what keeps a failed reload honest. Without it the
        // reload's catch leaves the PRE-save rows in place, and they redraw under "couldn't check
        // every model" while the chip shows the new one: two controls disagreeing, with the drawer
        // telling the reader their save was refused. `problem` goes back to null because the
        // verdict on the new model is exactly what has not been fetched yet.
        if (state.assignments && state.assignments.slots) {
          state.assignments = {
            ...state.assignments,
            slots: state.assignments.slots.map((r) => (r.slot === slot
              ? { ...r,
                  model: (state.catalog || {})[slot] || r.model,
                  assigned: Boolean(model),
                  problem: null }
              : r)),
          };
        }
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

    // Problems -----------------------------------------------------------
    //
    // One Preflight, and the toast for whatever it turned up that this session has not seen
    // (ADR-0027). Called at boot and after a failed turn, and from nowhere else: a background poll
    // is the thing that decision rejects by name, because it would be one gateway listing
    // multiplied by every open Workbench forever to learn what the next turn reports for free.
    //
    // Nothing here blocks anything, and nothing here composes a sentence. `state.problems` is the
    // payload as it came, and every control on screen stays exactly as usable as it was — a chip
    // that also locked controls would turn one dead service into a jail, which is the boot failure
    // this work started from.
    async refreshProblems() {
      // The list already on screen survives a read that did not land. The route answers 200 even
      // when every one of its own five reads failed, so a rejection is the route being unreachable
      // rather than a verdict of "clean" — and replacing a true chip with silence on that is how a
      // person comes to report a failed build as a bug.
      const found = await SW.api.health().then(
        (body) => (body && Array.isArray(body.problems) ? body.problems : []),
        () => null,
      );
      if (found === null) return state.problems;
      state.problems = found;

      // One toast for however many are new, not one each. The count is the whole message: a toast
      // may point at content and may never BE the content, which is the rule that lets ADR-0027
      // have a toast at all while ADR-0011's "never a toast" stands unchanged for anything somebody
      // has to read. The sentences, the remedies and the quoted platform bodies are in the drawer,
      // where they stay on screen for as long as they are true.
      const fresh = found.filter((p) => p && p.id && !toastedProblems.has(p.id));
      for (const p of fresh) toastedProblems.add(p.id);
      if (fresh.length) {
        // No {tokens} and no glossary noun, so this one sentence is written here rather than
        // server-side like every Problem's own: a count and a direction carry no term the pack
        // could rename, and routing it through the payload would mean the server composing copy
        // about the client's own furniture.
        antd.message.warning(fresh.length === 1
          ? '1 problem needs attention. Open Problems in the top bar.'
          : `${fresh.length} problems need attention. Open Problems in the top bar `
            + 'to read them.');
      }
      notify();
      return found;
    },

    // The chip's drawer. No read of its own: opening is not a Preflight, because the answer it
    // would give is the answer already on screen, and pressing the chip is not evidence that
    // anything changed.
    openProblems(open) {
      state.problemsOpen = Boolean(open);
      notify();
    },

    async init() {
      // Domino reports a workspace as running before its proxy serves, so the first call out of a
      // Workbench somebody has just opened can come back 502 from nginx — not from Sage, which is
      // why it arrives with no sentence in it. It clears itself in seconds, and while it lasts
      // every read below falls back to its own empty value: a Workbench with no Projects, no
      // conversations and no viewer, silent about all three, because each of those catches is
      // right about one dead service and wrong about a container that has not opened yet. So the
      // boot waits for the proxy before it reads anything, and reports only what is still broken
      // after the wait (ADR-0027).
      //
      // /healthz is the probe because it is already the readiness signal the door redirect waits
      // on, and it is already read here for the picker's open-weight list. This is that read moved
      // in front of the others, not a second readiness mechanism.
      //
      // The two failures out of it are not caught the same way. A proxy that never started serving
      // is rethrown once the budget is spent, so `app.js`'s full-page error stands with the
      // platform's own words on it — ten seconds of 502 is a container that is not coming up by
      // itself, and a silent empty Workbench gives nobody anything to reload past. Anything else is
      // /healthz alone being unhappy, and /healthz is only the picker's open-weight list here:
      // caught, exactly as it was when it sat in the batch below. The Problems this deployment has
      // are `/api/health`'s, further down, and were never this route's to report.
      const healthz = await SW.api.throughStartup(() => SW.api.healthz(), {
        onWaiting: () => {
          if (state.bootStatus === 'waiting') return;
          state.bootStatus = 'waiting';
          notify();
        },
      }).catch((err) => {
        if (SW.api.stillStarting(err)) throw err;
        return null;
      });

      // Every read below is caught, because the Workbench is not one service. Who is looking, the
      // Project listing, the chart registry, the starter deck and the bell answer from different
      // places, and any of them can be down while the thing somebody came here to do — build —
      // still works, since not one of these seven reads is that thing. An
      // uncaught reject in this Promise.all is not a missing panel: it reaches `app.js`, which turns
      // a boot failure into the full-page "The workspace could not load", so ONE dead service takes
      // the whole Builder with it. ADR-0027 names that shape as the reason it informs and never
      // blocks: a dead Project listing must not become a jail for somebody who could still have
      // built. Each fallback is that read's own honest empty value, and none of them says anything —
      // reporting is a separate concern with its own placement (the chip), not a boot-time throw.
      const [me, projects, charts, starters, notifications, brand, project] = await Promise.all([
        // Null is what `state.me` already holds before this answers, and the greeting is written for
        // it — it drops the first name. It is not free, though, and the cost is not the greeting:
        // `prefs` keys the viewer's whole preference record on `me.id`, so with no id there is no
        // record of theirs to open and none to write. `prefs` refuses both rather than falling back
        // to a shared key, which is where this session's panel choices used to land. An unreadable
        // viewer costs a name and a remembered panel, not a session.
        SW.api.me().catch(() => null),
        // Empty rather than `state.projects`, because empty is what "we could not read the listing"
        // honestly looks like, and the chip must not offer rows to switch to that we did not read.
        // The scope fallback below keeps it naming where you are.
        SW.api.projects().catch(() => []),
        SW.api.charts().catch(() => ({})),
        SW.api.starters().catch(() => null),
        // Caught for the same reason as the four above, even though `api.js` cannot reject it today:
        // it is the stub `empty()`, and the four reads beside it that already carry a URL say what
        // happens the day this one does. A bell with nothing in it is not worth a wall.
        SW.api.notifications().catch(() => []),
        SW.api.brand().catch(() => state.brand),
        // Read here rather than off the deferred `/project` in loadScopeData, because both links it
        // carries are drawn in chrome that paints as soon as `ready` flips. Deployment constants,
        // built once from env at boot, so reading them once at boot is the whole of it.
        SW.api.project().catch(() => null),
      ]);
      state.me = me;
      // Both side panels, before `ready` flips. A preference is keyed by viewer, so this is the
      // first moment there is a record to read — and it has to happen before the Shell paints, or
      // the Rail and the dock would open on the fallbacks and then shut again in front of someone.
      //
      // Safe with no viewer, and not by luck: `prefs` refuses to read or write a record it cannot
      // key, and hands back the fallback a first visit gets. The refusal lives there rather than
      // here because the writers need it too — ⌘/ and ⌘\ are live while the boot spinner is up.
      state.railHidden = SW.prefs.get('railHidden');
      state.dockTab = SW.prefs.get('dockTab');
      state.dockWidth = SW.prefs.get('dockWidth');
      if (brand) {
        state.brand = brand;
        applyBrandChrome(brand);
      }
      state.projects = projects;
      state.canProvision = !!(projects[0] && projects[0].provisioning);
      // `projects[0]` is this container's own row, and it is the only row that carries the bound
      // Project's display name and its model slots — the rest are Domino names and an id to attach
      // by. When the listing read failed there is no such row at all, so both come off `/project`
      // instead: `Project.status()` is built from this container and asks the control plane nothing,
      // which is exactly why it can still answer when the listing cannot. The chip then goes on
      // saying where you are and loses only the ability to move somewhere else, and the picker
      // already explains that loss on the control itself, because `canProvision` is false above and
      // the disabled New project button draws its own reason.
      state.scope = projects[0] || (project && {
        ...NO_SCOPE,
        id: project.id,
        name: project.name || project.id,
        untitled: !!project.untitled,
        current: true,
      }) || state.scope;
      // Same substitution, same reason: the model block in `projects[0]` was read off `/project` in
      // the first place, so reading it from `/project` directly loses nothing. Without this, Build's
      // picker would open on the seeded catalog with no slot marked current.
      applyModelStatus(projects[0] || project);
      state.charts = charts;
      state.starters = starters;
      state.notifications = notifications;
      state.manageUrl = (project && project.manage) || null;
      state.costUrl = (project && project.cost && project.cost.url) || null;
      state.ready = true;
      state.bootStatus = null;
      state.openWeightModels = (healthz && healthz.open_weight_models) || [];
      state.resourcesLoading = true;
      notify();
      // A reload during a turn lands here with no stream and no memory of one. Ask the lock, so
      // the composer opens disabled with a Stop beside it rather than taking a question the
      // server is about to refuse.
      await Promise.all([loadScopeData(), loadThreadList(), store.refreshTurnState()]);
      // The boot Preflight (ADR-0027). Last in the boot because it is the one read nothing else on
      // screen is built out of, so every panel above is already drawn while this asks.
      //
      // TWICE, and this is the survival rule rather than a retry. A Problem is reported only when
      // the Preflight BEFORE it found the same one, and that count is kept server-side across the
      // whole process — so on a Workbench that is first through the door, one ask can never report
      // anything, whatever is wrong. Two is the smallest number that can, and the pause between
      // them is what a self-clearing fault falls through: Domino reports a workspace running before
      // its proxy serves, so the first ask sees faults that are gone by the second.
      //
      // Two and then stop. It is not the poll ADR-0027 rejects, because it does not repeat: the
      // only other Preflight this tab ever makes is after a turn has actually failed. And when
      // another tab has already been through here the process has its count, the first ask answers
      // in full, and the second is skipped.
      if (!(await store.refreshProblems()).length) {
        setTimeout(() => store.refreshProblems(), PREFLIGHT_SETTLE_MS);
      }
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
      // Same rule as the apps below: the documents belong to the Project being left, and the
      // panel's Plans group would otherwise list another Project's plans under this one's name
      // until the deferred read landed.
      state.plans = [];
      state.projectPlan = null;
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
        title: `Creating the project ${trimmed}`,
        detail: 'Setting up the repository and starting your workspace. This takes about a minute.',
        // "couldn't create Sales dashboard" reads as a failure to build the thing named, which is
        // not what failed — the project it would live in never got made. Name the noun.
        failure: SW.brand.text("{assistantName} couldn't create the project {name}", { name: trimmed }),
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

    // The writers. A person's hand is on the control in each of these, so each records the answer —
    // a panel you opened is open again on the next load, and a panel you closed stays closed.
    // `focusPanel` below is the deliberate exception.
    //
    // `dockTab` holds `'resources'` or `null` and there is no second value to pick since ADR-0035:
    // one panel, so the tab bar it belonged to is gone. The key and its two doors survive because
    // the STATE they carry — open or closed, remembered per viewer (#150) — is unchanged, and
    // because `'activity'` is already written into records that exist (see `prefs.js`).
    openDock() {
      state.dockTab = 'resources';
      SW.prefs.set('dockTab', 'resources');
      notify();
    },

    // Appearance, from Account settings (ADR-0044). One writer for the theme and both names,
    // because all three land in one file behind one route.
    //
    // The server's answer is installed rather than the patch: it resolved the write against a
    // baked pack that may overrule part of it, and painting what was asked for would leave the
    // screen disagreeing with the next reload. That is also why nothing here is optimistic — the
    // round trip is one small file write, and a theme that flickered back would be worse than one
    // that took a moment to arrive.
    async saveBrand(patch) {
      const brand = await SW.api.saveBrand(patch);
      state.brand = brand;
      applyBrandChrome(brand);
      notify();
      return brand;
    },

    // The dock's left edge, dragged. A width the stylesheet picked is not written here —
    // only a number that came off the handle, already clamped to the preference's range.
    setDockWidth(px) {
      const range = SW.prefs.range('dockWidth');
      const width = Math.round(Number(px));
      if (!range || !Number.isInteger(width)) return;
      const clamped = Math.min(range.max, Math.max(range.min, width));
      if (clamped === state.dockWidth) return;
      state.dockWidth = clamped;
      SW.prefs.set('dockWidth', clamped);
      notify();
    },

    // The one act three doors mean: the panel's own Hide button, the collapsed dock's Show button,
    // and ⌘/. It used to be `toggleDock(tab)`, which toggled ONE tab and so only closed when handed
    // the tab already open — every door passed the constant `resources`, so with the panel open on
    // Activity a control captioned "Hide the side panel" switched tab instead, and the shortcut the
    // help drawer advertises as a toggle took two presses to close (#150). With one panel the
    // distinction has nothing left to be about, and the writer says what the doors say.
    toggleDockOpen() {
      state.dockTab = state.dockTab ? null : 'resources';
      // A filter is a question about a list nobody is looking at once the panel shuts.
      if (!state.dockTab) state.panelFilter = null;
      SW.prefs.set('dockTab', state.dockTab);
      notify();
    },

    toggleRail() {
      state.railHidden = !state.railHidden;
      // A hand on the control, so whatever the Rail had opened for itself is over — in both
      // directions. Opening by hand makes this a choice rather than an answer to a press, and
      // closing by hand ends the open outright.
      railAutoExpanded = false;
      // A filter is a question about the list, and it is dropped whichever way this goes.
      //
      // Closing: a closed Rail is not showing the list, so leaving it set would bring it back on
      // the next open as a filter nobody could see they had applied.
      //
      // Opening: the same unexplained narrowing arrives by the other door, because the filter can
      // be set WHILE the Rail is hidden — Build's header writes it on every app pick, and the Rail
      // now starts hidden, so that is the ordinary case rather than a corner. Clearing only on
      // close was the same rule stated half way.
      state.railAppFilter = null;
      SW.prefs.set('railHidden', state.railHidden);
      notify();
    },

    // The Rail closing because you picked a Conversation, which is not the same act as closing it.
    // It does NOT write the preference, and that is the whole point of having a second method:
    // auto-collapse and the stored choice are one value, so a write here would take a Rail someone
    // deliberately opened and record it as closed on their very next click — a preference they
    // never set, overwriting the one they did. One value, one meaning: the pref is the hand-made
    // choice, and only `toggleRail` above has a hand on it.
    collapseRail() {
      state.railHidden = true;
      railAutoExpanded = false;
      state.railAppFilter = null;
      notify();
    },

    // The mirror of it, and it does not write the preference for the same reason: the Rail opening
    // because a press had nowhere else to show its answer is not somebody choosing to keep the
    // list open. `newConversation` is the one caller — the pending row it sets is the only thing
    // on screen that says the press worked, and from the collapsed head that row is behind the
    // panel you just clicked, so the press looked dead.
    expandRail() {
      state.railHidden = false;
      // Marked, because this is the one open nobody asked for. See `newThread`, which is where
      // the reason in the comment above runs out and this gets read back.
      railAutoExpanded = true;
      state.railAppFilter = null;
      notify();
    },

    // Called when a script turn has opensPanel. Sage asking you to pick a kind
    // of thing is a browse task, so it opens the catalogue scoped to that kind
    // rather than filtering a panel that may not contain the answer yet.
    //
    // It opens the dock and does not write the preference: Sage asked for the panel, not the
    // person, and only the person's own choices are on file (#150).
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

    // These are shared platform Resources: another person can delete one, or add one, between a
    // scope load and somebody opening Browse Domino. So the catalogue re-reads the platform once
    // when it opens and redraws when the read lands — one refresh per open, in the background,
    // with the rows already on screen the whole time.
    async refreshResourceListing() {
      const scopeGen = scopeLoad;
      const gen = ++listingRead;
      const listing = await SW.api.resourceListing().catch(() => null);
      if (gen !== listingRead || scopeGen !== scopeLoad) return;
      if (!listing) {
        // A platform that refused is answered with error strings rather than a rejection, and
        // `applyListing` carries the unread kinds over — so reaching here at all takes a fault in
        // the read itself. Whatever the cause, what must not be left standing is "Sage has not
        // looked yet", which is a spinner with nothing left to end it.
        if (state.resourceListing) return;
        state.resourceListing = {
          errors: { listing: SW.brand.text("Couldn't read {platformName}.") },
          groups: {},
        };
        state.resourceListingScope = state.scope && state.scope.id;
        notify();
        return;
      }
      applyListing(listing);
      retryFailedListing(listing, scopeGen);
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
      await refreshWorkingSet();
      if (!options.silent && result.added) {
        antd.message.success(`${resource.name} is now in ${state.scope.name}.`);
      }
      return result;
    },

    // Where a Binding is taken back: the app's own list, which is the list that owns the scope
    // (ADR-0011). Pointed at from a Project row whose Resource is gone from Domino and is still
    // bound — that row's Remove is refused by `remove_project_resource` with a 409 naming this very
    // app, so the row offers the act that would work instead of the one that cannot (ADR-0034). The
    // guard on the Resource itself is left alone: relaxing it for a dead Resource would silently
    // break an app that still ships the Binding.
    //
    // Selected before the route is written, in that order and for the reason `buildPlanAgain` does
    // it: the server holds one selected app per Project, so a route that lands ahead of the
    // selection lands on somebody else's Bindings.
    async openAppBindings(appId) {
      if (!appId) return;
      // `selectApp` swallows its own failure: it warns and hands back the app that was already
      // selected. Routing anyway would put a dead app id in `?app=`, and BuildMode's effect would
      // ask for it again and warn a second time over a page still showing the old app. So the
      // navigation is gated on the select having actually landed — the warning is already on
      // screen, and one is enough.
      const selected = await store.selectApp({ id: appId });
      if (!selected || selected.id !== appId) return false;
      SW.router.go(SW.appRoute({ id: appId }));
      // Said on arrival, always. The row this came from is styled as a removal and labelled with a
      // removal's words, because that is what the 409 names and what ADR-0011 makes the app's act —
      // but pressing it moves the creator rather than removing anything, and a destructive-looking
      // control that silently teleports you owes you the sentence saying why you are here.
      // Opened, not merely named. The list of what an app uses is the App dependencies modal now
      // (ADR-0035), which is behind a header menu item — so a pointer that only said the words
      // would land the reader on a preview with no list in sight. ADR-0011's rule is that a
      // pointer is a promise the destination can act; this is that promise kept.
      state.appDependenciesOpen = true;
      antd.message.info(`Remove it from ${appScopeName()}.`);
      notify();
      return true;
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
              // A live conversation holding a context chip refuses it too (#168), and that holder
              // has no app source behind it — so `refs` is empty and the conversation titles are
              // the only thing standing between the reader and a sentence they cannot act on.
              const held = (err.payload && err.payload.conversations) || [];
              if (apps.length || held.length) {
                // Lists, not the server sentence: err.message already concatenates the same
                // names, and reprinting them under Held in is how untitled chats become a wall.
                // Both ways out of a chip stay on the conversation group: closing it keeps the
                // history, deleting the conversation does not, and only the reader knows which.
                const notice = SW.util.stillBoundNotice({
                  apps, refs, conversations: held, scopeName,
                });
                antd.Modal.info({
                  title: notice.title,
                  content: notice.content,
                  okText: 'Got it',
                  className: 'sw-still-bound-modal',
                  width: 440,
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
            await refreshWorkingSet();
            antd.message.info(`${resource.name} is out of ${scopeName}.`);
            resolve(true);
          },
          onCancel: () => resolve(false),
        });
      });
    },

    // Off the scratch bytes themselves — the Project-scope door onto an Upload (ADR-0023). Distinct
    // from `removeFromConversation`, which only drops the chip and leaves the file for Build to
    // still cross; this destroys it, so it asks first like the removal above, and unlike that one
    // never needs to name a Built App that refuses: a scratch file is Chat-only by definition and no
    // app can be bound to it yet.
    async deleteScratchFile(resource) {
      return new Promise((resolve) => {
        antd.Modal.confirm({
          title: `Delete ${resource.name}?`,
          content: 'This deletes the file, and there is no undo.',
          okText: 'Delete',
          okButtonProps: { danger: true },
          onOk: async () => {
            try {
              await SW.api.deleteScratchFile(resource.path);
            } catch (err) {
              antd.message.error(err.message);
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
            await refreshWorkingSet();
            antd.message.info(`${resource.name} is deleted.`);
            resolve(true);
          },
          onCancel: () => resolve(false),
        });
      });
    },

    // Into the selected Built App -----------------------------------------
    //
    // The act ADR-0011 left unhung: it wrote the door out of a Binding and the door in stayed shut,
    // so a Resource added to the Project after a plan crossed from Chat could never reach the app
    // (#127). It sat beside the removals in the Resource Browser until #144, on ADR-0011's reason —
    // the list that owns the scope owns the act. That reason turned out to argue the other way:
    // the panel owns the working set and never owned the app scope, so the act is on the Built App's
    // own surface now and the panel keeps the removals it points at (ADR-0021).
    //
    // No confirm and no generation guard, unlike the removal below, and both absences are the
    // point. Binding is additive and its undo is the `Remove from {app}` the receipt names; and that
    // guard exists because a MODAL can sit open while the selection moves. With no modal the window
    // is one request round trip, which is the window every other act on these surfaces already has.
    //
    // One argument, and no Scope in it. #129 took a cascade position here, because a Data Source's
    // Scope was the position the creator was standing on and the door stood in the cascade. #142
    // split that into two acts — a Binding is recorded first and scoped afterwards, by
    // `openScopePick` below — so no door has had a position to send since, and the parameter that
    // took one went with the last caller for it.
    //
    // The door itself, open and shut. It is controlled from the store rather than left to antd
    // because a second surface asks for it: the refusal card's credential repair points here now
    // (#143), and a control that only ever opened under its own pointer could not be pointed at.
    openAddToApp() {
      state.addToAppOpen = true;
      notify();
    },

    closeAddToApp() {
      state.addToAppOpen = false;
      notify();
    },

    async bindToApp(resource) {
      // The BARE id, off `bindingKey`. Deriving it from `resource.id` would send the prefixed
      // `llm_alias:al_1`, which resolves to no Alias, answers 404, and leaves the rail redrawing
      // unchanged — a failure shaped exactly like success.
      const key = resource.bindingKey;
      if (!key || key.length < 2) return false;
      const where = appScopeName();
      const name = resource.name || key[1];
      const gen = appGen;
      let result;
      try {
        result = await SW.api.bind(key[0], key[1]);
      } catch (err) {
        antd.message.error(`${name} couldn't be added to ${where} — ${err.message}`);
        return false;
      }
      // The route answers with the list it just wrote, so nothing is re-read — and the ticket is
      // taken against `gen` for the removal's reason: a `/bindings` read that started before this
      // and lands after it would take the new Binding back off the screen.
      applyAppScope(appScopeTicket(gen), { bindings: result.bindings || [] });
      notify();
      // The receipt ADR-0021 asks of every act that adds: what it did, the scope it wrote, and the
      // way back out. It stands in for the confirm the ADR deliberately refused — separation carries
      // the weight, and a repeat user pays for a confirm on every repetition — so it is said AFTER
      // rather than asked before, which is what "Stop using here" already does on the cheap side.
      // Here rather than at any one door, because the header's picker and the refusal card's repair
      // both bind through this act, and a sentence written at one of them is a sentence the other
      // does not say.
      //
      // "App dependencies" and not the panel: this is a POINTER, and ADR-0011 fixed the shape a
      // pointer takes — it names the destination in the words the reader will actually see on the
      // way to it. Those words are the header menu item's (`modes/builder.js`), which is the thing
      // that gets clicked. It said "Project resources, under {app}" while the panel held a section
      // per app; the panel is the Project's one list now (ADR-0035) and the app's own records — with
      // the removal that acts on them — are behind that menu item.
      // A Data Source arrives with no Scope, which is a named unfinished state and not an error
      // (#142) — so the receipt names the second act rather than leaving it to be found. No other
      // kind has a part to choose, so no other kind is told to choose one.
      const scopeHint = SW.util.recordsScope(key[0])
        ? ' Choose the table beside its name to say which part of it the app reads.'
        : '';
      antd.message.success(
        `${where} now uses ${name}.${scopeHint} Remove it under App dependencies.`
      );
      // Binding records use, and membership is the record of use (ADR-0018), so the server has
      // just put this Resource in the project. The rail is built from a read this act does not
      // repeat, so a Resource bound from outside it — a catalogue row, a Chat handoff — would stay
      // off the list until the next scope load. Asked of the index rather than the row, because
      // the index IS the working set and a row is whatever the door that drew it chose to carry.
      // Unconditionally, for a Resource the rail already holds too. That skip was written when
      // membership was all this read carried, and it is the reason a bind was as silent as a
      // delete was: `usedBy` is a Project-wide answer, this act is what changes it, and the rail
      // is the only place that says so. The same re-read `removeBindingFromApp` takes on the way
      // out (#161), now on both sides of the same count.
      await refreshWorkingSet();
      return true;
    },

    // Choosing which part of a bound Data Source the app reads --------------
    //
    // The second of the two acts ADR-0021 split the bind into (#142). The bind above records the
    // dependency and sends no position inside the Resource, because a picker row has none to give.
    // This answers the narrower question afterwards, against a Binding that already exists — and
    // answers it again whenever the choice moves, which is what makes a Scope editable at all.
    //
    // It walks the same ladder the Resource Browser's cascade walks and stops wherever the person
    // stops: a database alone is an answer, and so is a database and a schema. What it no longer
    // does is stand in front of the bind, which is the whole of what #129 got wrong.

    // Open the door on one Binding, at the top of its ladder.
    //
    // At the top rather than at the Scope already recorded, because this control MOVES a choice as
    // often as it makes one: a walk that resumed under the current answer would offer the levels
    // below it and no way back up out of it.
    //
    // The ladder comes off the Project's own row for the Resource, which is where the server puts
    // it — a store with no database level opens on its schemas, and one Domino pins a database for
    // opens a rung further down still. With no row there is nothing to enumerate, and the door says
    // so rather than drawing an empty ladder.
    openScopePick(binding) {
      if (!binding || !binding.id) return Promise.resolve(false);
      const row = state.resourceIndex[SW.util.bindingId(binding)] || {};
      state.scopePick = {
        binding,
        id: binding.id,
        name: binding.display_name || binding.name || binding.id,
        levels: row.levels || [],
        // Seeded from what Domino already answered, the way the cascade seeds itself: a source with
        // a pinned database opens with that level filled in and the person choosing the next one.
        database: row.default_database || '',
        schema: row.default_schema || '',
        items: null,
        error: null,
        // What the Binding records RIGHT NOW, so the door can offer the way back to no Scope at
        // all. Read at open rather than from the walk, because the walk is about to move.
        recorded: SW.util.scopeText(binding),
      };
      notify();
      return loadScopeLevel().then(() => true);
    },

    closeScopePick() {
      // A listing still in flight is now answering about a door that is shut, so it loses its turn
      // rather than reopening one.
      scopePickLoad += 1;
      state.scopePick = null;
      notify();
      return true;
    },

    // One rung down. WHICH level the name answers is a question about where the person is standing,
    // not about the name — so the ladder is asked, and the table stage has nothing below it, which
    // makes naming a table there the answer itself rather than another step.
    scopePickStep(name) {
      const pick = state.scopePick;
      if (!pick || !name) return Promise.resolve(false);
      const stage = SW.util.cascadeStage(pick.levels, pick.database, pick.schema);
      if (stage === 'table') {
        return store.saveScope({ database: pick.database, schema: pick.schema, table: name });
      }
      state.scopePick = {
        ...pick,
        ...(stage === 'database' ? { database: name } : { schema: name }),
        items: null,
        error: null,
      };
      notify();
      return loadScopeLevel().then(() => true);
    },

    // Back to the top, which is the only way out of a level already answered. A ladder that could
    // only be climbed downwards would make the first rung permanent for as long as the door is
    // open, and the door exists to let a choice move.
    scopePickReset() {
      return state.scopePick
        ? store.openScopePick(state.scopePick.binding)
        : Promise.resolve(false);
    },

    // Write the Scope. The levels are whatever the walk has answered, and an unanswered one is sent
    // empty rather than left out — the route flattens "" to "not chosen", and a body that omitted
    // the level would be asking the route to guess which of the two it meant.
    async saveScope(scope) {
      const pick = state.scopePick;
      if (!pick) return false;
      const where = appScopeName();
      const gen = appGen;
      const shown = SW.util.scopeShown(scope);
      let result;
      try {
        result = await SW.api.scopeBinding(pick.id, {
          database: scope.database || '',
          schema: scope.schema || '',
          table: scope.table || '',
        });
      } catch (err) {
        // Left open on a refusal, unlike the success below: the walk that got here is the work, and
        // shutting the door would make the person do it again to find out what went wrong.
        antd.message.error(`The table for ${pick.name} couldn't be set in ${where} — ${err.message}`);
        return false;
      }
      store.closeScopePick();
      // The act's own ticket, taken against `gen` for `bindToApp`'s reason: the route answers with
      // the list it has just written, and a `/bindings` read that started before this and lands
      // after it would put the old Scope back on the screen.
      applyAppScope(appScopeTicket(gen), { bindings: result.bindings || [] });
      notify();
      // The receipt, in the shape ADR-0021 asks of every act that adds: what it did, and the way
      // back. The way back here is the same control, because moving a Scope and setting one are the
      // same act — which is the difference between this and a Binding, whose undo is a Remove
      // somewhere else entirely.
      antd.message.success(shown
        ? `${where} now reads ${shown} from ${pick.name}.`
        // The Scope cleared rather than moved. Named as the state it leaves behind, in the words
        // the record itself is drawn with, so the sentence and the screen agree.
        : `The table for ${pick.name} is ${SW.util.NO_SCOPE_YET} in ${where}. `
          + 'Choose one from the same control.');
      // A Scope is half of what `usedBy` records — each app's name AND the part of the Resource it
      // reads — and the drawer prints both. The Binding list read above is the APP's answer and
      // says nothing about the Project's, the same split that left a delete and a bind silent
      // (#161). After the receipt, because the sentence is the act's and this is bookkeeping.
      await refreshWorkingSet();
      return true;
    },

    // A folder of Dataset files, into the selected Built App ---------------
    //
    // The one act in the Dataset tree that is not a single file, which is why it is the one that
    // confirms (ADR-0029). The numbers are not politeness: the total attach budget is what decides
    // whether the act can succeed at all, so what the folder weighs and what the app already
    // carries have to be on screen BEFORE the click rather than inside the refusal after it.
    //
    // Every number in the question comes from the row that asked it, never from a second read. The
    // row's count and size are the listing's own, and the server measures the same subtree again
    // before it links anything — so a stale tree costs a refusal that names real numbers, never a
    // partial attach.
    //
    // The app is captured where the question is asked and checked again before the act, exactly as
    // `removeBindingFromApp` does it: a modal can sit open for as long as somebody leaves it there,
    // and the title is a promise about which app gains the files.
    async attachFolderToApp({ datasetId, label, folder, files, bytes }) {
      const asked = state.activeApp;
      if (!asked || !datasetId || !files) return false;
      const where = asked.name;
      return new Promise((resolve) => {
        antd.Modal.confirm({
          title: `Attach ${SW.util.number(files)} ${files === 1 ? 'file' : 'files'}`
            + ` (${SW.util.bytes(bytes)}) to ${where}?`,
          // What the act commits to, in one sentence. Not "there is no undo" — there is one, and
          // naming it is what keeps this confirm from reading as a warning about a cheap act.
          content: `${where} will include every file in ${label} when you publish. You can remove `
            + 'files later.',
          okText: `Attach folder to ${where}`,
          onOk: async () => {
            let result;
            if (!state.activeApp || state.activeApp.id !== asked.id) {
              antd.message.warning(
                `Nothing was attached. The selected app changed to ${appScopeName()} while this `
                + `was open, and this attach named ${where}.`
              );
              resolve(false);
              return;
            }
            try {
              result = await SW.api.attachDatasetFolder(datasetId, folder);
            } catch (err) {
              // The server's own sentence, which is the only one that can name the three numbers a
              // cap refusal turns on. Retold here it would be a second, vaguer copy.
              antd.message.error(`${label} couldn't be attached to ${where} — ${err.message}`);
              resolve(false);
              return;
            }
            // The whole scope, because an attach moves two lists at once: the app's files and the
            // Build header's account of what it ships. Same refresh the crossing makes.
            await loadScopeData();
            // The receipt names what the act ADDED, which is not always what the row counted: a
            // file already in the app is passed over rather than attached twice, and a receipt
            // claiming otherwise would be a count nobody could reconcile with the list.
            antd.message.success(result.attached
              ? `${SW.util.number(result.attached)} ${result.attached === 1 ? 'file' : 'files'} `
                + `from ${label} ${result.attached === 1 ? 'is' : 'are'} in ${where}. `
                + "Remove one from the app's own list."
              : `${where} already carries every file in ${label}.`);
            resolve(true);
          },
          onCancel: () => resolve(false),
        });
      });
    },

    // The same folder, back out of the selected Built App -------------------
    //
    // The mirror of the attach above, and it inherits the removal vocabulary rather than inventing
    // a second one: the label names the app it acts on, because that is the only thing telling the
    // three removal scopes apart (ADR-0011).
    //
    // It names a COUNT and no size. The attach half shows both because the cap is what its numbers
    // are for — it is the thing that decides whether the act can succeed — and no cap decides a
    // removal. A size here would be decoration, and this row is offered over files the app carries
    // rather than over a listing, so it has no size of its own to quote anyway.
    //
    // The app is captured where the question is asked and checked again before the act, exactly as
    // the attach does it: a modal can sit open for as long as somebody leaves it there.
    // Resolves `true` when the folder came out, `'stale'` when a request was sent and failed —
    // which can still have moved files, so the caller re-reads — and `false` when nothing was
    // asked of the server at all.
    async removeFolderFromApp({ datasetId, label, folder, files }) {
      const asked = state.activeApp;
      if (!asked || !datasetId || !files) return false;
      const where = asked.name;
      return new Promise((resolve) => {
        antd.Modal.confirm({
          title: `Remove ${SW.util.number(files)} ${files === 1 ? 'file' : 'files'} from ${where}?`,
          // What is taken and what is kept, in one sentence. The Dataset's own bytes are never
          // Sage's to remove, and the folder can be attached again from the same tree — so this
          // says what it costs rather than warning about an act that is cheap to undo.
          content: SW.brand.text(
            `${where} stops carrying them and stops shipping them when you publish it. The files `
            + 'stay in the {dataset}, and the folder can be attached again from here.'
          ),
          okText: `Remove folder from ${where}`,
          okButtonProps: { danger: true },
          onOk: async () => {
            if (!state.activeApp || state.activeApp.id !== asked.id) {
              antd.message.warning(
                `Nothing was removed. The selected app changed to ${appScopeName()} while this was `
                + `open, and this removal named ${where}.`
              );
              resolve(false);
              return;
            }
            let result;
            try {
              result = await SW.api.detachDatasetFolder(datasetId, folder);
            } catch (err) {
              // The server's own sentence, because only it can name the files the app still uses.
              // Retold here it would be a second, vaguer copy of the one thing a person can act on.
              antd.message.error(`${label} couldn't be removed from ${where} — ${err.message}`);
              // A failure here is not always "nothing happened": a removal that stops part way
              // commits what it did, and the message points at the app's file list as the record
              // of what is left. That list has to be re-read for the message to be true. A refusal
              // moved nothing, so this only costs it a fetch.
              await loadScopeData();
              // `'stale'` rather than `false`, because the DATASET LISTING has to be re-read too:
              // its `attached` flags still say the part-removed files are carried, so their rows
              // would offer no way to put them back. Truthy, so the caller's re-read fires; not
              // `true`, so nobody reads this as the act having succeeded.
              resolve('stale');
              return;
            }
            // The whole scope, because a removal moves the same two lists an attach does: the app's
            // files and the Build header's account of what it ships.
            await loadScopeData();
            // No `appRemoval` notice: that one exists to report what the app's source STILL uses
            // after a removal went through, and here nothing can — source that still used a file is
            // what refuses this act outright rather than something to report afterwards.
            const leaked = result.removed_copies || [];
            const copies = leaked.length
              ? ` ${leaked.length === 1 ? 'A copy' : 'Copies'} left in ${leaked.join(', ')} `
                + `went with ${leaked.length === 1 ? 'it' : 'them'}.`
              : '';
            // What was left behind, because the server could not prove it was the data rather than
            // a file somebody wrote. Said out loud: the app stops covering these once its record
            // drops them, so they would otherwise turn up unannounced in the next save's diff.
            const left = result.kept_copies || [];
            const kept = left.length
              ? ` ${left.join(', ')} ${left.length === 1 ? 'shares a name with one of them and was'
                : 'share names with them and were'} left in place — check ${left.length === 1
                ? 'it is' : 'they are'} yours before saving.`
              : '';
            // The no-op branch its attach mirror has. The row's count can be stale against the
            // record — a build turn may have detached something since — and a receipt reading
            // "0 files are out" would be a success message about nothing happening.
            antd.message.success(result.detached
              ? `${SW.util.number(result.detached)} ${result.detached === 1 ? 'file' : 'files'} `
                + `from ${label} ${result.detached === 1 ? 'is' : 'are'} out of `
                + `${where}.${copies}${kept}`
              : `${where} was already carrying nothing from ${label}.`);
            resolve(true);
          },
          onCancel: () => resolve(false),
        });
      });
    },

    // Closing a gap a refused @mention named --------------------------------
    //
    // One act per reason a turn drops a mention, taking the row `_unusable_mentions` wrote (#135).
    // They live here rather than inside the card because the compose-time guard offers the same
    // fixes before send (#136), and two copies of "what does an unbound Alias need" would drift.
    //
    // None of them is a new way to record anything: each reaches the door that already exists for
    // its kind, from a second place. And none binds on its own — every one of these runs because a
    // person clicked it, which is what ADR-0010 asks of a Binding.

    // What a dropped @mention needs, one act per kind (#135, #136).
    //
    // Keyed on the Binding's own word for it — the three `sage/resources/bindings.py` names, plus
    // `file` for an attachment. Only the LLM Alias finishes in one click; the other two open the
    // door and stop, and that asymmetry was the point rather than a gap: a Data Source's Binding
    // carried a Scope chosen by standing somewhere in the cascade (#129), and a Model API's needs an
    // access token the server refuses to record a Binding without.
    //
    // Half of that reason is gone. A Data Source binds in one argument since #142, so its repair
    // could finish too — that is #143's, along with where both of these land.
    //
    // Each label names the app it acts on, because a Project holds many Built Apps (ADR-0008). The
    // token's does not: a token is stored per model and outlives any one Binding, so naming an app
    // there would claim something untrue about what the click buys.
    //
    // Here rather than inside the refusal card, where it started, because the compose-time warning
    // offers the same fixes before send (#136) and two copies of "what does an unbound Alias need"
    // would drift — the same reason the four acts below sit here.
    //
    // A row with no act, no identity or no app draws no button rather than a broken one. The server
    // already withholds a row it cannot offer a fix for — a workspace path that resolves to nothing
    // keeps the sentence and gets none — and this is the client half of the same rule, for a kind
    // added on the server before a door exists for it here.
    //
    // `activeAppId` is the refusal card's other half of the `live` rule, inside one session.
    // Switching Built App mid-build is allowed and the turn carries on (#77), so a card promising
    // "Use in Gong sentiment" can still be on screen after the rail has moved — while every act
    // behind it resolves whatever is selected NOW. Once the two disagree the card goes back to
    // being a record. The composer's warning is read off the selected app every render, so its
    // rows can never disagree; it passes the same id rather than keeping a second entry point.
    mentionFixes(entries, activeAppId, replay) {
      // The kinds whose bind takes a single argument, so a click on any of them finishes. A
      // Data Source joined the Alias here when #142 stopped deriving its Scope from a cascade
      // position: the bind records the dependency and nothing else now, and the Scope is a second
      // act on the app's own surface — which is where `bindToApp`'s receipt sends the reader.
      // One label for one act, in the words the header's own door already uses.
      //
      // A Dataset belongs with them and was missing until #212: #141 made `bind_dataset` a real
      // one-argument bind and put it on the route beside the Alias, and this map was not told. An
      // entry whose kind is absent here is DROPPED below, so the miss did not read as a missing
      // button — it read as a refusal with no fix at all, which is the exact five-step dead end
      // #135 exists to close. Adding a kind to the route means adding it here or taking that
      // dead end back.
      //
      // `sends` is which of them buys the whole request rather than half of it (#213). The three
      // binds and the attach write the record the mention was missing, so the turn the person
      // already asked for can follow the click. The Model API does not carry it: that act opens a
      // door and returns, the gap is still open afterwards, and a build behind it would walk into
      // the same refusal wearing a longer label.
      const bind = (e) => ({ label: `Use in ${e.app}`, act: () => store.bindFromMention(e),
                             sends: true });
      const MENTION_FIX = {
        llm_alias: bind,
        data_source: bind,
        dataset: bind,
        model_api: (e) => ({
          label: 'Add its access token',
          act: () => store.openCredentialForMention(e),
        }),
        file: (e) => ({ label: `Attach to ${e.app}`, act: () => store.attachFileForMention(e),
                        sends: true }),
      };
      // One click, two acts (#213). The record the mention needed, and then the request the person
      // already made, sent again against it — because closing the gap and then retyping the prompt
      // is one job, and the second half is what they were paying for. #183 and #185 settled this
      // shape for the candidate cards, and the difference worth naming is the plumbing: neither
      // surface here is answering a gate. The refusal's turn RAN — the agent was told the mention
      // was dropped (`_DROPPED_MENTION_NOTE`) and declined — so `replay` starts a fresh turn rather
      // than resuming a paused one, and carries no skip flags for gates this turn never reached.
      //
      // A caller with no `replay` gets the bind-only button it always had: the harness that pins
      // these acts, and a live card whose event predates the `prompt` field. A label promising a
      // build that nothing would start is the dead end #135 exists to close, one word longer.
      //
      // The record's own answer decides whether the build follows. `bindToApp` returns false when
      // the route refused the Binding, and the attach returns null the same way — a turn sent on
      // top of either would meet the refusal it was supposed to have cleared.
      const withReplay = (fix) => (!replay || !fix.sends ? fix : {
        ...fix,
        label: `${fix.label} and build`,
        act: () => Promise.resolve(fix.act()).then((ok) => (ok ? replay() : ok)),
      });
      // A row carrying `table` is the app's Scope falling short, not its Bindings — the Binding is
      // already recorded, and `bind` would rewrite it with the values it already holds and leave
      // the gap exactly where it was. So the kind does not decide this one; the field does.
      //
      // Off `MENTION_FIX` rather than a fifth entry in it, because that map is keyed on the Binding
      // kind and this is a second state of ONE of those kinds. Keyed in there, `data_source` would
      // have to mean two acts at once.
      const scope = (e) => ({ label: `Choose what ${e.app} reads`,
                              act: () => store.openScopeForMention(e) });
      return (entries || []).map((entry) => {
        const make = entry && (entry.table ? scope : MENTION_FIX[entry.kind]);
        if (!make || !entry.id || !entry.app) return null;
        if (!entry.appId || entry.appId !== activeAppId) return null;
        return { ...withReplay(make(entry)), key: `${entry.kind}:${entry.id}` };
      }).filter(Boolean);
    },

    // What a card with no buttons says instead (#213). One entry per kind the route can refuse, so
    // the sentence can never leave a named Resource with no way out at all.
    //
    // What it says is the prose the SERVER used to carry, moved rather than rewritten — so a
    // replayed card reads today exactly as it read before the buttons learned to build. That is why
    // `model_api` is sent to "Use in {app}" here while its live button offers the access token: the
    // token is the first half of that fix and the bind is the second (see `openCredentialForMention`
    // and #128), and a record is the wrong place to close a gap the live card has never closed. The
    // rule these follow is the server's sentence, not `MENTION_FIX`.
    //
    // The server's lines say only what happened, because a live refusal draws the way out and an
    // instruction beside a button describes the long way round past it. A replayed refusal draws no
    // buttons on purpose — an old card must not bind anything for somebody scrolling back (#135,
    // #209) — so the instruction it used to carry in the prose lives here, in the one branch that
    // knows there is nothing to click.
    //
    // No `activeAppId` check: this IS the record, and it names the app the record named. Named at
    // all because a Project holds many Built Apps (ADR-0008), so "the app" is the one word that
    // cannot say which of them the act would land in.
    mentionFixHints(entries) {
        const ships = (e) => `Add it to ${e.app}, then ask again.`;
      const HINT = {
        llm_alias: ships,
        data_source: ships,
        dataset: ships,
        model_api: ships,
        file: (e) => `Attach it to ${e.app}, then ask again.`,
      };
      // The Scope's own hint, chosen by the field rather than the kind for the reason `mentionFixes`
      // splits on the same one: the app HAS this Resource, so "Add it" would send the reader to a
      // door that has nothing left to write.
      const scoped = (e) => `Choose what ${e.app} reads under App dependencies, then ask again.`;
      const said = [];
      (entries || []).forEach((entry) => {
        const make = entry && (entry.table ? scoped : HINT[entry.kind]);
        if (!make || !entry.id || !entry.app) return;
        // One sentence per distinct instruction rather than per row: three unbound Resources on one
        // app are one thing to go and do, and saying it three times reads as three.
        const line = make(entry);
        if (said.indexOf(line) === -1) said.push(line);
      });
      return said;
    },

    // An LLM Alias or a Data Source, the two kinds whose bind takes a single argument, so a click
    // on either finishes. Reshaped into what `bindToApp` reads rather than calling `/bindings`
    // again: the app-name reporting, the generation ticket and the bare-id rule all belong to that
    // act and are not worth a second copy.
    //
    // Which surface the reader is left on is that act's answer and not this one's. `bindToApp`
    // writes the receipt, and for a Data Source the receipt names the second act — the Scope,
    // beside the record's own name in the Build header (#142, ADR-0021).
    bindFromMention(entry) {
      if (!entry || !entry.kind || !entry.id) return Promise.resolve(false);
      return store.bindToApp({
        // The prefixed id is what `resourceIndex` keys on: without it `bindToApp` cannot see the
        // Resource is already in the rail, and every bind from this door reloads the whole scope.
        id: SW.util.bindingId({ kind: entry.kind, id: entry.id }),
        name: entry.name || entry.id,
        bindingKey: [entry.kind, entry.id],
      });
    },

    // A Model API. Sage refuses to record one it holds no access token for, so the credential is the
    // first half of the fix and the bind is the second — offering the bind here would offer an act
    // the server is designed to turn down. The sentence names the Resource and not the app on
    // purpose: a token is stored per model and outlives any one Binding (see UNBIND_COPY), so it is
    // not a thing an app owns.
    //
    // So this one stays a signpost, and #143 moved what it points at. It opened the Resource
    // Browser because that is where the bind was; the bind is on the Built App's own surface now
    // (ADR-0021), so what stands open is the header's own door — beside a sentence saying what
    // that door will ask for before it will record anything.
    //
    // That door does list this kind, which is not the same as offering the bind here. #141 settled
    // it: a Model API row in the picker answers 409 with the server's own instruction, and putting
    // that sentence on screen unchanged is a door doing its job. What a card must not do is spend
    // its ONE click on it — the rule every button here is drawn under (#135).
    openCredentialForMention(entry) {
      if (!entry || !entry.id) return false;
      store.openAddToApp();
      // What the token is for and where it comes from, and no more than that. The form that takes
      // the paste is #128's — until it exists there is no box to point at, and a sentence promising
      // one would be the dead end this card removes, rebuilt. When it lands it hangs off this act,
      // which is the whole reason the act is named rather than inlined into the card.
      antd.message.info(SW.brand.text(
        '{assistantName} needs an access token for {name}. Copy the sample '
        + 'request from its Overview page in {platformName}.',
        { name: entry.name || entry.id }
      ));
      return true;
    },

    // A table the app's Scope does not reach. The Binding is already on disk — a bind here would
    // rewrite the record with the values it already holds — so what is missing is the second act,
    // and this opens the door that owns it (`openScopePick`, #142) on that Binding.
    //
    // It opens and stops, like the Model API's above and for the same reason: widening a Scope is a
    // choice with a shape — schema, or this table instead of the bound one — and a card must not
    // spend its one click guessing which. The app's screens read the bound table, and a button that
    // silently moved the Scope off it would break them to satisfy one sentence in a prompt.
    //
    // Resolved out of `state.bindings` rather than sent: the row names a Binding, and the ladder
    // this opens walks off the Project's row for the Resource anyway.
    openScopeForMention(entry) {
      if (!entry || !entry.id) return false;
      const binding = (state.bindings || []).find(
        (b) => b.kind === entry.kind && b.id === entry.id
      );
      if (!binding) return false;
      store.openScopePick(binding);
      return true;
    },

    // A Chat file. It lives at the Project root, outside every app, so the fix is to put the bytes
    // on a Dataset — which attaches them under public/data/ for the selected app in the same act
    // (see `upload_file`). No Dataset is named: a refusal offers ONE click, and the server's own
    // default target is the answer the upload box already gives when nobody picks. Choosing a
    // particular one stays in the panel's menu, where the list is on screen.
    attachFileForMention(entry) {
      const path = String((entry && entry.id) || '');
      if (!path) return Promise.resolve(null);
      const name = entry.name || path.split('/').pop();
      return store.addScratchToDataset({ id: `file:${path}`, name, path }, '', { quiet: true })
        .then((res) => {
          if (res) antd.message.success(`${name} is attached to ${entry.app}`);
          return res;
        });
    },

    // What a Build turn would drop if this text went now (#136) ------------
    //
    // The compose-time half of `_unusable_mentions`: the same two questions, asked of the same two
    // lists, before the send instead of after it. Nothing is fetched — `bindings` and
    // `appAttachments` are the selected app's own records, already read per app (#101) — and the
    // mentions come out of `collectTurnRefs`, the very function that decides what the send will
    // carry. Reading anything else would let the warning and the turn disagree about one prompt.
    //
    // Rows come out in `_unusable_mentions`'s shape, so one `mentionFixes` draws both the warning
    // and the refusal that follows it if the person sends anyway. And like the server's, only a
    // drop somebody can close in ONE act gets a row: a Chat file can be promoted onto a Dataset,
    // an unbound Resource can be bound, and a workspace path that resolves to nothing has no act
    // to offer — that one keeps the refusal it has always had.
    //
    // Nothing here binds. It reads two lists and returns rows; every fix is a click (ADR-0010).
    unusableMentions(text) {
      const app = state.activeApp;
      if (!app || !String(text || '').trim()) return [];
      const refs = collectTurnRefs(text);
      const bound = new Set((state.bindings || []).map((b) => SW.util.bindingId(b)));
      // The app's own files, keyed by basename. A mention token IS a basename — `mentionToken`
      // prefers the file's — so "does the app already hold what this names" is a basename question,
      // and asking it by full path answers "no" every time: an attachment is always under
      // `public/data/` (`_attach_dest`) and a Chat upload always under `.sage/scratch/`, so the two
      // sets never meet. That matters when a promote leaves the original standing —
      // `promote_scratch_to_dataset` swallows a failed unlink — because the file HAS reached the
      // app, and a warning offering to send it again would be wrong twice over.
      const attached = new Set(
        (state.appAttachments || []).map((a) => String(a.path || '').split('/').pop())
      );
      const entries = [];
      // Chat's uploads first, the order the refusal reports them in. A path anywhere else that the
      // app does not hold is the case with no act, so it is passed over here and left to the
      // server's sentence.
      refs.mentions.forEach((path) => {
        const name = path.split('/').pop();
        // Only a Chat upload carries a one-click fix: it lives at the Project root, outside every
        // app, and promoting it onto a Dataset attaches it in the same act. Any other path the app
        // does not hold keeps the refusal it has always had, because there is no act to offer it.
        if (attached.has(name) || !path.startsWith(SCRATCH_PREFIX)) return;
        entries.push({ kind: 'file', id: path, name, app: app.name, appId: app.id });
      });
      // One row per Resource rather than per mention: "@Warehouse and @FCT_USAGE_DAILY" names one
      // Data Source at one table, and two identical buttons would offer the same bind twice.
      const seen = new Set();
      // The Scope each bound Resource records, for the second question below. A Binding carries its
      // levels (`_labelled_bindings` hands the manifest entry straight through), so the app's own
      // answer is already here and nothing is fetched — the rule this warning lives by.
      const scopeOf = {};
      (state.bindings || []).forEach((b) => { scopeOf[SW.util.bindingId(b)] = b; });
      refs.resources.forEach((ref) => {
        const key = `${ref.kind}:${ref.id}`;
        if (seen.has(key)) return;
        if (bound.has(key)) {
          // Bound, and still not what the mention named. The @ menu offers the tables pinned on the
          // PROJECT's row while a turn honors the ones inside this app's Scope, and a sibling of the
          // bound table passes the test above and is dropped by the turn without a word.
          //
          // Asked of the Binding's own levels and not of a schema this surface has never read: that
          // is the half of the server's question a browser can answer, and it is the half that
          // matters. Two states of it, and the second is the ordinary one — the header's picker
          // binds a Data Source in one argument and leaves the Scope as a second act (#142), so a
          // store bound there reaches NO table until somebody answers it:
          //
          //   - a Scope narrowed to one table, and the mention names a sibling;
          //   - no Scope at all, so it names nothing.
          //
          // A Scope that stopped at a database or a schema holds every table under it, so it stays
          // quiet here and is left to the turn — the right way round for a warning that must never
          // fire on a mention that worked.
          const at = scopeOf[key];
          if (!ref.table || !at) return;
          const scoped = SW.util.scopeText(at);
          if (scoped && (!at.table || at.table === ref.table)) return;
          seen.add(key);
          entries.push({ kind: ref.kind, id: ref.id, name: ref.table, table: ref.table,
                         source: ref.sourceName || at.display_name || at.name,
                         scope: SW.util.scopeShown(at), app: app.name, appId: app.id });
          return;
        }
        seen.add(key);
        entries.push({ kind: ref.kind, id: ref.id, name: ref.name || ref.id,
                       app: app.name, appId: app.id });
      });
      return entries;
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
              antd.message.error(`${name} couldn't be removed — ${err.message}`);
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
            // `usedBy` is the Project row's, computed by `list_project_resources` off the apps' own
            // manifests — and this act just changed one of them. The read above answers with the
            // APP's list and says nothing about the Project's, so without this the rail would go on
            // naming an app that no longer binds anything. Since #161 that is not only a wrong
            // subtitle: a row whose Resource is gone from Domino offers the app's door INSTEAD of
            // the Project's, so a stale `usedBy` leaves the row pointing at an app it has already
            // left, with no way out short of a reload.
            //
            // Membership only, which is a 145 ms local read — the platform listing an unbind cannot
            // change is not re-read (#162).
            await refreshWorkingSet();
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
        antd.message.error(`${name} couldn't be removed — ${err.message}`);
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
      // What could not be proven to be the data, so was left where it is. Said out loud because the
      // app stops covering it the moment its record drops the file, and the bytes would otherwise
      // turn up in a save nobody expected them in.
      const left = result.kept_copies || [];
      const kept = left.length
        ? ` ${left.join(', ')} shares its name and was left in place — check it is yours before `
          + 'saving.'
        : '';
      // The route hands back no manifest, so the list is the one on screen minus what just went —
      // filtered HERE, off whatever the newest read left, and installed under the act's own ticket
      // so a `/project` read that started before the detach cannot put the file back (#101).
      applyAppScope(appScopeTicket(gen), {
        appAttachments: (state.appAttachments || []).filter((a) => a.path !== attachment.path),
        appRemoval: removalNotice(where, name, result.refs || [], `${source}${copies}${kept}`),
      });
      notify();
      return true;
    },

    // Off the Dataset bytes an Upload wrote — the Dataset-scope door (ADR-0023). Distinct from
    // `removeAttachmentFromApp` above, which only drops the app's symlink and keeps the data: this
    // destroys it, so — unlike that one — it confirms first and carries the same race guard as
    // `removeBindingFromApp`, since the window a confirm leaves open is exactly the length that
    // guard answers. The menu only ever offers this on a Sage-managed upload (`isSageUpload`);
    // a genuine pre-existing Dataset file has no door here to delete it.
    async deleteAttachmentFromApp(attachment) {
      const asked = state.activeApp;
      const where = appScopeName();
      const name = (attachment.file || attachment.path || '').split('/').pop();
      return new Promise((resolve) => {
        antd.Modal.confirm({
          title: `Delete ${name} from ${attachment.dataset}?`,
          content: 'This deletes the file, and there is no undo.',
          okText: 'Delete',
          okButtonProps: { danger: true },
          onOk: async () => {
            const gen = appGen;
            if (!asked || !state.activeApp || state.activeApp.id !== asked.id) {
              antd.message.warning(
                `Nothing was deleted. The selected app changed to ${appScopeName()} while this was `
                + `open, and this deletion named ${asked ? asked.name : 'another app'}.`
              );
              resolve(false);
              return;
            }
            try {
              await SW.api.deleteFile(attachment.path);
            } catch (err) {
              antd.message.error(err.message);
              resolve(false);
              return;
            }
            applyAppScope(appScopeTicket(gen), {
              appAttachments: (state.appAttachments || []).filter((a) => a.path !== attachment.path),
              appRemoval: removalNotice(where, name, [], "The file's data is gone too."),
            });
            notify();
            resolve(true);
          },
          onCancel: () => resolve(false),
        });
      });
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
      if (!conversationId()) await store.newThread();
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
        await refreshWorkingSet();
        if (!options.silent) {
          antd.message.success(`${attachment.resourceName} is now in ${state.scope.name}.`);
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
          : `${attachment.resourceName} is out of this conversation — still in ${state.scope.name}.`
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
        // A receipt, not a turn. `system` is what draws it: `SW.Message` renders that role as a
        // bare line with no avatar and no "who" row, which is what this is — the model has said
        // nothing yet. As `assistant` it read as something Sage had answered, and it promised the
        // behaviour of a turn that had not run. It also keeps `lastUserText` reading past it, which
        // is right: this line is not the answer to whatever the reader last asked.
        pushMessage({
          id: `ack_${Date.now()}`,
          role: 'system',
          at: new Date().toISOString(),
          blocks: [
            {
              type: 'text',
              value: `**${attachment.resourceName}** is now in this conversation.`,
            },
          ],
        });
      }
    },

    // Chat ---------------------------------------------------------------

    // No arguments, and none to give: the server mints the Thread untitled and names it from the
    // first thing typed into it, in Chat and in Build alike (see `_name_conversation`). This used
    // to pass a scope, a title and an app id that `SW.api.createThread` has never taken and the
    // route has never read — three dead arguments that read like the title was being asked for
    // here and ignored somewhere else.
    async newThread() {
      const thread = await SW.api.createThread();
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
      // And the Rail that was opened to draw that placeholder closes with it. `expandRail` opens
      // it for one reason — the pending row is the only thing on screen saying the press worked —
      // and the `pendingConversation` clear above is that reason running out. Without this the
      // open had no end at all: only a row click undid it, and somebody who STARTED a conversation
      // never makes one, so a panel nobody chose stayed open for the rest of the session.
      //
      // Guarded, and that is what the flag is for. A row click may close a Rail somebody opened by
      // hand, because clicking a row ANSWERS the Rail — it asks which conversation you are looking
      // at. Typing the first message answers nothing it asked, so a Rail that was chosen survives.
      //
      // After the `notify` and not beside the clear it belongs to, because `collapseRail` notifies:
      // called up there it would publish this view half-written — the new Thread already set, the
      // conversation's messages not yet cleared — and draw the abandoned transcript under it.
      if (railAutoExpanded) store.collapseRail();
      // The advertised way out of a session lock is this button (ADR-0043), so it has to actually
      // take the lock off the screen. Dropped BEFORE the read rather than left to it: the read's
      // error handler leaves the last answer standing, so one failed request would draw the
      // abandoned conversation's lock — and its sentence — over a brand-new empty one.
      dropSessionLock();
      refreshSensitivity();
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
      // ADR-0009: the Conversation's newest BOUND handoff entry names the app. The rail's own list
      // carries that answer already — the server reduces the handoff record to `boundAppId` — so
      // the common path costs nothing and the reads below are the correction (#139).
      const listed = (state.threads || []).find((t) => t.id === threadId);
      if (listed && listed.boundAppId) return listed.boundAppId;
      // Only a confirmed handoff writes `appId`, so its presence is what "bound" means here. The
      // thread is usually the one already open, and reusing it is what keeps this off the network.
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
      // Half the lock is this Conversation's own (ADR-0043), so the answer standing on screen
      // belongs to the one that was open before. Unawaited beside the attachments read below: the
      // view is already painted, and a lock arriving a beat later is the same deferral a scope load
      // makes. It carries no `gen` because it does not need this one — it holds the Conversation it
      // asked about and drops its own answer if that has moved on.
      //
      // The drop comes first for the reason it does in `newThread`: a read that fails or is
      // superseded leaves the last answer standing, and that answer is the previous Conversation's.
      dropSessionLock();
      refreshSensitivity();
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
      // "Start a new chat" is the way out the copy gives, and a lock still drawn over an empty
      // screen makes the one instruction it gives look like it did nothing. Dropped rather than
      // re-read because this reset is synchronous and reaches no network — every other caller of
      // it depends on that.
      dropSessionLock();
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
      // A new Conversation has touched no app, and the Rail's app filter hides every row that has
      // not touched the app it names — the pending row included, which is why that row is drawn
      // only when no filter is set. So a filter left standing hides the Conversation somebody just
      // started, and the Rail says no conversation has changed that app yet.
      //
      // Here rather than beside a caller, because all three doors that start one mean the same
      // thing — the Rail's two heads and the command palette — and only one of them was clearing
      // it, through the `collapseRail` that happens to follow it.
      state.railAppFilter = null;
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
        // The Rail gets out of the way, for the reason it does when you click one of its rows
        // (#150): a New app puts a new app in the preview, and the preview is the thing 260px of
        // Rail is taking from. Here rather than beside the two doors that call this — the app
        // picker's own button and the empty state's — because both mean the same act, which is the
        // argument `newConversation` makes about its three.
        //
        // It reaches the Rail at all because nothing else was ending an auto-expand. `expandRail`
        // does not write the preference and is not meant to last, but only a row click ever undid
        // it — so a Conversation started from Chat's collapsed head left the Rail open across the
        // mode switch and across this, which is the one place in Build that can least afford it.
        //
        // `collapseRail`, not `toggleRail`: someone who opened the Rail by hand keeps that choice
        // for their next load. Same rule, same reason, as every other auto-collapse here.
        store.collapseRail();
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
      const out = await SW.api.patchApp(id, { name });
      // The conversation rail names this app too — a tag on every conversation that changed it —
      // and the server has just relabelled those. Read the rail back with the app list, or the
      // chips go on saying the old name until something else happens to reload it.
      //
      // The panel is the third place the name lands: `usedBy` carries it, so the drawer and the
      // `Remove from {app}` door both print it. Same sentence as the chips, one list further out.
      // The published App is the fourth place, and the only one that can refuse (#219). Handed
      // back rather than swallowed: the rename here succeeded, so this cannot be thrown, and a
      // half-rename nobody is told about is the exact divergence the PATCH exists to close.
      //
      // Which is why the reads above cannot take it down with them. The server has already written
      // the rename; a failed re-read leaves a stale list on screen, and a rejection here would
      // additionally swallow the one sentence saying the published App kept its old name.
      try {
        await Promise.all([loadAppList(), loadThreadList(), refreshWorkingSet()]);
      } catch (err) {
        console.warn('[store] renameApp: the rename landed, a reload after it did not', err);
      }
      return out;
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
      // The app's Bindings went with its directory, and `usedBy` on every Project row is computed
      // off the apps' own manifests — so without this the rail goes on saying `Used by 1 app`
      // about an app nobody can open any more. `loadBuild` cannot cover it: it reads the app that
      // is left, and this act changed the answer to a question about all of them. Same re-read
      // `removeBindingFromApp` takes after an unbind, for the same reason (#161).
      await refreshWorkingSet();
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
    //
    // `name` is the second thing the confirm hands in, and the only one that reaches the request:
    // what the person accepted in its name field (#218). The server writes it before it deploys, so
    // the re-read below is also what takes the new name off the row.
    async publishApp(asked, name = '') {
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
        return await SW.api.publish(name);
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
    async sendBuildPrompt(text, { skipResetGate = false, skipIncomingGate = false,
                                  skipTableGate = false, skipSourceGate = false,
                                  chosenSource = '', sourceName = '', tableName = '',
                                  skipDatasetGate = false, datasetDismissed = '',
                                  datasetPick = '' } = {}) {
      if (!text.trim()) return null;
      if (!state.thread) await store.newThread();
      state.buildTurnMode = state.buildMode;
      // Echo what the server will write to the transcript, so live and reloaded read the same. For a
      // click that is the click, not the request — the request is already a bubble above the offer,
      // and repeating it would say the user asked twice (see build_stream's `user_text`).
      // A Data Source pick is a click too, but not that one: what it answered was which store,
      // so the bubble says which store. A table pick answers the same question one level down and
      // says which table (#208) — `Build it.` there both threw the choice away and put a sentence
      // nobody typed in their own voice. Written the same both ends (see _picked_source_text and
      // _picked_table_text): this bubble is the only one drawn live, because the `user` event the
      // server writes to the transcript is never streamed back, so a reload must read the same.
      // A Dataset file or folder pick is the third of them, and it answers "which data" one card
      // further down again (#196). The row it names is passed rather than looked up, for the reason
      // written at `_picked_dataset_text`: a folder attach writes an entry per file and none of
      // them is the folder. The way past the card sets none of these three and still says
      // `Build it.`, which is accurate — it attached nothing to name.
      const picked = sourceName || tableName || datasetPick;
      const bubble = picked ? `Use ${picked}.`
        : ((skipResetGate || skipIncomingGate || skipTableGate || skipSourceGate
            || skipDatasetGate)
          ? 'Build it.' : text);
      // The app this turn is for. Switching Built App mid-build is allowed and the build carries on
      // server-side (#77), so the events below have to be checked against this before they are
      // appended — otherwise one app's build writes itself into another app's transcript. The
      // rail's Building mark is what reports the turn once the person has moved on.
      const turnApp = state.activeApp && state.activeApp.id;
      const movedOn = () => state.activeApp && state.activeApp.id !== turnApp;
      // And the conversation this turn belongs to, captured for the same reason: opening another
      // one mid-build is allowed, and the turn stays the turn it was when it was sent.
      const turnThread = state.thread.id;
      // The queue's two ends of this send: `ticket` is what a Cancel would name, and `unran` is
      // whether the turn ended without ever running — which is the only case where the bubble
      // drawn just below has to come back off the screen again.
      let ticket = '';
      let unran = false;
      // This tab's own name for the turn, once it is actually running. Held so the `finally` can
      // take back exactly what it put there and nothing else.
      let claim = null;
      // Whatever card this turn is the answer to has now been answered (#209). Every button on
      // every one of them ends here, so this is where they stop being clickable — the reload each
      // of those callers does just above is what used to retire them, and it cannot any more.
      forgetLiveCards();
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
            prompt: text, conversation: state.thread.id,
            skipResetGate, skipIncomingGate, skipTableGate, skipSourceGate, chosenSource,
            skipDatasetGate, datasetDismissed, datasetPick,
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
          // Past the queue and past the two ways a turn ends without running: this turn holds the
          // lock, so name it, and the Stop bar has something to match against from the first frame.
          if (!unran && !claim) {
            claim = claimRunningTurn('build', turnThread, turnApp);
          }
          // Once the rail has moved on, these events describe an app that is no longer on screen.
          if (movedOn()) return;
          applyBuildEvent(ev);
          notify();
        });
        if (stopped) await store.loadBuild({ keepPreview: true });
      } catch (err) {
        applyBuildEvent({ type: 'error', message: String(err.message || err) });
        // A turn that never opened a stream is still a failed turn, and `readSSE` saw no frame to
        // notice it by. This is where a refused POST lands — including `_turn_slot_refusal`, which
        // refuses on exactly the dead model slot the chip is about (ADR-0027).
        store.refreshProblems();
      } finally {
        liveBuildTurns -= 1;
        dropQueuedTurn(ticket);
        // Not `false`: this tab can have several turns alive at once now, and the first one to
        // unwind used to clear a flag the others were still relying on. Any of them still here
        // means a turn is running in this project — its own, or the one it is queued behind.
        state.buildRunning = liveBuildTurns > 0;
        state.buildTyping = state.buildRunning ? state.buildTyping : null;
        releaseRunningTurn(claim);
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
      // "Just reset" is the one answer to a card that starts no turn, so it is the one that does not
      // reach `sendBuildPrompt`'s forget (#209). Without this the offer it answered would still be
      // offering to reset an app that has just been reset.
      forgetLiveCards();
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

    // The answer to a table candidate card (#183): the click writes the record, then the request
    // the person already made is sent again against it. Two calls rather than one, because they are
    // two acts — the record stands whether or not the build that follows it succeeds, and the build
    // is an ordinary turn taking the turn lock like any other. Refreshing the Bindings first is
    // what puts the chosen table on the app's row before the build starts talking about it.
    //
    // Reloading the transcript is what retires the card, as it is for the offers above: the
    // server's copy of it carries no `live`, so its buttons go with the reload and the same card
    // cannot be answered twice. Answering it twice would overwrite the recorded table and start a
    // second build, silently, off a catalog the server may already have dropped as stale.
    //
    // `answered` carries the gates this turn was already past, so the replay does not walk back
    // into one the person has settled.
    async chooseTableAndBuild(prompt, sourceId, scope, answered, bindFirst = false) {
      await SW.api.confirmTableCandidate(sourceId, scope, bindFirst);
      // `refreshWorkingSet` for `saveScope`'s reason: this writes the same Scope the same door
      // writes, and the Project's row carries a copy of it under `usedBy`.
      await Promise.all([
        refreshBindings(),
        store.loadBuild({ keepPreview: true }),
        refreshWorkingSet(),
      ]);
      // The Scope the click just recorded, schema-qualified, for the bubble the send draws: the
      // server composes the same sentence off the Binding, and this one is what the person sees
      // while the turn runs. The database is left off it for the reason the card's own heading
      // carries it and its buttons do not — `MARTS` against `STAGING` is what they were picking
      // between.
      const tableName = [scope.schema, scope.table].filter(Boolean).join('.');
      return store.sendBuildPrompt(prompt, { ...(answered || {}), skipTableGate: true, tableName });
    },

    // The answer to a Data Source card (#185): the click records the Binding, then the request the
    // person already made is sent again — and walks into the table search against the store they
    // just named, which is the point of asking. `chosenSource` carries that store, because the
    // pick is the answer: somebody who clicked one row under a prompt naming another chose the row.
    //
    // Two calls for the reason the table card's click is two: the record stands whether or not the
    // turn after it succeeds, and that turn takes the turn lock like any other. Reloading the
    // transcript is what retires the card, so it cannot be answered twice.
    async chooseSourceAndSearch(prompt, sourceId, sourceName, answered) {
      await SW.api.bind('data_source', sourceId);
      // Three lists, three reads, none of them the others'. `refreshWorkingSet` is here for the
      // reason it is in `bindToApp`: this bind joined the Data Source to the Project (ADR-0018)
      // and put an app's name on its `usedBy`, and the rail draws both. Without it a store named
      // from a card sat off the panel entirely until the next scope load, while the build that
      // follows talked about it.
      await Promise.all([
        refreshBindings(),
        store.loadBuild({ keepPreview: true }),
        refreshWorkingSet(),
      ]);
      return store.sendBuildPrompt(prompt, {
        ...(answered || {}), chosenSource: sourceId, sourceName,
      });
    },

    // The other button: this request was never about a store. Nothing is recorded, and the gate is
    // answered rather than skipped — without it the same words meet the same card, forever.
    //
    // It carries `answered` for the same reason the pick above it does, and the cost of forgetting
    // is worse here: "start over and build a dashboard from Snowflake" answers the reset offer,
    // reaches this card, and a replay without `skipResetGate` offers to throw the app away again —
    // then answering THAT loses `skipSourceGate`, and the two cards trade the turn back and forth.
    async buildWithoutSource(prompt, answered) {
      await store.loadBuild({ keepPreview: true });
      return store.sendBuildPrompt(prompt, { ...(answered || {}), skipSourceGate: true });
    },

    // The same click, answered in Chat (#188). Two acts here too — the record stands whether or not
    // the answer after it succeeds — but the record is the Thread's own context row, because Chat
    // has no Built App to depend on anything. It reaches the Built App's Binding at the handoff.
    //
    // Reloading the Thread retires the card, as it does in Build: the server's copy carries no
    // `live`, so its buttons go with the reload and the same card cannot be answered twice.
    //
    // `echo` is off and `skipTableGate` is on for one reason between them: the question is already
    // in the transcript above the card, and the server will not write it a second time.
    async chooseTableAndAsk(prompt, threadId, sourceId, scope) {
      await SW.api.confirmThreadTableCandidate(threadId, sourceId, scope);
      const opened = await store.openThread(threadId);
      // The record stands either way — it is written above, and it belongs to the Thread rather
      // than to whatever is on screen. What must not follow it is the answer: `openThread` returns
      // null when a click on another conversation supersedes it, and `sendMessage` reads
      // `state.thread`, so replaying here would post this question into the conversation the person
      // moved to — with `echo` off and the question never written, under a card they cannot see.
      if (!opened || !state.thread || state.thread.id !== threadId) return null;
      return store.sendMessage(prompt, { echo: false, skipTableGate: true });
    },

    // The Dataset card's click (#196, ADR-0039). Two acts again — the attach stands whether or not
    // the build after it succeeds, and the build is an ordinary turn taking the turn lock — and the
    // attach is the DECLARATION here, not a step before one: a Dataset needs no scope on its
    // Binding because its files are declared by being attached.
    //
    // `answered` carries the gates this turn was already past, so the replay does not walk back
    // into one the person has settled. `skipDatasetGate` is this card being answered.
    async attachFileAndBuild(prompt, datasetId, path, answered) {
      await SW.api.attachDatasetFile(datasetId, path);
      // The whole scope, because an attach moves two lists at once: the app's files and the Build
      // header's account of what it ships. Reloading the transcript beside it is what RETIRES the
      // card — the server's copy carries no `live` — so the same card cannot be answered twice,
      // months later, into another attach and another build.
      await Promise.all([loadScopeData(), store.loadBuild({ keepPreview: true })]);
      return store.sendBuildPrompt(prompt, {
        ...(answered || {}), skipDatasetGate: true, datasetPick: path,
      });
    },

    // The folder row's click, which is one act rather than two hundred (ADR-0029). No confirm in
    // front of it, unlike the Data panel's folder attach: this click IS the answer to a question
    // Sage asked, and a modal on top of it would be the same question a second time.
    async attachFolderAndBuild(prompt, datasetId, folder, answered) {
      await SW.api.attachDatasetFolder(datasetId, folder);
      await Promise.all([loadScopeData(), store.loadBuild({ keepPreview: true })]);
      return store.sendBuildPrompt(prompt, {
        ...(answered || {}), skipDatasetGate: true, datasetPick: folder,
      });
    },

    // The way past the card: this app holds its own data, or the person does not want the question.
    // Nothing is attached, and the answer OUTLIVES this request — `datasetDismissed` names the
    // Dataset so the app is not asked again. Without that name the gate, which reads the app's
    // state rather than the request's words, would put this card in front of "make the button
    // blue". The agent is still told the Dataset cannot be read (#195), which is what keeps failing
    // open from meaning failing silently.
    async buildWithoutAttaching(prompt, datasetId, answered) {
      await store.loadBuild({ keepPreview: true });
      return store.sendBuildPrompt(prompt, {
        ...(answered || {}), skipDatasetGate: true, datasetDismissed: datasetId,
      });
    },

    // And the way past it in Chat, remembered against the Thread for the same reason: the card is
    // drawn off the Thread's own Dataset row, so without this the row would put the same card in
    // front of every question the conversation ever asks.
    async askWithoutAttaching(prompt, threadId, datasetId) {
      const opened = await store.openThread(threadId);
      if (!opened || !state.thread || state.thread.id !== threadId) return null;
      return store.sendMessage(prompt, {
        echo: false, skipDatasetGate: true, datasetDismissed: datasetId,
      });
    },

    // The same click, answered in Chat (#196). A different record, not a different act: Chat has no
    // Built App to attach to, so the file joins Session context as a `dsfile:` chip and crosses
    // into `App.requires` at the handoff.
    //
    // `echo` is off and `skipDatasetGate` is on for the one reason between them: the question is
    // already in the transcript above the card, and the server will not write it a second time.
    async pinDatasetFileAndAsk(prompt, threadId, datasetId, path) {
      await SW.api.pinThreadDatasetFile(threadId, datasetId, path);
      const opened = await store.openThread(threadId);
      // The chip stands either way — it was written above, and it belongs to the Thread rather than
      // to whatever is on screen. What must not follow it is the question landing in a conversation
      // the person has moved to, which is the race `chooseTableAndAsk` documents beside this.
      if (!opened || !state.thread || state.thread.id !== threadId) return null;
      return store.sendMessage(prompt, { echo: false, skipDatasetGate: true });
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

    // `options.buildAgain` marks the Plan page's "Build this again" (ADR-0024) rather than a first
    // approval. Everything else about the turn is identical, which is the point: one path, so a
    // rebuild streams, queues, retries and resumes exactly as the first build did.
    async approveBuild(answers, planEdits, planId, options = {}) {
      const buildAgain = !!options.buildAgain;
      if (!state.thread) await store.newThread();
      // Same rule as sendBuildPrompt, twice over: an approve is a build turn, so the person can
      // move to another Built App while it streams (#77) and it waits in line rather than being
      // refused when one is already going (#79).
      const turnApp = state.activeApp && state.activeApp.id;
      const movedOn = () => state.activeApp && state.activeApp.id !== turnApp;
      const turnThread = state.thread.id;
      let ticket = '';
      let unran = false;
      // This tab's own name for the turn once it is running, so the Stop bar has something to match
      // (#126). Held so the `finally` takes back exactly what it put there. See claimRunningTurn.
      let claim = null;
      // The same sentence the server writes for this turn, so the optimistic row does not change
      // wording the moment the transcript reloads underneath it.
      appendBuildRow({ type: 'user', text: buildAgain ? BUILD_AGAIN_TEXT : 'Approved the plan.' });
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
        if (buildAgain) payload.build_again = true;
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
          // A refused rebuild is a turn that never ran either, so it reloads for the same reason a
          // cancelled one does: the server persisted no user row, and the optimistic bubble above
          // the error would otherwise sit there claiming a build that never started.
          if (ev.contextChanged
              || (ev.type === 'done' && ['cancelled', 'plan moved on'].includes(ev.decision))) {
            unran = true;
          }
          // Past the queue and past every way this turn ends without running: it holds the lock, so
          // name it, and the Stop bar can match it from the first frame rather than only after a
          // mode switch has reloaded the state behind it.
          if (!unran && !claim) claim = claimRunningTurn('build', turnThread, turnApp);
          if (movedOn()) return;
          applyBuildEvent(ev);
          notify();
        });
        if (stopped) await store.loadBuild({ keepPreview: true });
      } catch (err) {
        applyBuildEvent({ type: 'error', message: String(err.message || err) });
        // A turn that never opened a stream is still a failed turn, and `readSSE` saw no frame to
        // notice it by. This is where a refused POST lands — including `_turn_slot_refusal`, which
        // refuses on exactly the dead model slot the chip is about (ADR-0027).
        store.refreshProblems();
      } finally {
        liveBuildTurns -= 1;
        dropQueuedTurn(ticket);
        state.buildRunning = liveBuildTurns > 0;
        state.buildTyping = state.buildRunning ? state.buildTyping : null;
        releaseRunningTurn(claim);
        notify();
        // `unran` reloads for the same reason `movedOn` does: an approve that never ran left an
        // "Approved the plan." bubble the server has no record of, and the plan is still waiting.
        if (movedOn() || unran) await store.loadBuild({ keepPreview: true });
        await Promise.all([probePreview(), refreshBindings(), loadThreadList()]);
        notify();
      }
    },

    // "Build this again" on the Plan page (ADR-0024). The page is a full window of its own, and the
    // build has to be watchable, so this walks over to Build before it starts: select the plan's
    // app, open the conversation the plan was written in, land on that route, then approve.
    //
    // In that order, and awaited, because each step is what makes the next one right. The server
    // holds one selected app per Project and the turn builds THAT one, so selecting first is not
    // cosmetic. `SW.appRoute` names the route off the store's open thread, so the conversation has
    // to be open before the route is written or the URL names the one we are leaving.
    //
    // BuildMode's own selection and conversation effects then find both already where they would
    // have put them and do nothing. Its transcript effect still runs, so a slow approve POST can
    // see the server's history land over the optimistic row — cosmetic, self-correcting, and the
    // same race every other build already runs.
    //
    // The conversation is the plan's own and never whichever one happens to be open: the server
    // withholds eligibility from a document that records none, so there is always one to open.
    async buildPlanAgain(plan) {
      await store.selectApp({ id: plan.appId });
      await store.openThread(plan.originThreadId);
      SW.router.go(SW.appRoute({ id: plan.appId }));
      // The document's own current text is the edit: the page's per-section editor has already
      // saved it, so there is nothing here to collect and nothing left over afterwards.
      return store.approveBuild('', plan.markdown, plan.id, { buildAgain: true });
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

    // The app dependencies modal. No read to gate on the way in — unlike `openBuildHistory`,
    // `bindings` and `appAttachments` are already kept current per app (`loadBuild`), so there is
    // nothing this needs to go and fetch.
    openAppDependencies() {
      state.appDependenciesOpen = true;
      notify();
    },

    closeAppDependencies() {
      state.appDependenciesOpen = false;
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

    // A Stop names the turn it meant, so the server can refuse to fire it at another one (#126).
    // Refused is not failed, and it is not silent either: the press did nothing, and the turn bar
    // re-rendering into a different sentence is the only other thing that would say so — which it
    // cannot when the reason is simply that the turn ended a moment before the POST landed.
    async stopBuild() {
      state.buildTyping = 'Stopping…';
      notify();
      const res = await SW.api.stopBuild({
        kind: 'build',
        conversation: (state.thread && state.thread.id) || '',
        app: (state.activeApp && state.activeApp.id) || '',
      });
      if (res && res.stopped === false) antd.message.info('That turn had already finished.');
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

    // `opts` is how `Not now` reuses this. Declining a Build offer runs the question the offer was
    // made instead of answering, and that turn is an ordinary Chat turn in every way but two: the
    // question is already on the Thread and already on screen, so neither end records it again.
    //   `echo: false` — do not push a second bubble for a question already in the transcript.
    //   `url`         — the decline route, which suppresses and then streams that turn.
    // `skipTableGate` is a candidate card being answered (#188): the table is already on the
    // Thread and the question is already in the transcript, so the turn neither re-asks nor
    // re-writes the person's sentence. `echo` is off for the same reason on that path.
    // `skipDatasetGate` says the same of the Dataset card (#196) — the file is pinned by then.
    async sendMessage(text, { echo = true, url = '', attachments: attachmentsOverride,
                              skipTableGate = false, skipDatasetGate = false,
                              datasetDismissed = '' } = {}) {
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

      const attachments = attachmentsOverride || state.attachments.map((a) => ({
        resourceId: a.resourceId,
        name: a.resourceName,
        kind: a.resourceKind,
        addedBy: a.addedBy,
      }));
      if (echo) {
        pushMessage({
          id: `u_${Date.now()}`,
          role: 'user',
          at: new Date().toISOString(),
          blocks: [{ type: 'text', value: text }],
          attachments,
        });
      }
      // The queue's two ends of this send (#79): `ticket` is what a Cancel would name, and `unran`
      // is whether it ended without ever running — the one case where the bubble above has to come
      // back off the screen, because the server recorded nothing to replace it with.
      let ticket = '';
      let unran = false;
      // And this tab's own name for the turn once it is running, so Chat's Stop bar has something
      // to match while this send holds the only stream there is. See claimRunningTurn.
      let claim = null;
      liveChatTurns += 1;
      state.typing = 'Thinking…';
      state.chatRunning = true;
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
      // `fromStream` marks a block this stream wrote, and is what the transcript record below
      // replaces. It is NOT `live`, which these blocks used to be filtered on — that key belongs to
      // the candidate and reset-offer cards, which this filter must leave standing.
      // `streaming` is narrower — it is the open block, the one still being written — because it
      // draws the blinking caret. They were one flag, so every fragment the turn closed kept
      // blinking for the rest of the turn: a Chat answer that read three files showed three carets.
      const flush = (closed) => {
        painting = false;
        if (liveIndex < 0) return;
        const blocks = [...assistant.blocks];
        blocks[liveIndex] = { type: 'text', value: streamed, fromStream: true, streaming: !closed };
        assistant.blocks = blocks;
        notify();
      };
      const paint = () => {
        if (painting) return;
        painting = true;
        // Wrapped rather than passed: rAF hands its callback a timestamp, which would arrive here
        // as a truthy `closed` and stop the caret on the first frame of every fragment.
        requestAnimationFrame(() => flush(false));
      };

      try {
        const res = await fetch(url || `./api/threads/${thread.id}/chat/stream`, {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          // The decline route ignores this and reads the pending question off the Thread, so a
          // stale tab cannot put a turn under a question it does not match.
          body: JSON.stringify({ prompt: text, skipTableGate, skipDatasetGate, datasetDismissed }),
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
          // Past the queue and past the two ways a turn ends without running: this one holds the
          // lock, so name it. Before the `mine()` check below, because a turn whose reader has
          // walked away is still the turn holding the lock.
          if (!claim) claim = claimRunningTurn('chat', turnThread, '');
          // Moved on. The turn is still running and the server is still writing its transcript, so
          // nothing is lost — reopening the conversation replays it. What is not wanted is this
          // answer appearing under a different question.
          if (!mine()) return;
          if (ev.type === 'delta') {
            state.typing = null;
            ensurePushed();
            if (liveIndex < 0) {
              liveIndex = assistant.blocks.length;
              assistant.blocks = [...assistant.blocks,
                                  { type: 'text', value: '', fromStream: true, streaming: true }];
              streamed = '';
            }
            if (ev.final) {
              // The whole text rather than the last fragment. The stream cannot be replayed, so
              // this is what repairs a live copy that dropped a frame — and it closes the block.
              streamed = ev.text || '';
              flush(true);
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
            assistant.blocks = [...assistant.blocks.filter((b) => !b.fromStream),
                                { type: 'text', value: ev.text }];
            notify();
          } else if (ev.type === 'agent' && ev.kind === 'tool') {
            state.typing = SW.util.activityLabel(ev);
            notify();
          } else if (ev.type === 'artifacts' || (ev.type === 'done' && ev.artifacts && ev.artifacts.length)) {
            state.typing = null;
            ensurePushed();
            const items = ev.items || ev.artifacts;
            // The server sends this list TWICE — once as `artifacts`, once again on the `done`
            // that closes the turn — so this filter is the only thing standing between one file
            // and two cards. Identity is the path. Folding each block down to whichever of
            // `src`/`path`/`title` was set first made the match depend on which key that
            // happened to be: a table had no `src` and no `path`, so it was matched on the title
            // the TURN wrote inside the JSON, while the incoming row carries a title derived
            // from the filename. Those differ the moment the JSON names itself, which is how one
            // "Adverse Events Summary" became two. Titles are not identifiers in the other
            // direction either — a matrix is written as a PNG and a table that share one on
            // purpose — so match on the path and nothing else.
            const have = new Set(
              assistant.blocks.filter((b) => b.type === 'image' || b.type === 'table' || b.type === 'file')
                .flatMap((b) => [b.src, b.path].filter(Boolean))
            );
            const fresh = (items || []).filter((a) => !have.has(fileUrl(a.path)) && !have.has(a.path));
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
          } else if (ev.type === 'withhold-search') {
            // The failure is already on screen; this is the line under it. Pushed as its own block
            // so the answer can replace it where it stands rather than arrive as a second card.
            state.typing = null;
            ensurePushed();
            assistant.blocks = [...assistant.blocks,
                                { type: 'withhold', searching: true, surface: 'chat', live: true }];
            notify();
          } else if (ev.type === 'withhold-found') {
            // Replaces the spinner IN PLACE. `live` is set here and nowhere else: this frame came
            // over SSE, so its buttons belong to the person watching, while the row a reload reads
            // back off the transcript renders the same sentence with none.
            state.typing = null;
            ensurePushed();
            putWithholdCard(state.messages, assistant, {
              type: 'withhold',
              searching: false,
              carriers: ev.carriers || [],
              complete: !!ev.complete,
              surviving: ev.surviving || 0,
              prompt: !!ev.prompt,
              stopped: ev.stopped || '',
              surface: 'chat',
              live: true,
            });
            notify();
          } else if (ev.type === 'table-candidates') {
            // The tables a search found, asked about before the turn ran (#188). `live` is set here
            // and nowhere else: this frame arrived over SSE, so its buttons belong to the person
            // looking at it, while the copy a reload reads back off the Thread has none.
            state.typing = null;
            ensurePushed();
            assistant.blocks = [...assistant.blocks, { ...ev, type: 'table_candidates', live: true }];
            notify();
          } else if (ev.type === 'dataset-files') {
            // What a Dataset holds, asked about before the turn ran (#196). `live` is set here and
            // nowhere else, for the reason the card above it is: this frame arrived over SSE, so
            // its buttons belong to the person looking at it.
            state.typing = null;
            ensurePushed();
            assistant.blocks = [...assistant.blocks, { ...ev, type: 'dataset_files', live: true }];
            notify();
          }
        });
      } catch (err) {
        if (mine()) {
          state.typing = null;
          ensurePushed();
          assistant.blocks = [...assistant.blocks, { type: 'text', value: String(err.message || err) }];
        }
        notify();
        // Same reason as the two build paths: a turn refused before its stream opened is a failed
        // turn that `readSSE` never saw a frame of, and it is refused most often by the slot check.
        store.refreshProblems();
      } finally {
        liveChatTurns -= 1;
        dropQueuedTurn(ticket);
        // The turn is over wherever the reader is now. Not `false` outright: this tab can have
        // several turns alive at once now, and the flag says "this project is busy", not "this
        // conversation is busy" — so it stays true while any of them is still here.
        state.chatRunning = liveChatTurns > 0;
        if (mine()) state.typing = null;
        releaseRunningTurn(claim);
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
        const res = await SW.api.stopBuild({
          kind: 'chat',
          conversation: (state.thread && state.thread.id) || '',
        });
        if (res && res.stopped === false) antd.message.info('That turn had already finished.');
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

    // `answerHere` is the explicit arm of the card — the offer that was made INSTEAD of running a
    // turn, so declining it owes the person that answer. The classifier arm sits under a turn that
    // already answered and owes nothing, and it stays a local suppress so it cannot flash a
    // spinner on its way to doing nothing. The card already knows which it is (`reason`), so this
    // no longer re-derives it from where the messages happen to sit.
    dismissPlanSuggestion({ answerHere = false } = {}) {
      const pending = lastUserText();
      state.messages = state.messages.filter(
        (m) => !m.blocks.some((b) => b.type === 'plan_suggestion')
      );
      if (!state.thread) {
        notify();
        return;
      }
      const id = state.thread.id;
      state.thread = { ...state.thread, handoff: { ...(state.thread.handoff || {}), suppressed: true, status: 'suppressed' } };
      notify();
      if (!answerHere || !pending) {
        // The classifier arm, or a Thread with nothing of the person's to re-run. Either way no
        // answer is owed, so this stays local and never touches the route.
        SW.api.patchThread(id, { handoff: 'suppress' }).catch(() => {});
        return;
      }
      // The offer was made INSTEAD of answering, so declining it owes the person that answer.
      // The route suppresses too, which is why there is no patch on this path.
      store.sendMessage(pending, { echo: false, url: `./api/threads/${id}/handoff/decline` });
    },

    // Dismissal is local and lasts until the next refusal re-offers. Nothing is patched: the
    // transcript is the record, and hiding a card is not an answer worth writing into it.
    //
    // Remembered in `dismissedRecallOffers` as well as filtered out of what is on screen, because
    // filtering alone only lasted until the next read rebuilt these messages — which is every
    // `openThread`, and in Build every two-second poll. The comment above has said "lasts until the
    // next refusal" since it shipped; this is what makes that true.
    dismissRecallOffer(offerKey) {
      if (offerKey !== undefined) dismissedRecallOffers.add(offerKey);
      state.messages = state.messages.filter(
        (m) => !m.blocks.some((b) => b.type === 'recall_offer')
      );
      notify();
    },

    // The rung below clearing, on both halves. Takes away one named thing the gateway refuses and
    // leaves the Conversation standing — so unlike `clearRecall` there is nothing to warn about and
    // nothing lost but the thing that was already unusable.
    //
    // Re-runs the failed turn only when something it read survives. Withhold the only file a turn
    // opened and there is nothing left to answer from, so re-running would spend a whole turn on
    // "I cannot read that" — the server says which case this is in `surviving`.
    // Local, like `dismissRecallOffer`: hiding a card is not an answer worth writing down. The
    // next refusal searches again and offers again, which is the behaviour a person expects from
    // something that only appears on a turn that has already failed.
    dismissWithholdCard(block) {
      dismissedWithholds.add(withholdCardKey(block || {}));
      // Both lists, because the card is the same card on both surfaces and only one of them is
      // being looked at. Build re-derives from `buildHistory`, where the remembered key is what
      // keeps it gone; Chat's drawn list is filtered now and re-derived on the next read.
      state.messages = state.messages.filter((m) => !m.blocks.some((b) => b.type === 'withhold'));
      applyBuildTranscript();
      notify();
    },

    async withholdContent(block) {
      const id = state.thread && state.thread.id;
      if (!id) return;
      const onBuild = block.surface === 'build';
      // Every line below that names a transcript has to name the RIGHT one. The first version of
      // this named Chat's on both surfaces, so on Build the door was called, the row was written,
      // and the person watched a card that did not move: `state.messages` is not what Build draws,
      // `lastUserPrompt` found nothing in it, and `sendMessage` is Chat's turn.
      const drawn = onBuild ? state.buildMessages : state.messages;
      // Two questions, and re-running needs both answered. `surviving` says there is something left
      // to answer FROM; `prompt` says the question itself is one of the things going. They come
      // apart in the case a person meets most: the guardrail matched what they typed, every file
      // the turn read survives, and `surviving` counts those files. Re-sending then asks nothing —
      // the same words hash to the same key (`chat_paths.text_key`) and are replaced by a
      // placeholder that tells the model to say it cannot see them — and the copy stays for good,
      // because the session has no delete, no revert and no fork (ADR-0022).
      const again = (block.surviving || 0) > 0 && !block.prompt ? lastUserPrompt(drawn) : '';
      const keys = (block.carriers || []).map((c) => c.key);
      const labels = (block.carriers || []).map((c) => c.label);
      if (!keys.length) return;
      try {
        if (onBuild) await SW.api.withholdBuildContent(id, keys, labels, block.prompt);
        else await SW.api.withholdContent(id, keys, labels, block.prompt);
      } catch (e) {
        antd.message.error(e && e.message ? e.message : "Couldn't stop sending that.");
        return;
      }
      // Re-read rather than push a receipt in from here: the server wrote the row and the
      // transcript is what renders it. One copy of the truth, as everywhere else on this object.
      // The card retires itself on the way through — `withheldKeys` reads the row that was just
      // written and the derivation stops drawing the question it answered.
      if (onBuild) {
        // And the memory of it goes too (#209). `rememberLiveCard` holds a `withhold-found` key for
        // the life of the Conversation, so without this a card answered once would come back
        // answerable on any later poll that redrew it. Same reason `resetApp` forgets by hand: the
        // path that starts no turn is the one `sendBuildPrompt` never gets to clean up after.
        forgetLiveCards();
        await applyBuildRead(await readBuildTranscript(id));
        notify();
      } else {
        await store.openThread(id);
      }
      if (!again) return;
      // The same turn running again with the refused content no longer in it, not a new thing the
      // person asked. Build echoes the request back into the transcript and Chat does not
      // (`echo: false`), which is each surface's own convention for a card that re-runs a turn —
      // see `resetAndBuild` and `chooseTableAndBuild` for the Build half of it.
      if (onBuild) await store.sendBuildPrompt(again);
      else await store.sendMessage(again, { echo: false });
    },

    // Build's half of `clearRecall` below. The conversation is `state.thread.id` here too — a Build
    // Conversation IS the Thread that drove it — but the app is not named at all: the server empties
    // the session filed under whichever Built App is selected, which is the one this transcript
    // belongs to (ADR-0022).
    async clearBuildRecall(scope) {
      try {
        await SW.api.clearBuildRecall(state.thread && state.thread.id, scope);
      } catch (e) {
        antd.message.error("Couldn't clear recall.");
        return;
      }
      // Re-read rather than push the divider in from here, for the reason Chat re-opens: the server
      // wrote the event and the transcript is what renders it. One copy of the truth. The card goes
      // with it — the `recall-cleared` row the server just wrote retires the offer above it.
      const watched = state.thread && state.thread.id;
      await applyBuildRead(
        watched ? await readBuildTranscript(watched) : { merged: null, history: [] });
      notify();
    },

    // Remembered rather than filtered out of what is drawn, because Build rebuilds this transcript
    // from `buildHistory` every two seconds — a filtered message list lasted until the next tick.
    dismissBuildRecallOffer(offerKey) {
      if (offerKey !== undefined) dismissedRecallOffers.add(offerKey);
      applyBuildTranscript();
      notify();
    },

    async clearRecall(scope) {
      const id = state.thread && state.thread.id;
      if (!id) return;
      state.messages = state.messages.filter(
        (m) => !m.blocks.some((b) => b.type === 'recall_offer')
      );
      notify();
      try {
        await SW.api.clearRecall(id, scope);
      } catch (e) {
        antd.message.error("Couldn't clear recall.");
        return;
      }
      // Reopen rather than push the divider in from here: the server wrote the event, and the
      // transcript is what renders it. One copy of the truth, the way every other turn works.
      await store.openThread(id);
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
      await refreshWorkingSet();
      const resource = {
        id: `file:${body.path}`,
        name: name,
        kind: 'file',
        path: body.path,
        source: body.source || 'scratch',
      };
      state.resourceIndex[resource.id] = resource;
      await store.attach(resource.id, 'user', 'Uploaded in this conversation.', { silent: true });
      antd.message.success(`${name} is now in this conversation.`);
      return resource;
    },

    async pinLeaf(parent, pin) {
      if (!parent || !parent.id) return null;
      await store.addToProject({ ...parent, pin }, { silent: true });
      return true;
    },

    async unpinLeaf(parent, pin) {
      await SW.api.unpinFromProject(parent.id, pin);
      await refreshWorkingSet();
      return true;
    },

    // `datasetId` may be empty, which asks the server for the same default target an unpicked upload
    // lands on (`_resolve_upload_target`). The panel always names one — its menu lists them — but a
    // refusal card offers one click and has no list to show (#135). `quiet` is for that caller too:
    // it says the file reached the APP, which is the question that was asked, and two toasts saying
    // one thing is being told twice.
    async addScratchToDataset(resource, datasetId, options = {}) {
      if (!resource || !resource.path) return null;
      let res;
      try {
        res = await SW.api.promoteScratch(resource.path, datasetId);
      } catch (err) {
        antd.message.error(err.message);
        return null;
      }
      const oldId = resource.id;
      // The working set, not the whole scope. A promote moves a row between two groups the
      // membership read and `/project` answer for separately, and `loadScopeData` writes the first
      // and DEFERS the second — so for the length of the platform listing read (2.5-3.3 s) the
      // group map it wrote carries no `file` key at all, and the Files heading and every row under
      // it vanish before the promoted one comes back on the Dataset. `refreshWorkingSet` reads both
      // halves together and writes them together, which is why the Upload directly above this has
      // never flickered. Nothing a promote changes is something the platform listing answers (#162).
      await refreshWorkingSet();
      // Except the lock, which a promote CAN move (ADR-0043): `upload_file` attaches the promoted
      // file under `public/data/`, so a Dataset that was in nobody's scope is now in this app's.
      // The three other attach paths reach this through `loadScopeData`, which this one
      // deliberately does not call, so it is asked for by name.
      //
      // Behind a LOCAL test, because #162's whole claim is that a promote reads no platform
      // listing and this read is one. `declared` rides on the Dataset row the rail already holds,
      // so an opted-out deployment — where no row ever carries it — pays nothing, and neither does
      // a promote onto an ordinary Dataset. The already-locked half is for the other direction: a
      // second declared Dataset joining the scope changes the sentence the notice draws.
      const target = state.resourceIndex[`dataset:${res.dataset_id || datasetId}`];
      if ((target && target.declared) || SW.util.isLocked(state.sensitivity)) refreshSensitivity();
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
      if (!options.quiet) {
        antd.message.success(SW.brand.text('{name} is now on the {dataset}.', { name: resource.name }));
      }
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

    // No caller in the Workbench: a scope load is reached through `setScope` or through boot.
    // `js/working_set_refresh_harness.mjs` reaches `loadScopeData` through it, to put one in
    // flight against a mutation — so a dead-export sweep takes two tests with it.
    reloadScopeData: loadScopeData,
    reloadThreads: loadThreadList,
    reloadAttachments: refreshAttachments,
    reloadMembers,

    // The panel's pin names the plan document, so renaming one on the plan page has to reach the
    // pin without a reload. `refreshProjectPlan` does not notify on its own — its other callers
    // fold it into a bigger read that does — so this one says so itself.
    reloadProjectPlan: () => refreshProjectPlan().then(notify, () => {}),

    // The sensitivity lock (ADR-0043). Re-read by hand after an act that can change a declaration
    // without changing a Binding — declaring a Dataset sensitive from the promote confirm is the
    // only one today.
    reloadSensitivity: () => refreshSensitivity(),

    // The switch notice has been read. `key` is what the notice SAID — the approved set and the
    // model it named — rather than a bare flag, so an administrator editing OR reordering the group
    // moves the session again and the notice comes back: that is a new fact about a different
    // model, and the one already dismissed was never an answer to it. The composer builds the key,
    // and every key dismissed stays dismissed — see `sensitivityNoticeFor`.
    dismissSensitivityNotice(key) {
      const seen = String(key || '');
      if (!state.sensitivityNoticeFor.includes(seen)) {
        state.sensitivityNoticeFor = state.sensitivityNoticeFor.concat(seen);
      }
      notify();
    },

    // Declare a Dataset this Project owns sensitive, from the promote confirm (ADR-0043). The
    // server refuses one shared in; this reports what it answered and never guesses. The tag write
    // is best-effort by design — the bytes are already on the mount, and losing an upload to a
    // governance tag would be the wrong trade — so a `tagged: false` is said plainly rather than
    // swallowed or raised.
    async declareDatasetSensitive(datasetId) {
      let res;
      try {
        res = await SW.api.declareDatasetSensitive(datasetId);
      } catch (err) {
        antd.message.error(err.message);
        return null;
      }
      if (res.tagged) {
        antd.message.success(SW.brand.text(
          '{name} is now sensitive. Only approved models can be used with it.',
          { name: res.dataset }
        ));
      } else {
        antd.message.warning(SW.brand.text(
          "Couldn't mark {name} as sensitive. The file is uploaded — tag the {dataset} in "
          + '{platformName} to finish.',
          { name: res.dataset }
        ));
      }
      await refreshWorkingSet();
      await refreshSensitivity();
      return res;
    },

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
