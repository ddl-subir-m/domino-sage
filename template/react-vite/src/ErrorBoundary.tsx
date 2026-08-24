import { Component, type ErrorInfo, type ReactNode } from "react";
import { buildIsRunning, reportRuntimeError } from "./reportRuntimeError";

// A render/runtime throw in the app used to unmount React to nothing — a silent blank preview
// whose real error lived only in the browser console. This boundary catches it and shows the
// message + stack inline, so a crash is legible in the preview instead of a blank page.
// (The agent's generated code is what usually throws; this makes that visible, not hidden.)
//
// It says one of two things, depending on whose crash it is. DURING a build the agent is part-way
// through rewriting these files and the in-between versions throw routinely — three writes to
// App.tsx in 25 seconds, live on 2026-08-24. Sage has already been sent the error and says so in
// the chat ("app crashed at runtime — fixing"), so a red card telling the creator to go fix the
// code both contradicts that and teaches them to distrust a preview that is merely mid-edit.
// OUTSIDE a build the crash really is theirs to act on, and the card stays exactly as it was.
//
// What does not change either way: the error is still reported. That report is what the build loop
// waits on to autofix, so quieting the card must never quiet the channel.
type Props = { children: ReactNode };
type State = { error: Error | null; building: boolean };

const POLL_MS = 2000; // the interval the builder's own idle-poll uses

export default class ErrorBoundary extends Component<Props, State> {
  state: State = { error: null, building: false };
  private timer: number | null = null;

  static getDerivedStateFromError(error: Error): State {
    // Start blunt. componentDidCatch softens it only once the builder has confirmed a live build,
    // so a check that never answers leaves the honest card on screen rather than a reassuring one.
    return { error, building: false };
  }

  componentDidCatch(error: Error, info: ErrorInfo): void {
    console.error("App crashed:", error, info.componentStack);
    // Report render-tree throws (which React catches here, so window.onerror never sees them) so
    // the builder can feed the error back to the agent to autofix.
    reportRuntimeError(error.message, error.stack || info.componentStack || undefined);
    void this.watchBuild();
  }

  componentWillUnmount(): void {
    this.stopPolling();
  }

  private stopPolling(): void {
    if (this.timer !== null) {
      window.clearInterval(this.timer);
      this.timer = null;
    }
  }

  // Ask once, then keep asking until the build ends.
  //
  // The poll is the part that matters. Sage only retries a runtime crash a bounded number of times,
  // so a build can finish with the app still broken — and a card that went on promising a fix after
  // the build that would have delivered it had stopped would be worse than never softening at all.
  // When the build ends the card flips back to the blunt one, over the same error.
  private async watchBuild(): Promise<void> {
    const running = await buildIsRunning();
    this.setState({ building: running });
    if (!running || this.timer !== null) return;
    this.timer = window.setInterval(() => {
      void buildIsRunning().then((still) => {
        this.setState({ building: still });
        if (!still) this.stopPolling();
      });
    }, POLL_MS);
  }

  render(): ReactNode {
    const { error, building } = this.state;
    if (!error) return this.props.children;
    return (
      <main
        style={{
          minHeight: "100svh",
          display: "flex",
          alignItems: "center",
          justifyContent: "center",
          padding: 24,
          boxSizing: "border-box",
        }}
      >
        <div
          style={{
            maxWidth: 640,
            width: "100%",
            textAlign: "left",
            background: "var(--surface)",
            border: "1px solid var(--border)",
            borderRadius: "var(--radius)",
            boxShadow: "var(--shadow)",
            padding: 24,
          }}
        >
          <h2 style={{ color: building ? "var(--text-h)" : "var(--danger)", margin: "0 0 8px" }}>
            {building ? "Sage is still building this app" : "The app crashed while rendering"}
          </h2>
          <p style={{ color: "var(--text-muted)", margin: "0 0 16px" }}>
            {building
              ? "The agent is part-way through an edit, so this error is expected. Sage has it and is working on a fix — the preview reloads on its own when the code changes."
              : "A runtime error was thrown. Fix the code below (or ask the agent to), and the preview will reload automatically."}
          </p>
          <pre
            style={{
              margin: 0,
              padding: 12,
              background: "var(--code-bg)",
              borderRadius: 4,
              fontFamily: "var(--mono)",
              fontSize: 13,
              lineHeight: 1.5,
              color: "var(--text-h)",
              whiteSpace: "pre-wrap",
              wordBreak: "break-word",
              overflowX: "auto",
            }}
          >
            {error.message}
            {error.stack ? "\n\n" + error.stack : ""}
          </pre>
        </div>
      </main>
    );
  }
}
