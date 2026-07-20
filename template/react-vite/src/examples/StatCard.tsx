// Golden example: a small, typed, styled component. Agents should copy this shape
// (typed props, a single-purpose component, colocated inline styles or a CSS module).

export interface StatCardProps {
  label: string;
  value: string | number;
  hint?: string;
}

export function StatCard({ label, value, hint }: StatCardProps) {
  return (
    <div
      style={{
        padding: "1rem 1.25rem",
        border: "1px solid #e0e0e0",
        borderRadius: 8,
        background: "#fff",
        minWidth: 160,
      }}
    >
      <div style={{ fontSize: 12, color: "#65657b" }}>{label}</div>
      <div style={{ fontSize: 24, fontWeight: 600, color: "#2e2e38" }}>{value}</div>
      {hint ? <div style={{ fontSize: 12, color: "#8f8fa3" }}>{hint}</div> : null}
    </div>
  );
}
