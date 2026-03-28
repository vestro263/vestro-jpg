import { useState, useEffect } from 'react'
import { useValuationEngine } from '../hooks/useValuationEngine'

function useIsMobile(bp = 640) {
  const [m, setM] = useState(() => window.innerWidth < bp)
  useEffect(() => {
    const h = () => setM(window.innerWidth < bp)
    window.addEventListener('resize', h)
    return () => window.removeEventListener('resize', h)
  }, [bp])
  return m
}

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
      fontSize: 11, color, padding: '3px 8px',
      border: `1px solid ${color}33`, borderRadius: 4,
    }}>
      <span style={{ width: 6, height: 6, borderRadius: '50%', background: color }} />
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
    <div style={{ display: 'flex', height: 14, borderRadius: 3, overflow: 'hidden', minWidth: 80 }}>
      <div style={{ width: `${rPct}%`, background: '#16a34a',
                    display: 'flex', alignItems: 'center', justifyContent: 'flex-end', paddingRight: 3 }}>
        <span style={{ fontSize: 9, color: '#dcfce7', fontWeight: 600 }}>{rPct}%</span>
      </div>
      <div style={{ width: `${fPct}%`, background: '#b91c1c',
                    display: 'flex', alignItems: 'center', paddingLeft: 3 }}>
        <span style={{ fontSize: 9, color: '#fee2e2', fontWeight: 600 }}>{fPct}%</span>
      </div>
    </div>
  )
}

// Mobile card view for a single firm
function FirmCard({ firm, onClick }) {
  const score = firm.score
  return (
    <div
      onClick={() => onClick(firm)}
      style={{
        background: '#111827', border: '1px solid #1f2937',
        borderRadius: 10, padding: '12px 14px', cursor: 'pointer',
      }}
    >
      <div style={{ display: 'flex', justifyContent: 'space-between', marginBottom: 8 }}>
        <div>
          <div style={{ fontWeight: 600, fontSize: 14, color: '#f1f5f9' }}>{firm.name}</div>
          <div style={{ fontSize: 11, color: '#6b7280', marginTop: 2 }}>
            {firm.sector ?? '—'} · {firm.country ?? '—'}
          </div>
        </div>
        <div style={{ textAlign: 'right' }}>
          <div style={{ fontSize: 18, fontWeight: 700, color: convictionColor(score?.conviction) }}>
            {score?.conviction ?? '—'}
          </div>
          <div style={{ fontSize: 10, color: '#6b7280' }}>conviction</div>
        </div>
      </div>
      <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center' }}>
        <div style={{ fontSize: 12, color: '#9ca3af' }}>{formatFunding(firm.total_funding_usd)}</div>
        <ProbBar rise={score?.rise_prob} fall={score?.fall_prob} />
      </div>
      {score?.top_driver && (
        <div style={{ marginTop: 6, fontSize: 11, color: '#6b7280' }}>
          Driver: <span style={{ color: '#818cf8' }}>{score.top_driver.replace(/_/g, ' ')}</span>
        </div>
      )}
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
        <div style={{ fontWeight: 500, fontSize: 13, color: 'var(--text-primary, #f1f5f9)' }}>{firm.name}</div>
        <div style={{ fontSize: 11, color: '#6b7280' }}>{firm.sector ?? '—'} · {firm.country ?? '—'}</div>
      </td>
      <td style={{ padding: '10px 12px', fontSize: 12, color: '#9ca3af' }}>
        <span style={{ background: '#1e293b', padding: '2px 7px', borderRadius: 4, fontSize: 11 }}>
          {firm.stage ?? '—'}
        </span>
      </td>
      <td style={{ padding: '10px 12px', fontSize: 12, color: '#9ca3af' }}>{formatFunding(firm.total_funding_usd)}</td>
      <td style={{ padding: '10px 12px' }}><ProbBar rise={score?.rise_prob} fall={score?.fall_prob} /></td>
      <td style={{ padding: '10px 12px', textAlign: 'right' }}>
        <span style={{ fontSize: 14, fontWeight: 600, color: convictionColor(score?.conviction) }}>
          {score?.conviction ?? '—'}
        </span>
      </td>
      <td style={{ padding: '10px 12px', fontSize: 11, color: '#6b7280' }}>
        {score?.top_driver?.replace(/_/g, ' ') ?? '—'}
      </td>
    </tr>
  )
}

function FirmDetail({ firm, signals, onClose, isMobile }) {
  const score = firm?.score
  if (!firm) return null
  const firmSignals = signals.filter(s => s.firm_id === firm.id).slice(0, 12)

  return (
    <div
      style={{
        position: 'fixed', inset: 0, background: 'rgba(0,0,0,0.7)',
        display: 'flex', alignItems: isMobile ? 'flex-end' : 'center',
        justifyContent: 'center', zIndex: 1000,
      }}
      onClick={onClose}
    >
      <div
        style={{
          background: '#0f1117', border: '1px solid #2a2f3e',
          borderRadius: isMobile ? '16px 16px 0 0' : 12,
          padding: isMobile ? '20px 16px' : 24,
          width: isMobile ? '100%' : 560,
          maxHeight: isMobile ? '85vh' : '80vh',
          overflowY: 'auto', WebkitOverflowScrolling: 'touch',
        }}
        onClick={e => e.stopPropagation()}
      >
        {/* drag handle on mobile */}
        {isMobile && (
          <div style={{ width: 36, height: 4, background: '#374151', borderRadius: 2,
                        margin: '0 auto 16px' }} />
        )}

        <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'flex-start', marginBottom: 20 }}>
          <div>
            <div style={{ fontSize: 18, fontWeight: 600, color: '#f1f5f9' }}>{firm.name}</div>
            <div style={{ fontSize: 12, color: '#6b7280', marginTop: 3 }}>
              {firm.domain} · {firm.sector} · {firm.country}
            </div>
          </div>
          <button onClick={onClose} style={{ background: 'none', border: 'none', color: '#6b7280', fontSize: 22, cursor: 'pointer', padding: '0 4px', lineHeight: 1 }}>×</button>
        </div>

        {score && (
          <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr 1fr', gap: 10, marginBottom: 20 }}>
            {[
              { label: 'Rise prob', value: `${Math.round((score.rise_prob ?? 0) * 100)}%`, color: '#22c55e' },
              { label: 'Fall risk', value: `${Math.round((score.fall_prob ?? 0) * 100)}%`, color: '#ef4444' },
              { label: 'Conviction', value: score.conviction, color: convictionColor(score.conviction) },
            ].map(({ label, value, color }) => (
              <div key={label} style={{ background: '#1a1f2e', borderRadius: 8, padding: 12, textAlign: 'center' }}>
                <div style={{ fontSize: 11, color: '#6b7280', marginBottom: 4 }}>{label}</div>
                <div style={{ fontSize: 20, fontWeight: 600, color }}>{value}</div>
              </div>
            ))}
          </div>
        )}

        {score?.top_driver && (
          <div style={{ marginBottom: 16, fontSize: 12, color: '#9ca3af' }}>
            Top driver:{' '}
            <span style={{ color: '#818cf8', fontWeight: 500 }}>{score.top_driver.replace(/_/g, ' ')}</span>
          </div>
        )}

        <div style={{ display: 'flex', flexWrap: 'wrap', gap: 16, marginBottom: 20, fontSize: 13, color: '#9ca3af' }}>
          <span>Funding: <b style={{ color: '#f1f5f9' }}>{formatFunding(firm.total_funding_usd)}</b></span>
          <span>Stage: <b style={{ color: '#f1f5f9' }}>{firm.stage ?? '—'}</b></span>
          <span>Employees: <b style={{ color: '#f1f5f9' }}>{firm.employee_count?.toLocaleString() ?? '—'}</b></span>
        </div>

        {firmSignals.length > 0 && (
          <>
            <div style={{ fontSize: 12, color: '#6b7280', marginBottom: 8, textTransform: 'uppercase', letterSpacing: '0.05em' }}>
              Recent signals
            </div>
            {firmSignals.map(s => (
              <div key={s.id} style={{ display: 'flex', gap: 10, padding: '7px 0', borderBottom: '1px solid #1e293b', alignItems: 'flex-start' }}>
                <div style={{ width: 7, height: 7, borderRadius: '50%', marginTop: 5, flexShrink: 0,
                              background: s.value > 0 ? '#22c55e' : s.value < 0 ? '#ef4444' : '#6b7280' }} />
                <div style={{ flex: 1, fontSize: 12, color: '#9ca3af' }}>
                  <span style={{ color: '#c7d2fe', fontWeight: 500 }}>{signalLabel(s.type)}</span>
                  {s.value !== null && (
                    <span style={{ marginLeft: 6, color: s.value > 0 ? '#22c55e' : '#ef4444' }}>
                      {s.type === 'headcount_delta' ? `${s.value > 0 ? '+' : ''}${(s.value * 100).toFixed(1)}%`
                        : s.type === 'funding_round' ? formatFunding(s.value)
                        : s.value.toFixed(2)}
                    </span>
                  )}
                  {s.text && (
                    <div style={{ marginTop: 2, fontSize: 11, color: '#4b5563', overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap', maxWidth: '100%' }}>
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

        {firm.crunchbase_url && (
          <a href={firm.crunchbase_url} target="_blank" rel="noreferrer"
            style={{ display: 'inline-block', marginTop: 16, fontSize: 12, color: '#6366f1', textDecoration: 'none' }}>
            View on Crunchbase →
          </a>
        )}
      </div>
    </div>
  )
}

export default function Valuation() {
  const { firms, signals, loading, error, wsStatus } = useValuationEngine()
  const [selected, setSelected] = useState(null)
  const [search,   setSearch]   = useState('')
  const [sector,   setSector]   = useState('All')
  const [sortKey,  setSortKey]  = useState('conviction')
  const isMobile = useIsMobile()

  const sectors = ['All', ...Array.from(new Set(firms.map(f => f.sector).filter(Boolean))).sort()]

  const filtered = firms
    .filter(f => {
      const q = search.toLowerCase()
      return (!q || f.name.toLowerCase().includes(q) || (f.sector ?? '').toLowerCase().includes(q))
          && (sector === 'All' || f.sector === sector)
    })
    .sort((a, b) => {
      if (sortKey === 'conviction') return (b.score?.conviction ?? 0) - (a.score?.conviction ?? 0)
      if (sortKey === 'rise')       return (b.score?.rise_prob  ?? 0) - (a.score?.rise_prob  ?? 0)
      if (sortKey === 'funding')    return (b.total_funding_usd ?? 0) - (a.total_funding_usd ?? 0)
      return a.name.localeCompare(b.name)
    })

  const highConviction = firms.filter(f => (f.score?.conviction ?? 0) >= 75).length
  const riseSignals    = firms.filter(f => (f.score?.rise_prob  ?? 0) >= 0.7).length

  if (loading) return (
    <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'center',
                  height: '60vh', color: '#6b7280', fontSize: 14 }}>
      Loading valuation engine…
    </div>
  )

  if (error) return (
    <div style={{ padding: 20, color: '#ef4444', fontSize: 13 }}>
      Backend error: {error}. Is VESTRO-BACKEND running?
    </div>
  )

  return (
    <div style={{ padding: isMobile ? 14 : 20, color: 'var(--text-primary, #f1f5f9)', display: 'flex', flexDirection: 'column', gap: 16 }}>

      {/* Header */}
      <div style={{ display: 'flex', alignItems: 'flex-start', justifyContent: 'space-between', gap: 8 }}>
        <div>
          <h1 style={{ fontSize: isMobile ? 16 : 20, fontWeight: 600, margin: 0 }}>Valuation Engine</h1>
          <p style={{ fontSize: 12, color: '#6b7280', margin: '4px 0 0' }}>
            {firms.length} firms tracked · ML-scored in real-time
          </p>
        </div>
        <WsBadge status={wsStatus} />
      </div>

      {/* Stat cards */}
      <div style={{
        display: 'grid',
        gridTemplateColumns: isMobile ? 'repeat(2,1fr)' : 'repeat(3,1fr)',
        gap: 10,
      }}>
        <StatCard label="Firms tracked"   value={firms.length}    sub="across all sectors" />
        <StatCard label="High conviction" value={highConviction}  color="#22c55e" sub="conviction ≥ 75" />
        <StatCard label="Rise signals"    value={riseSignals}     color="#22c55e" sub="rise prob ≥ 70%" />
      </div>

      {/* Filters — stack on mobile */}
      <div style={{ display: 'flex', flexDirection: isMobile ? 'column' : 'row', gap: 8 }}>
        <input
          value={search}
          onChange={e => setSearch(e.target.value)}
          placeholder="Search firms…"
          style={{
            background: '#1a1f2e', border: '1px solid #2a2f3e',
            borderRadius: 6, padding: '8px 12px', fontSize: 13,
            color: '#f1f5f9', outline: 'none',
            width: isMobile ? '100%' : 200,
            boxSizing: 'border-box',
            minHeight: 38,
          }}
        />
        <div style={{ display: 'flex', gap: 8 }}>
          <select value={sector} onChange={e => setSector(e.target.value)} style={{
            background: '#1a1f2e', border: '1px solid #2a2f3e',
            borderRadius: 6, padding: '8px 12px', fontSize: 13,
            color: '#9ca3af', outline: 'none', flex: 1, minHeight: 38,
          }}>
            {sectors.map(s => <option key={s}>{s}</option>)}
          </select>
          <select value={sortKey} onChange={e => setSortKey(e.target.value)} style={{
            background: '#1a1f2e', border: '1px solid #2a2f3e',
            borderRadius: 6, padding: '8px 12px', fontSize: 13,
            color: '#9ca3af', outline: 'none', flex: 1, minHeight: 38,
          }}>
            <option value="conviction">Conviction</option>
            <option value="rise">Rise prob</option>
            <option value="funding">Funding</option>
            <option value="name">Name</option>
          </select>
        </div>
        <span style={{ fontSize: 12, color: '#6b7280', alignSelf: 'center' }}>
          {filtered.length} firms
        </span>
      </div>

      {/* Firm list — cards on mobile, table on desktop */}
      {isMobile ? (
        <div style={{ display: 'flex', flexDirection: 'column', gap: 10 }}>
          {filtered.length === 0 ? (
            <div style={{ padding: 32, textAlign: 'center', color: '#4b5563', fontSize: 13 }}>
              No firms yet — backend is collecting data
            </div>
          ) : (
            filtered.map(firm => (
              <FirmCard key={firm.id} firm={firm} onClick={setSelected} />
            ))
          )}
        </div>
      ) : (
        <div style={{ background: '#0f1117', border: '1px solid #2a2f3e', borderRadius: 10, overflow: 'hidden' }}>
          <table style={{ width: '100%', borderCollapse: 'collapse' }}>
            <thead>
              <tr style={{ background: '#0d1117', borderBottom: '1px solid #2a2f3e' }}>
                {['Firm','Stage','Funding','Rise / Fall','Conviction','Top driver'].map(h => (
                  <th key={h} style={{ padding: '10px 12px', textAlign: 'left', fontSize: 11, color: '#6b7280', fontWeight: 500, textTransform: 'uppercase', letterSpacing: '0.05em' }}>{h}</th>
                ))}
              </tr>
            </thead>
            <tbody>
              {filtered.length === 0 ? (
                <tr><td colSpan={6} style={{ padding: 32, textAlign: 'center', color: '#4b5563', fontSize: 13 }}>
                  No firms yet — backend is collecting data
                </td></tr>
              ) : (
                filtered.map(firm => (
                  <FirmRow key={firm.id} firm={firm} onClick={setSelected} selected={selected?.id === firm.id} />
                ))
              )}
            </tbody>
          </table>
        </div>
      )}

      {selected && (
        <FirmDetail firm={selected} signals={signals} onClose={() => setSelected(null)} isMobile={isMobile} />
      )}
    </div>
  )
}