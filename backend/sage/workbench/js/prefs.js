window.SW = window.SW || {};

// The viewer's own preferences for the Workbench (#52). One record per person, and the same
// record whichever Project they are in.
//
// Why the browser rather than the Builder. Each Project runs this viewer in its own Sage Builder
// container — `/api/projects/{id}/open` says it plainly: switching Project means leaving this
// container for another one — so anything written inside the Builder is gone after the switch.
// The one thing in that container that does survive is the Project's git repo, and that is
// exactly where a preference must never live: two viewers in one Project run two Builders against
// one remote and collide on anything shared, which is why #62 spent three tickets taking the
// Thread index (#64), the rendered history (#65) and the build log (#68) back out of it.
//
// What does span Projects is the origin. `workspace_open_url` hands the browser a host-relative
// path, so every Builder this viewer opens is served from the same Domino host, and localStorage
// is scoped to that host. Keyed by viewer on top of that, so two people sharing one browser
// profile read their own answers instead of overwriting each other's.
(function () {
  const KEY = 'sw.prefs';

  // A preference is its default plus the values it is allowed to take. Anything else read back is
  // treated as absent: localStorage is editable by hand and outlives the build that wrote it, so
  // a value with no branch behind it would leave the UI drawing nothing.
  const PREFS = {
    // Two views of one Conversation: split keeps Chat and Build as separate halves, which is what
    // the Workbench does today, and unified shows one transcript in both (#50). Nothing reads this
    // yet — this ticket only gives the answer somewhere to live.
    conversationView: { fallback: 'split', values: ['split', 'unified'] },

    // What a confirmed handoff carries from the Conversation into the Built App (#58). These were
    // four checkboxes rebuilt from hardcoded defaults every time the sheet opened, so the same
    // person answered the same questions on every handoff. The fallbacks are those defaults
    // exactly: the answer moves, what a handoff writes does not.
    //
    // Where the handoff LANDS is deliberately absent and stays absent. The sheet still asks which
    // Built App every time and still preselects none, because a Project holds many and building
    // over one the person did not choose is the silent overwrite ADR-0008 exists to close (#73).
    handoffResources: { fallback: true, values: [true, false] },
    handoffArtifacts: { fallback: true, values: [true, false] },
    handoffTranscript: { fallback: false, values: [true, false] },

    // The first chip's one-time note (#137). A chip is Session context — this Conversation's
    // only — and the note that teaches it can be dismissed for good. True is the viewer's own
    // "don't show this again". There is no "seen" value on purpose: an undismissed note may
    // show again, a dismissed one never does.
    chipScopeHintDismissed: { fallback: false, values: [true, false] },
  };

  // `/api/me` answers "me" when the container has no identity to report, so the key matches what
  // that said rather than inventing a second name for the same person.
  function viewer() {
    const me = SW.store && SW.store.get().me;
    return String((me && me.id) || 'me');
  }

  // An array answers `typeof === 'object'` but drops named properties on the way back through
  // JSON.stringify, so treating one as a record would make every write vanish without ever
  // throwing — the choice on screen and the choice on file would disagree, silently.
  function asRecord(value) {
    return value && typeof value === 'object' && !Array.isArray(value) ? value : null;
  }

  function readAll() {
    try {
      return asRecord(JSON.parse(window.localStorage.getItem(KEY))) || {};
    } catch (err) {
      // Storage that is blocked and storage that is unparseable are the same situation to a
      // reader: there is no answer on file, so the fallbacks are the answer.
      return {};
    }
  }

  SW.prefs = {
    get(name) {
      const spec = PREFS[name];
      const stored = (asRecord(readAll()[viewer()]) || {})[name];
      return spec.values.includes(stored) ? stored : spec.fallback;
    },

    // True once the answer is on file. False is worth telling the reader about: they made a
    // choice, and the next load will not honour it. `get` refuses a value it does not recognise,
    // so `set` has to refuse the same ones — otherwise a write would read back as the fallback
    // and the control would sit on a value nothing agrees with.
    set(name, value) {
      const spec = PREFS[name];
      if (!spec.values.includes(value)) return false;
      const all = readAll();
      const who = viewer();
      all[who] = { ...(asRecord(all[who]) || {}), [name]: value };
      try {
        window.localStorage.setItem(KEY, JSON.stringify(all));
        return true;
      } catch (err) {
        console.warn('[prefs] this browser would not store the choice', err);
        return false;
      }
    },
  };
})();
