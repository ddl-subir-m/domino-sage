import "./App.css";

// Warm placeholder: renders immediately so the preview shows a running app BEFORE the agent
// writes a line (Step 3.1 / SPEC C9). The coding agent replaces this file with the real app.
function App() {
  return (
    <main className="sage-placeholder">
      <h1>Your app will appear here</h1>
      <p>Describe what you want to build in the chat, and it will take shape live in this preview.</p>
    </main>
  );
}

export default App;
