import { useState } from 'react'

// HMR PROOF: the "Bump marker" button in the outer page rewrites this number via the server,
// which triggers Vite HMR. If the marker below changes WHILE the counter keeps its value, HMR
// works through Domino's proxy and Path A is viable. If the page full-reloads (counter resets)
// or the browser console shows a failed wss:// connection, capture the failing ws URL + the
// Vite startup log and report back. (You can also edit this by hand.)
const MARKER = 'edit-marker #0'

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
        Increment the counter, then click <b>Bump marker</b> in the top bar. HMR works if the
        marker number below changes while the count is preserved.
      </p>
    </div>
  )
}
