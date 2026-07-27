import { Component, type ErrorInfo, type ReactNode } from "react";

// A render/runtime throw in the app used to unmount React to nothing — a silent blank preview
// whose real error lived only in the browser console. This boundary catches it and shows the
// message + stack inline, so a crash is legible in the preview instead of a blank page.
// (The agent's generated code is what usually throws; this makes that visible, not hidden.)
type Props = { children: ReactNode };
type State = { error: Error | null };

export default class ErrorBoundary extends Component<Props, State> {
  state: State = { error: null };

  static getDerivedStateFromError(error: Error): State {
    return { error };
  }

  componentDidCatch(error: Error, info: ErrorInfo): void {
    console.error("App crashed:", error, info.componentStack);
  }

  render(): ReactNode {
    const { error } = this.state;
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
          <h2 style={{ color: "var(--danger)", margin: "0 0 8px" }}>
            The app crashed while rendering
          </h2>
          <p style={{ color: "var(--text-muted)", margin: "0 0 16px" }}>
            A runtime error was thrown. Fix the code below (or ask the agent to), and the preview
            will reload automatically.
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
