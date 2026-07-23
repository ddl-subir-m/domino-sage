import { useState } from 'react'

// HMR PROOF: change the word below (e.g. "one" -> "two") while the workspace is open.
// If the text updates WITHOUT the counter resetting to 0, hot-module-reload works through
// Domino's proxy and Path A is viable. If the page full-reloads (counter resets) or the
// browser console shows a failed wss:// connection, HMR is not connecting — capture the
// failing ws URL + the Vite startup log and report back.
const MARKER = 'edit-marker: one'

export default function App() {
  const [n, setN] = useState(0)
  return (
    <div style={{ fontFamily: 'system-ui', padding: 32 }}>
      <h1 style={{ color: '#1820A0' }}>sage path spike — preview</h1>
      <p>{MARKER}</p>
      <button onClick={() => setN((x) => x + 1)} style={{ fontSize: 18, padding: '8px 16px' }}>
        count: {n}
      </button>
      <p style={{ color: '#7F8385' }}>
        Increment the counter, then edit <code>MARKER</code> in <code>app/src/App.jsx</code>.
        HMR works if the text changes but the count is preserved.
      </p>
    </div>
  )
}
