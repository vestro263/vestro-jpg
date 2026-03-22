// ─── shared design tokens ─────────────────────────────────────────────────────
export const S = {
  page:  { padding: '24px', display: 'flex', flexDirection: 'column', gap: 20 },
  grid4: { display: 'grid', gridTemplateColumns: 'repeat(4,minmax(0,1fr))', gap: 12 },
  grid2: { display: 'grid', gridTemplateColumns: 'repeat(2,minmax(0,1fr))', gap: 16 },
  grid3: { display: 'grid', gridTemplateColumns: 'repeat(3,minmax(0,1fr))', gap: 16 },
  card:  { background: '#111827', border: '1px solid #1f2937', borderRadius: 12, padding: 16 },
  h3:    { fontSize: 13, fontWeight: 600, color: '#e5e7eb', marginBottom: 12 },
  td:    { padding: '8px 12px', fontSize: 12, color: '#d1d5db', borderBottom: '1px solid #1f2937' },
  th:    { padding: '8px 12px', fontSize: 11, color: '#6b7280', textTransform: 'uppercase', letterSpacing: '0.05em', borderBottom: '1px solid #374151', textAlign: 'left' },
}

// ── StatCard — mirrors your existing StatCard.jsx ─────────────────────────────
export function StatCard({ label, value, sub, color = '#f1f5f9' }) {
  return (
    <div style={{ background: '#1f2937', borderRadius: 10, padding: '12px 14px', minWidth: 0 }}>
      <div style={{ fontSize: 10, color: '#6b7280', textTransform: 'uppercase', letterSpacing: '0.05em', fontWeight: 500 }}>
        {label}
      </div>
      <div style={{ fontSize: 22, fontWeight: 600, color, marginTop: 4, lineHeight: 1.2 }}>
        {value}
      </div>
      {sub && <div style={{ fontSize: 11, color: '#6b7280', marginTop: 3 }}>{sub}</div>}
    </div>
  )
}

// ── DirectionBadge ────────────────────────────────────────────────────────────
export function DirectionBadge({ direction }) {
  const isBuy  = direction === 1 || direction === 'buy'
  const isFlat = direction === 0
  const label  = isBuy ? '▲ BUY' : isFlat ? '— FLAT' : '▼ SELL'
  const styles = isBuy
    ? { background: '#052e16', color: '#4ade80', border: '1px solid #166534' }
    : isFlat
    ? { background: '#1f2937', color: '#9ca3af', border: '1px solid #374151' }
    : { background: '#1c0a0a', color: '#f87171', border: '1px solid #991b1b' }
  return (
    <span style={{ display: 'inline-flex', alignItems: 'center', gap: 4, padding: '2px 8px', borderRadius: 6, fontSize: 11, fontWeight: 600, ...styles }}>
      {label}
    </span>
  )
}

// ── ATRZoneBadge ──────────────────────────────────────────────────────────────
export function ATRZoneBadge({ zone }) {
  const map = {
    high:   { bg: '#1c1400', color: '#fbbf24', border: '#92400e', label: 'HIGH ATR' },
    medium: { bg: '#0c1a2e', color: '#60a5fa', border: '#1e40af', label: 'MED ATR'  },
    low:    { bg: '#111827', color: '#6b7280', border: '#374151', label: 'LOW ATR'  },
  }
  const t = map[zone] || map.low
  return (
    <span style={{ display: 'inline-flex', alignItems: 'center', padding: '2px 8px', borderRadius: 6, fontSize: 11, fontWeight: 600, background: t.bg, color: t.color, border: `1px solid ${t.border}` }}>
      {t.label}
    </span>
  )
}

// ── TSSBar ────────────────────────────────────────────────────────────────────
export function TSSBar({ score }) {
  const pct   = Math.min(100, Math.max(0, ((score + 1) / 2) * 100))
  const color = pct > 65 ? '#4ade80' : pct > 40 ? '#fbbf24' : '#f87171'
  return (
    <div style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
      <div style={{ flex: 1, height: 6, background: '#1f2937', borderRadius: 99, overflow: 'hidden' }}>
        <div style={{ width: `${pct}%`, height: '100%', background: color, borderRadius: 99, transition: 'width 0.4s ease' }} />
      </div>
      <span style={{ fontSize: 11, color, minWidth: 28, textAlign: 'right' }}>{score?.toFixed(2)}</span>
    </div>
  )
}

// ── Empty state ───────────────────────────────────────────────────────────────
export function Empty({ icon = '—', text = 'No data' }) {
  return (
    <div style={{ padding: '32px 0', textAlign: 'center', color: '#4b5563' }}>
      <div style={{ fontSize: 28, marginBottom: 8 }}>{icon}</div>
      <div style={{ fontSize: 12 }}>{text}</div>
    </div>
  )
}