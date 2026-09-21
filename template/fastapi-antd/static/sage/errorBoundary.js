// A render or runtime throw in the app would unmount React to nothing — a silent blank page whose
// real error lived only in the browser console. This boundary catches it and shows the message and
// stack inline, so a crash is legible on screen instead of a blank page.
//
// It says one of two things, depending on whose crash it is. DURING a build the agent is part-way
// through rewriting these files and the in-between versions throw routinely. Sage has already been
// sent the error and says so in the chat, so a red card telling the creator to go fix the code both
// contradicts that and teaches them to distrust a preview that is merely mid-edit. OUTSIDE a build
// the crash really is theirs to act on, and the card is blunt.
//
// What does not change either way: the error is still reported. That report is what the build loop
// waits on to autofix, so quieting the card must never quiet the channel.
//
// Sage owns this file. Do not edit it. Wrap the app in it: `h(sage.ErrorBoundary, null, h(App))`.
window.sage = window.sage || {};

(function () {
  const h = React.createElement;
  const POLL_MS = 2000; // the interval the builder's own idle-poll uses

  class ErrorBoundary extends React.Component {
    constructor(props) {
      super(props);
      this.state = { error: null, building: false };
      this.timer = null;
    }

    static getDerivedStateFromError(error) {
      // Start blunt. componentDidCatch softens it only once the builder has confirmed a live build,
      // so a check that never answers leaves the honest card on screen rather than a reassuring one.
      return { error, building: false };
    }

    componentDidCatch(error, info) {
      console.error("App crashed:", error, info && info.componentStack);
      // React catches render-tree throws here, so window.onerror never sees them: report them so
      // the builder can feed the error back to the agent.
      sage.reportRuntimeError(error.message, error.stack || (info && info.componentStack) || undefined);
      void this.watchBuild();
    }

    componentWillUnmount() {
      this.stopPolling();
    }

    stopPolling() {
      if (this.timer !== null) {
        window.clearInterval(this.timer);
        this.timer = null;
      }
    }

    // Ask once, then keep asking until the build ends. Sage only retries a runtime crash a bounded
    // number of times, so a build can finish with the app still broken — and a card that went on
    // promising a fix after the build that would have delivered it had stopped would be worse than
    // never softening at all. When the build ends the card flips back to the blunt one.
    async watchBuild() {
      const running = await sage.buildIsRunning();
      this.setState({ building: running });
      if (!running || this.timer !== null) return;
      this.timer = window.setInterval(() => {
        void sage.buildIsRunning().then((still) => {
          this.setState({ building: still });
          if (!still) this.stopPolling();
        });
      }, POLL_MS);
    }

    render() {
      const { error, building } = this.state;
      if (!error) return this.props.children;
      return h("main", {
        style: { minHeight: "100svh", display: "flex", alignItems: "center",
                 justifyContent: "center", padding: 24, boxSizing: "border-box" },
      },
        h("div", {
          style: { maxWidth: 640, width: "100%", textAlign: "left", background: "var(--surface)",
                   border: "1px solid var(--border)", borderRadius: "var(--radius-lg)", padding: 24 },
        },
          h("h2", { style: { color: building ? "var(--text)" : "var(--danger)", margin: "0 0 8px" } },
            building ? "Sage is still building this app" : "The app crashed while rendering"),
          h("p", { style: { color: "var(--text-muted)", margin: "0 0 16px" } },
            building
              ? "The agent is part-way through an edit, so this error is expected. Sage has it and is working on a fix — the preview reloads on its own when the code changes."
              : "A runtime error was thrown. Fix the code below (or ask the agent to), and the preview will reload automatically."),
          h("pre", {
            style: { margin: 0, padding: 12, background: "var(--bg)", borderRadius: 4,
                     fontFamily: "var(--mono)", fontSize: 13, lineHeight: 1.5, color: "var(--text)",
                     whiteSpace: "pre-wrap", wordBreak: "break-word", overflowX: "auto" },
          }, error.message + (error.stack ? "\n\n" + error.stack : ""))));
    }
  }

  sage.ErrorBoundary = ErrorBoundary;
})();
