import { useEffect, useState, useCallback } from 'react'
import {
  BarChart, Bar, XAxis, YAxis, Tooltip,
  ResponsiveContainer, CartesianGrid, Cell,
} from 'recharts'
import { S, StatCard, Empty } from '../components/ui'
import useBotStore from '../store/botStore'

const API = 'https://vestro-jpg.onrender.com'

// ── Helpers ───────────────────────────────────────────────────────────────────

function OutcomeBadge({ outcome }) {
  const map = {
    WIN:     { bg: '#052e16', border: '#166534', color: '#4ade80', label: 'WIN' },
    LOSS:    { bg: '#1f1217', border: '#7f1d1d', color: '#f87171', label: 'LOSS' },
    NEUTRAL: { bg: '#1c1a08', border: '#713f12', color: '#fbbf24', label: 'NEUTRAL' },
  }
  const s = map[outcome] ?? { bg: '#111827', border: '#1f2937', color: '#4b5563', label: 'OPEN' }
  return (
    <span style={{
      fontSize: 10, fontWeight: 700, padding: '2px 7px', borderRadius: 5,
      background: s.bg, border: `1px solid ${s.border}`, color: s.color,
      letterSpacing: '0.04em',
    }}>
      {s.label}
    </span>
  )
}

function SignalBadge({ signal }) {
  const isBuy = signal === 'BUY'
  return (
    <span style={{
      fontSize: 10, fontWeight: 700, padding: '2px 7px', borderRadius: 5,
      background: isBuy ? '#052e16' : '#1f1217',
      border: `1px solid ${isBuy ? '#166534' : '#7f1d1d'}`,
      color: isBuy ? '#4ade80' : '#f87171',
    }}>
      {isBuy ? '▲ BUY' : '▼ SELL'}
    </span>
  )
}

function ConfBar({ value }) {
  const pct   = Math.round(value * 100)
  const color = value >= 0.75 ? '#4ade80' : value >= 0.65 ? '#fbbf24' : '#38bdf8'
  return (
    <div style={{ display: 'flex', alignItems: 'center', gap: 6 }}>
      <div style={{ width: 56, height: 4, background: '#1f2937', borderRadius: 2, overflow: 'hidden' }}>
        <div style={{ width: `${pct}%`, height: '100%', background: color, borderRadius: 2 }} />
      </div>
      <span style={{ fontSize: 11, color: '#9ca3af', minWidth: 28 }}>{pct}%</span>
    </div>
  )
}

// ── Main ──────────────────────────────────────────────────────────────────────

export default function Performance() {
  const accountId = useBotStore(s => s.accountId)
  const isDemo    = accountId?.startsWith('VRT') ?? false

  const [execData,    setExecData]    = useState(null)
  const [execLoading, setExecLoading] = useState(true)
  const [execError,   setExecError]   = useState(null)
  const [minConf,     setMinConf]     = useState(0.60)
  const [strategy,    setStrategy]    = useState('ALL')
  const [signalFlt,   setSignalFlt]   = useState('ALL')
  const [lastFetched, setLastFetched] = useState(null)

  const fetchExec = useCallback(async () => {
    if (!accountId) return
    setExecLoading(true)
    setExecError(null)
    try {
      const params = new URLSearchParams({
        min_confidence: minConf,
        account_id:     accountId,       // ← scope to active account
      })
      if (strategy  !== 'ALL') params.set('strategy', strategy)
      if (signalFlt !== 'ALL') params.set('signal',   signalFlt)

      const res  = await fetch(`${API}/debug/execution-window?${params}`)
      if (!res.ok) throw new Error(`HTTP ${res.status}`)
      const data = await res.json()
      setExecData(data)
      setLastFetched(new Date().toLocaleTimeString())
    } catch (e) {
      setExecError(e.message)
    } finally {
      setExecLoading(false)
    }
  }, [minConf, strategy, signalFlt, accountId])

  // Re-fetch whenever accountId or filters change
  useEffect(() => { fetchExec() }, [fetchExec])

  // Auto-refresh every 30s
  useEffect(() => {
    const id = setInterval(fetchExec, 30_000)
    return () => clearInterval(id)
  }, [fetchExec])

  // ── Derived ───────────────────────────────────────────────────────────────
  const signals  = execData?.signals  ?? []
  const wins     = execData?.wins     ?? 0
  const losses   = execData?.losses   ?? 0
  const open     = execData?.open     ?? 0
  const winRate  = execData?.win_rate ?? null
  const total    = execData?.total    ?? 0

  const buckets  = [0.60, 0.65, 0.70, 0.75, 0.80, 0.85, 0.90, 0.95]
  const confDist = buckets.map((b, i) => {
    const next  = buckets[i + 1] ?? 1.01
    const count = signals.filter(s => s.confidence >= b && s.confidence < next).length
    return { label: `${Math.round(b * 100)}`, count }
  })

  const byStrategy = ['V75', 'Crash500'].map(strat => {
    const rows   = signals.filter(s => s.strategy === strat)
    const w      = rows.filter(s => s.outcome === 'WIN').length
    const l      = rows.filter(s => s.outcome === 'LOSS').length
    const closed = w + l
    return { strat, wins: w, losses: l, wr: closed ? Math.round((w / closed) * 100) : null }
  })

  const selStyle = {
    background: '#1f2937', border: '1px solid #374151',
    borderRadius: 7, color: '#e5e7eb', fontSize: 12,
    padding: '6px 10px', outline: 'none',
  }

  // No account selected guard
  if (!accountId) {
    return (
      <div style={S.page}>
        <div style={{ padding: '48px 0', textAlign: 'center', color: '#4b5563', fontSize: 13 }}>
          Select an account to view performance data
        </div>
      </div>
    )
  }

  return (
    <div style={S.page}>

      {/* ── Section header ── */}
      <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', flexWrap: 'wrap', gap: 8 }}>
        <div>
          <div style={{ fontSize: 15, fontWeight: 700, color: '#f1f5f9' }}>
            Execution Tracker
            <span style={{
              marginLeft: 10, fontSize: 10, fontWeight: 700,
              padding: '2px 7px', borderRadius: 4,
              background: isDemo ? '#1e3a5f' : '#14532d',
              color:      isDemo ? '#60a5fa' : '#4ade80',
            }}>
              {isDemo ? 'DEMO' : 'REAL'} · {accountId}
            </span>
          </div>
          <div style={{ fontSize: 11, color: '#4b5563', marginTop: 2 }}>
            Signals above confidence threshold — actual WIN / LOSS outcomes
            {lastFetched && <span style={{ marginLeft: 8 }}>· refreshed {lastFetched}</span>}
          </div>
        </div>
        <button onClick={fetchExec} disabled={execLoading} style={{
          padding: '6px 14px', borderRadius: 7, border: '1px solid #1f2937',
          background: 'transparent', color: '#6b7280', fontSize: 12, cursor: 'pointer',
        }}>
          {execLoading ? '↻ Loading…' : '↻ Refresh'}
        </button>
      </div>

      {/* ── Filters ── */}
      <div style={{
        display: 'flex', flexWrap: 'wrap', gap: 10, alignItems: 'center',
        background: '#111827', border: '1px solid #1f2937', borderRadius: 10, padding: '10px 14px',
      }}>
        <div>
          <div style={{ fontSize: 10, color: '#4b5563', marginBottom: 4 }}>Min confidence</div>
          <select style={selStyle} value={minConf} onChange={e => setMinConf(parseFloat(e.target.value))}>
            {[0.55, 0.60, 0.65, 0.70, 0.75, 0.80].map(v => (
              <option key={v} value={v}>{Math.round(v * 100)}%</option>
            ))}
          </select>
        </div>
        <div>
          <div style={{ fontSize: 10, color: '#4b5563', marginBottom: 4 }}>Strategy</div>
          <select style={selStyle} value={strategy} onChange={e => setStrategy(e.target.value)}>
            <option value="ALL">All</option>
            <option value="V75">V75</option>
            <option value="Crash500">Crash500</option>
          </select>
        </div>
        <div>
          <div style={{ fontSize: 10, color: '#4b5563', marginBottom: 4 }}>Signal</div>
          <select style={selStyle} value={signalFlt} onChange={e => setSignalFlt(e.target.value)}>
            <option value="ALL">All</option>
            <option value="BUY">BUY</option>
            <option value="SELL">SELL</option>
          </select>
        </div>
      </div>

      {/* ── KPI cards ── */}
      <div style={S.grid4}>
        <StatCard label="Executed"   value={total}  color="#93c5fd" />
        <StatCard label="Wins"       value={wins}   color="#4ade80" />
        <StatCard label="Losses"     value={losses} color="#f87171" />
        <StatCard
          label="Win Rate"
          value={winRate !== null ? `${Math.round(winRate * 100)}%` : '—'}
          color={winRate === null ? '#4b5563' : winRate >= 0.55 ? '#4ade80' : winRate >= 0.48 ? '#fbbf24' : '#f87171'}
          sub={`${open} open / pending`}
        />
      </div>

      {/* ── Win rate bar ── */}
      {winRate !== null && (
        <div style={{ background: '#111827', border: '1px solid #1f2937', borderRadius: 10, padding: '12px 16px' }}>
          <div style={{ display: 'flex', justifyContent: 'space-between', marginBottom: 8 }}>
            <span style={{ fontSize: 12, color: '#6b7280' }}>Win rate vs loss rate</span>
            <span style={{ fontSize: 12, fontWeight: 700, color: winRate >= 0.5 ? '#4ade80' : '#f87171' }}>
              {Math.round(winRate * 100)}% / {Math.round((1 - winRate) * 100)}%
            </span>
          </div>
          <div style={{ height: 8, background: '#1f2937', borderRadius: 4, overflow: 'hidden', display: 'flex' }}>
            <div style={{ width: `${winRate * 100}%`, background: '#16a34a', transition: 'width 0.5s ease' }} />
            <div style={{ flex: 1, background: '#dc2626' }} />
          </div>
          <div style={{ display: 'flex', justifyContent: 'space-between', marginTop: 6 }}>
            <span style={{ fontSize: 10, color: '#16a34a' }}>▲ {wins} wins</span>
            <span style={{ fontSize: 10, color: '#dc2626' }}>{losses} losses ▼</span>
          </div>
        </div>
      )}

      {/* ── Strategy breakdown ── */}
      <div style={{ display: 'grid', gridTemplateColumns: 'repeat(2, minmax(0,1fr))', gap: 10 }}>
        {byStrategy.map(({ strat, wins: w, losses: l, wr }) => (
          <div key={strat} style={{
            background: '#111827', border: '1px solid #1f2937',
            borderRadius: 10, padding: '12px 14px',
          }}>
            <div style={{ fontSize: 12, fontWeight: 600, color: '#e5e7eb', marginBottom: 8 }}>{strat}</div>
            <div style={{ display: 'flex', gap: 12, marginBottom: 8 }}>
              <div>
                <div style={{ fontSize: 10, color: '#4b5563' }}>W</div>
                <div style={{ fontSize: 18, fontWeight: 700, color: '#4ade80' }}>{w}</div>
              </div>
              <div>
                <div style={{ fontSize: 10, color: '#4b5563' }}>L</div>
                <div style={{ fontSize: 18, fontWeight: 700, color: '#f87171' }}>{l}</div>
              </div>
              <div style={{ marginLeft: 'auto', textAlign: 'right' }}>
                <div style={{ fontSize: 10, color: '#4b5563' }}>Rate</div>
                <div style={{ fontSize: 18, fontWeight: 700, color: wr === null ? '#4b5563' : wr >= 55 ? '#4ade80' : wr >= 48 ? '#fbbf24' : '#f87171' }}>
                  {wr !== null ? `${wr}%` : '—'}
                </div>
              </div>
            </div>
            {wr !== null && (
              <div style={{ height: 4, background: '#1f2937', borderRadius: 2, overflow: 'hidden' }}>
                <div style={{ width: `${wr}%`, height: '100%', background: wr >= 55 ? '#16a34a' : wr >= 48 ? '#ca8a04' : '#dc2626', transition: 'width 0.4s' }} />
              </div>
            )}
          </div>
        ))}
      </div>

      {/* ── Confidence distribution chart ── */}
      {signals.length > 0 && (
        <div style={{ background: '#111827', border: '1px solid #1f2937', borderRadius: 10, padding: '14px 16px' }}>
          <div style={S.h3}>Confidence distribution (executed only)</div>
          <ResponsiveContainer width="100%" height={140}>
            <BarChart data={confDist} barSize={28}>
              <CartesianGrid stroke="#1f2937" vertical={false} />
              <XAxis dataKey="label" tick={{ fontSize: 10, fill: '#6b7280' }} tickLine={false} axisLine={false}
                tickFormatter={v => `${v}%`} />
              <YAxis tick={{ fontSize: 10, fill: '#6b7280' }} tickLine={false} axisLine={false} width={28} allowDecimals={false} />
              <Tooltip
                contentStyle={{ background: '#0b1120', border: '1px solid #1f2937', borderRadius: 8, fontSize: 12 }}
                labelStyle={{ color: '#6b7280' }}
                formatter={v => [v, 'Signals']}
                labelFormatter={v => `${v}–${parseInt(v) + 5}%`}
              />
              <Bar dataKey="count" radius={[3, 3, 0, 0]}>
                {confDist.map((entry, i) => (
                  <Cell key={i} fill={entry.count === 0 ? '#1f2937' : '#38bdf8'} />
                ))}
              </Bar>
            </BarChart>
          </ResponsiveContainer>
        </div>
      )}

      {/* ── Signal table ── */}
      <div style={{ background: '#111827', border: '1px solid #1f2937', borderRadius: 10, padding: '14px 16px' }}>
        <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', marginBottom: 12 }}>
          <div style={S.h3}>
            Executed signals
            <span style={{ marginLeft: 8, fontSize: 11, fontWeight: 400, color: '#4b5563' }}>
              confidence ≥ {Math.round(minConf * 100)}%
            </span>
          </div>
          <span style={{ fontSize: 11, color: '#4b5563' }}>{signals.length} rows</span>
        </div>

        {execError && (
          <div style={{ padding: '12px 14px', background: '#1f1217', border: '1px solid #7f1d1d', borderRadius: 8, fontSize: 12, color: '#f87171', marginBottom: 12 }}>
            ✗ {execError} — showing cached data if available
          </div>
        )}

        {signals.length === 0 && !execLoading ? (
          <div style={{ padding: '32px 0', textAlign: 'center', color: '#4b5563', fontSize: 12 }}>
            No signals above {Math.round(minConf * 100)}% confidence for {accountId}
          </div>
        ) : (
          <div style={{ overflowX: 'auto', WebkitOverflowScrolling: 'touch' }}>
            <table style={{ width: '100%', borderCollapse: 'collapse', minWidth: 480 }}>
              <thead>
                <tr>
                  {['Time', 'Strategy', 'Signal', 'Confidence', 'Entry', 'Outcome'].map(h => (
                    <th key={h} style={{
                      padding: '6px 10px', fontSize: 10, fontWeight: 600,
                      color: '#4b5563', textTransform: 'uppercase', letterSpacing: '0.05em',
                      borderBottom: '1px solid #1f2937', textAlign: 'left',
                    }}>{h}</th>
                  ))}
                </tr>
              </thead>
              <tbody>
                {signals.map((s, i) => (
                  <tr key={s.id ?? i}
                    style={{ transition: 'background 0.1s' }}
                    onMouseEnter={e => e.currentTarget.style.background = '#1f2937'}
                    onMouseLeave={e => e.currentTarget.style.background = 'transparent'}
                  >
                    <td style={{ padding: '8px 10px', fontSize: 11, color: '#6b7280', borderBottom: '1px solid #111827', whiteSpace: 'nowrap' }}>
                      {(s.captured_at ?? '').slice(11, 19)}
                    </td>
                    <td style={{ padding: '8px 10px', fontSize: 12, color: '#d1d5db', borderBottom: '1px solid #111827' }}>
                      {s.strategy}
                    </td>
                    <td style={{ padding: '8px 10px', borderBottom: '1px solid #111827' }}>
                      <SignalBadge signal={s.signal} />
                    </td>
                    <td style={{ padding: '8px 10px', borderBottom: '1px solid #111827' }}>
                      <ConfBar value={s.confidence ?? 0} />
                    </td>
                    <td style={{ padding: '8px 10px', fontSize: 11, color: '#9ca3af', borderBottom: '1px solid #111827', fontFamily: 'monospace' }}>
                      {s.entry_price ? s.entry_price.toFixed(4) : '—'}
                    </td>
                    <td style={{ padding: '8px 10px', borderBottom: '1px solid #111827' }}>
                      <OutcomeBadge outcome={s.outcome} />
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </div>

    </div>
  )
}