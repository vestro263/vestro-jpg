/**
 * Valuation.jsx
 * Drop into mt5-dashboard/src/pages/
 * Add to your router the same way Dashboard.jsx is registered.
 *
 * Matches Vestro's existing dark theme — uses the same CSS variables
 * and class patterns as Dashboard.jsx / Signals.jsx.
 */
import { useState, useEffect } from 'react'
import { useValuationEngine } from '../hooks/useValuationEngine'

// ── Conviction colour helper ──────────────────────────────────────────────
function convictionColor(c) {
  if (!c) return '#6b7280'
  if (c >= 80) return '#22c55e'
  if (c >= 60) return '#f59e0b'
  return '#ef4444'
}

function signalLabel(type) {
  return {
    headcount_delta: 'Headcount Δ',
    funding_round:   'Funding',
    news_sentiment:  'News',
    exec_departure:  'Exec departure',
  }[type] ?? type
}

function formatFunding(n) {
  if (!n) return '—'
  if (n >= 1e9) return `$${(n / 1e9).toFixed(1)}B`
  if (n >= 1e6) return `$${(n / 1e6).toFixed(0)}M`
  return `$${(n / 1e3).toFixed(0)}K`
}

// ── Sub-components ────────────────────────────────────────────────────────

function WsBadge({ status }) {
  const map = {
    connected:    { color: '#22c55e', label: 'Live' },
    connecting:   { color: '#f59e0b', label: 'Connecting' },
    reconnecting: { color: '#f59e0b', label: 'Reconnecting' },
    error:        { color: '#ef4444', label: 'Offline' },
  }
  const { color, label } = map[status] ?? map.error
  return (
    <span style={{
      display: 'inline-flex', alignItems: 'center', gap: 5,
      fontSize: 11, color, padding: '2px 8px',
      border: `1px solid ${color}33`, borderRadius: 4,
    }}>
      <span style={{
        width: 6, height: 6, borderRadius: '50%', background: color,
      }} />
      {label}
    </span>
  )
}

function StatCard({ label, value, sub, color }) {
  return (
    <div style={{
      background: 'var(--bg-secondary, #1a1f2e)',
      border: '1px solid var(--border, #2a2f3e)',
      borderRadius: 8, padding: '14px 16px',
    }}>
      <div style={{ fontSize: 11, color: '#6b7280', textTransform: 'uppercase',
                    letterSpacing: '0.05em', marginBottom: 6 }}>{label}</div>
      <div style={{ fontSize: 22, fontWeight: 500,
                    color: color ?? 'var(--text-primary, #f1f5f9)' }}>{value}</div>
      {sub && <div style={{ fontSize: 12, color: '#6b7280', marginTop: 4 }}>{sub}</div>}
    </div>
  )
}

function ProbBar({ rise, fall }) {
  const rPct = Math.round((rise ?? 0.5) * 100)
  const fPct = 100 - rPct
  return (
    <div style={{ display: 'flex', height: 14, borderRadius: 3, overflow: 'hidden', width: 120 }}>
      <div style={{ width: `${rPct}%`, background: '#16a34a',
                    display: 'flex', alignItems: 'center', justifyContent: 'flex-end',
                    paddingRight: 3 }}>
        <span style={{ fontSize: 9, color: '#dcfce7', fontWeight: 600 }}>{rPct}%</span>
      </div>
      <div style={{ width: `${fPct}%`, background: '#b91c1c',
                    display: 'flex', alignItems: 'center', paddingLeft: 3 }}>
        <span style={{ fontSize: 9, color: '#fee2e2', fontWeight: 600 }}>{fPct}%</span>
      </div>
    </div>
  )
}

function FirmRow({ firm, onClick, selected }) {
  const score = firm.score
  return (
    <tr
      onClick={() => onClick(firm)}
      style={{
        cursor: 'pointer',
        background: selected ? 'rgba(99,102,241,0.08)' : 'transparent',
        borderBottom: '1px solid var(--border, #2a2f3e)',
        transition: 'background 0.1s',
      }}
    >
      <td style={{ padding: '10px 12px' }}>
        <div style={{ fontWeight: 500, fontSize: 13,
                      color: 'var(--text-primary, #f1f5f9)' }}>{firm.name}</div>
        <div style={{ fontSize: 11, color: '#6b7280' }}>
          {firm.sector ?? '—'} · {firm.country ?? '—'}
        </div>
      </td>
      <td style={{ padding: '10px 12px', fontSize: 12, color: '#9ca3af' }}>
        <span style={{
          background: '#1e293b', padding: '2px 7px',
          borderRadius: 4, fontSize: 11,
        }}>{firm.stage ?? '—'}</span>
      </td>
      <td style={{ padding: '10px 12px', fontSize: 12, color: '#9ca3af' }}>
        {formatFunding(firm.total_funding_usd)}
      </td>
      <td style={{ padding: '10px 12px' }}>
        <ProbBar rise={score?.rise_prob} fall={score?.fall_prob} />
      </td>
      <td style={{ padding: '10px 12px', textAlign: 'right' }}>
        <span style={{
          fontSize: 14, fontWeight: 600,
          color: convictionColor(score?.conviction),
        }}>
          {score?.conviction ?? '—'}
        </span>
      </td>
      <td style={{ padding: '10px 12px', fontSize: 11, color: '#6b7280' }}>
        {score?.top_driver?.replace(/_/g, ' ') ?? '—'}
      </td>
    </tr>
  )
}

function FirmDetail({ firm, signals, onClose }) {
  const score = firm?.score
  if (!firm) return null

  const firmSignals = signals.filter(s => s.firm_id === firm.id).slice(0, 12)

  return (
    <div style={{
      position: 'fixed', inset: 0, background: 'rgba(0,0,0,0.6)',
      display: 'flex', alignItems: 'center', justifyContent: 'center',
      zIndex: 1000,
    }}
      onClick={onClose}
    >
      <div
        style={{
          background: '#0f1117', border: '1px solid #2a2f3e',
          borderRadius: 12, padding: 24, width: 560, maxHeight: '80vh',
          overflowY: 'auto',
        }}
        onClick={e => e.stopPropagation()}
      >
        {/* Header */}
        <div style={{ display: 'flex', justifyContent: 'space-between',
                      alignItems: 'flex-start', marginBottom: 20 }}>
          <div>
            <div style={{ fontSize: 18, fontWeight: 600,
                          color: '#f1f5f9' }}>{firm.name}</div>
            <div style={{ fontSize: 12, color: '#6b7280', marginTop: 3 }}>
              {firm.domain} · {firm.sector} · {firm.country}
            </div>
          </div>
          <button onClick={onClose} style={{
            background: 'none', border: 'none', color: '#6b7280',
            fontSize: 20, cursor: 'pointer', padding: '0 4px',
          }}>×</button>
        </div>

        {/* Score summary */}
        {score && (
          <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr 1fr',
                        gap: 10, marginBottom: 20 }}>
            <div style={{ background: '#1a1f2e', borderRadius: 8, padding: 12,
                          textAlign: 'center' }}>
              <div style={{ fontSize: 11, color: '#6b7280', marginBottom: 4 }}>Rise prob</div>
              <div style={{ fontSize: 20, fontWeight: 600, color: '#22c55e' }}>
                {Math.round((score.rise_prob ?? 0) * 100)}%
              </div>
            </div>
            <div style={{ background: '#1a1f2e', borderRadius: 8, padding: 12,
                          textAlign: 'center' }}>
              <div style={{ fontSize: 11, color: '#6b7280', marginBottom: 4 }}>Fall risk</div>
              <div style={{ fontSize: 20, fontWeight: 600, color: '#ef4444' }}>
                {Math.round((score.fall_prob ?? 0) * 100)}%
              </div>
            </div>
            <div style={{ background: '#1a1f2e', borderRadius: 8, padding: 12,
                          textAlign: 'center' }}>
              <div style={{ fontSize: 11, color: '#6b7280', marginBottom: 4 }}>Conviction</div>
              <div style={{ fontSize: 20, fontWeight: 600,
                            color: convictionColor(score.conviction) }}>
                {score.conviction}
              </div>
            </div>
          </div>
        )}

        {/* Top driver */}
        {score?.top_driver && (
          <div style={{ marginBottom: 16, fontSize: 12, color: '#9ca3af' }}>
            Top signal driver:{' '}
            <span style={{ color: '#818cf8', fontWeight: 500 }}>
              {score.top_driver.replace(/_/g, ' ')}
            </span>
          </div>
        )}

        {/* Funding */}
        <div style={{ display: 'flex', gap: 20, marginBottom: 20,
                      fontSize: 13, color: '#9ca3af' }}>
          <span>Total funding: <b style={{ color: '#f1f5f9' }}>
            {formatFunding(firm.total_funding_usd)}</b></span>
          <span>Stage: <b style={{ color: '#f1f5f9' }}>{firm.stage ?? '—'}</b></span>
          <span>Employees: <b style={{ color: '#f1f5f9' }}>
            {firm.employee_count?.toLocaleString() ?? '—'}</b></span>
        </div>

        {/* Signals */}
        {firmSignals.length > 0 && (
          <>
            <div style={{ fontSize: 12, color: '#6b7280', marginBottom: 8,
                          textTransform: 'uppercase', letterSpacing: '0.05em' }}>
              Recent signals
            </div>
            {firmSignals.map(s => (
              <div key={s.id} style={{
                display: 'flex', gap: 10, padding: '7px 0',
                borderBottom: '1px solid #1e293b', alignItems: 'flex-start',
              }}>
                <div style={{
                  width: 7, height: 7, borderRadius: '50%', marginTop: 5, flexShrink: 0,
                  background: s.value > 0 ? '#22c55e' : s.value < 0 ? '#ef4444' : '#6b7280',
                }} />
                <div style={{ flex: 1, fontSize: 12, color: '#9ca3af' }}>
                  <span style={{ color: '#c7d2fe', fontWeight: 500 }}>
                    {signalLabel(s.type)}
                  </span>
                  {s.value !== null && (
                    <span style={{ marginLeft: 6, color: s.value > 0 ? '#22c55e' : '#ef4444' }}>
                      {s.type === 'headcount_delta'
                        ? `${s.value > 0 ? '+' : ''}${(s.value * 100).toFixed(1)}%`
                        : s.type === 'funding_round'
                        ? formatFunding(s.value)
                        : s.value.toFixed(2)}
                    </span>
                  )}
                  {s.text && (
                    <div style={{ marginTop: 2, fontSize: 11, color: '#4b5563',
                                  whiteSpace: 'nowrap', overflow: 'hidden',
                                  textOverflow: 'ellipsis', maxWidth: 440 }}>
                      {s.text}
                    </div>
                  )}
                </div>
                <div style={{ fontSize: 10, color: '#374151', whiteSpace: 'nowrap' }}>
                  {new Date(s.captured_at).toLocaleDateString()}
                </div>
              </div>
            ))}
          </>
        )}

        {/* Crunchbase link */}
        {firm.crunchbase_url && (
          <a href={firm.crunchbase_url} target="_blank" rel="noreferrer"
            style={{ display: 'inline-block', marginTop: 16, fontSize: 12,
                     color: '#6366f1', textDecoration: 'none' }}>
            View on Crunchbase →
          </a>
        )}
      </div>
    </div>
  )
}

// ── Main page ─────────────────────────────────────────────────────────────

export default function Valuation() {
  const { firms, signals, loading, error, wsStatus } = useValuationEngine()
  const [selected, setSelected]   = useState(null)
  const [search,   setSearch]     = useState('')
  const [sector,   setSector]     = useState('All')
  const [sortKey,  setSortKey]    = useState('conviction')

  const sectors = ['All', ...Array.from(new Set(firms.map(f => f.sector).filter(Boolean))).sort()]

  const filtered = firms
    .filter(f => {
      const q = search.toLowerCase()
      const matchSearch = !q || f.name.toLowerCase().includes(q) ||
                          (f.sector ?? '').toLowerCase().includes(q)
      const matchSector = sector === 'All' || f.sector === sector
      return matchSearch && matchSector
    })
    .sort((a, b) => {
      if (sortKey === 'conviction') return (b.score?.conviction ?? 0) - (a.score?.conviction ?? 0)
      if (sortKey === 'rise')       return (b.score?.rise_prob ?? 0) - (a.score?.rise_prob ?? 0)
      if (sortKey === 'funding')    return (b.total_funding_usd ?? 0) - (a.total_funding_usd ?? 0)
      return a.name.localeCompare(b.name)
    })

  const highConviction = firms.filter(f => (f.score?.conviction ?? 0) >= 75).length
  const riseSignals    = firms.filter(f => (f.score?.rise_prob  ?? 0) >= 0.7).length
  const riskFlags      = firms.filter(f => (f.score?.fall_prob  ?? 0) >= 0.7).length

  if (loading) return (
    <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'center',
                  height: '60vh', color: '#6b7280', fontSize: 14 }}>
      Loading valuation engine…
    </div>
  )

  if (error) return (
    <div style={{ padding: 24, color: '#ef4444', fontSize: 13 }}>
      Backend error: {error}. Is VESTRO-BACKEND running?
    </div>
  )

  return (
    <div style={{ padding: 20, color: 'var(--text-primary, #f1f5f9)' }}>

      {/* Header */}
      <div style={{ display: 'flex', alignItems: 'center',
                    justifyContent: 'space-between', marginBottom: 20 }}>
        <div>
          <h1 style={{ fontSize: 20, fontWeight: 600, margin: 0 }}>
            Valuation Engine
          </h1>
          <p style={{ fontSize: 12, color: '#6b7280', margin: '4px 0 0' }}>
            {firms.length} private firms tracked · ML-scored in real-time
          </p>
        </div>
        <WsBadge status={wsStatus} />
      </div>

      {/* Stat cards */}
      <div style={{ display: 'grid', gridTemplateColumns: 'repeat(3, 1fr)',
                    gap: 12, marginBottom: 20 }}>
        <StatCard label="Firms tracked"     value={firms.length}    sub="across all sectors" />
        <StatCard label="High conviction"   value={highConviction}  color="#22c55e"
                  sub="conviction ≥ 75" />
        <StatCard label="Rise signals"      value={riseSignals}     color="#22c55e"
                  sub="rise prob ≥ 70%" />
      </div>

      {/* Filters */}
      <div style={{ display: 'flex', gap: 10, marginBottom: 16, flexWrap: 'wrap' }}>
        <input
          value={search}
          onChange={e => setSearch(e.target.value)}
          placeholder="Search firms…"
          style={{
            background: '#1a1f2e', border: '1px solid #2a2f3e',
            borderRadius: 6, padding: '7px 12px', fontSize: 13,
            color: '#f1f5f9', outline: 'none', width: 200,
          }}
        />
        <select
          value={sector}
          onChange={e => setSector(e.target.value)}
          style={{
            background: '#1a1f2e', border: '1px solid #2a2f3e',
            borderRadius: 6, padding: '7px 12px', fontSize: 13,
            color: '#9ca3af', outline: 'none',
          }}
        >
          {sectors.map(s => <option key={s}>{s}</option>)}
        </select>
        <select
          value={sortKey}
          onChange={e => setSortKey(e.target.value)}
          style={{
            background: '#1a1f2e', border: '1px solid #2a2f3e',
            borderRadius: 6, padding: '7px 12px', fontSize: 13,
            color: '#9ca3af', outline: 'none',
          }}
        >
          <option value="conviction">Sort: Conviction</option>
          <option value="rise">Sort: Rise prob</option>
          <option value="funding">Sort: Funding</option>
          <option value="name">Sort: Name</option>
        </select>
        <span style={{ fontSize: 12, color: '#6b7280',
                       alignSelf: 'center', marginLeft: 4 }}>
          {filtered.length} firms
        </span>
      </div>

      {/* Table */}
      <div style={{ background: '#0f1117', border: '1px solid #2a2f3e',
                    borderRadius: 10, overflow: 'hidden' }}>
        <table style={{ width: '100%', borderCollapse: 'collapse' }}>
          <thead>
            <tr style={{ background: '#0d1117', borderBottom: '1px solid #2a2f3e' }}>
              {['Firm', 'Stage', 'Funding', 'Rise / Fall', 'Conviction', 'Top driver'].map(h => (
                <th key={h} style={{
                  padding: '10px 12px', textAlign: 'left',
                  fontSize: 11, color: '#6b7280', fontWeight: 500,
                  textTransform: 'uppercase', letterSpacing: '0.05em',
                }}>{h}</th>
              ))}
            </tr>
          </thead>
          <tbody>
            {filtered.length === 0 ? (
              <tr><td colSpan={6} style={{ padding: 32, textAlign: 'center',
                                          color: '#4b5563', fontSize: 13 }}>
                No firms yet — backend is collecting data
              </td></tr>
            ) : (
              filtered.map(firm => (
                <FirmRow
                  key={firm.id}
                  firm={firm}
                  onClick={setSelected}
                  selected={selected?.id === firm.id}
                />
              ))
            )}
          </tbody>
        </table>
      </div>

      {/* Detail modal */}
      {selected && (
        <FirmDetail
          firm={selected}
          signals={signals}
          onClose={() => setSelected(null)}
        />
      )}
    </div>
  )
}