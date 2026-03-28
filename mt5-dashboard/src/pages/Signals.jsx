import { useState } from 'react'
import useBotStore from '../store/botStore'
import { S, StatCard, DirectionBadge, ATRZoneBadge, TSSBar, Empty } from '../components/ui'

function useIsMobile(bp = 640) {
  const [m, setM] = useState(() => window.innerWidth < bp)
  // effect omitted — Signals doesn't need resize reactivity at component level
  return m
}

export default function Signals() {
  const { signals } = useBotStore()
  const [filter, setFilter] = useState('all')
  const isMobile = useIsMobile()

  const filtered = signals.filter(s => {
    if (filter === 'all')  return true
    if (filter === 'buy')  return s.signal?.direction ===  1
    if (filter === 'sell') return s.signal?.direction === -1
    if (filter === 'flat') return s.signal?.direction ===  0
    return true
  })

  const buys  = signals.filter(s => s.signal?.direction ===  1).length
  const sells = signals.filter(s => s.signal?.direction === -1).length
  const flats = signals.filter(s => s.signal?.direction ===  0).length
  const avgTSS = signals.length
    ? (signals.reduce((a, s) => a + (s.signal?.tss_score || 0), 0) / signals.length).toFixed(3)
    : '—'

  const grid4 = {
    display: 'grid',
    gridTemplateColumns: isMobile ? 'repeat(2,minmax(0,1fr))' : 'repeat(4,minmax(0,1fr))',
    gap: 10,
  }

  const FilterBtn = ({ val, label }) => (
    <button
      onClick={() => setFilter(val)}
      style={{
        padding: '6px 12px', borderRadius: 7, border: 'none',
        cursor: 'pointer', fontSize: 12, minHeight: 34,
        fontWeight: filter === val ? 600 : 400,
        background: filter === val ? '#1f2937' : 'transparent',
        color: filter === val ? '#f1f5f9' : '#6b7280',
        transition: 'all 0.15s', whiteSpace: 'nowrap',
      }}
    >
      {label}
    </button>
  )

  return (
    <div style={S.page}>

      {/* Stats */}
      <div style={grid4}>
        <StatCard label="Total Signals" value={signals.length}  color="#93c5fd" />
        <StatCard label="Buy Signals"   value={buys}            color="#4ade80"
          sub={`${signals.length ? ((buys/signals.length)*100).toFixed(0) : 0}% of total`} />
        <StatCard label="Sell Signals"  value={sells}           color="#f87171"
          sub={`${signals.length ? ((sells/signals.length)*100).toFixed(0) : 0}% of total`} />
        <StatCard label="Avg TSS Score" value={avgTSS}          color="#fbbf24" />
      </div>

      {/* Table card */}
      <div style={S.card}>

        {/* Header + filter — stacks on mobile */}
        <div style={{
          display: 'flex', flexWrap: 'wrap', alignItems: 'center',
          gap: 8, marginBottom: 14,
        }}>
          <span style={S.h3}>Signal History</span>

          {/* Filter pills — scrollable on tiny screens */}
          <div style={{
            marginLeft: isMobile ? 0 : 'auto',
            width: isMobile ? '100%' : 'auto',
            overflowX: 'auto', WebkitOverflowScrolling: 'touch',
          }}>
            <div style={{
              display: 'inline-flex', gap: 4,
              background: '#0b1120', borderRadius: 8,
              padding: 3, whiteSpace: 'nowrap',
            }}>
              <FilterBtn val="all"  label={`All (${signals.length})`} />
              <FilterBtn val="buy"  label={`Buy (${buys})`} />
              <FilterBtn val="sell" label={`Sell (${sells})`} />
              <FilterBtn val="flat" label={`Flat (${flats})`} />
            </div>
          </div>
        </div>

        {filtered.length === 0 ? (
          <Empty icon="📡" text="No signals yet — waiting for the bot…" />
        ) : (
          <div style={{ overflowX: 'auto', WebkitOverflowScrolling: 'touch' }}>
            <table style={{ width: '100%', borderCollapse: 'collapse', minWidth: 720 }}>
              <thead>
                <tr>
                  {['Time','Symbol','Direction','ATR Zone','TSS','RSI','ADX','ATR','MACD Hist','EMA50','EMA200'].map(h => (
                    <th key={h} style={S.th}>{h}</th>
                  ))}
                </tr>
              </thead>
              <tbody>
                {filtered.map(entry => {
                  const sig = entry.signal || {}
                  return (
                    <tr key={entry.id} style={{ transition: 'background 0.1s' }}
                      onMouseEnter={e => e.currentTarget.style.background = '#1f2937'}
                      onMouseLeave={e => e.currentTarget.style.background = 'transparent'}
                    >
                      <td style={S.td}>{entry.receivedAt}</td>
                      <td style={{ ...S.td, fontWeight: 600, color: '#e5e7eb' }}>{entry.symbol || '—'}</td>
                      <td style={S.td}><DirectionBadge direction={sig.direction} /></td>
                      <td style={S.td}><ATRZoneBadge zone={sig.atr_zone} /></td>
                      <td style={{ ...S.td, minWidth: 110 }}><TSSBar score={sig.tss_score || 0} /></td>
                      <td style={S.td}>{(sig.rsi      || 0).toFixed(2)}</td>
                      <td style={S.td}>{(sig.adx      || 0).toFixed(2)}</td>
                      <td style={S.td}>{(sig.atr      || 0).toFixed(5)}</td>
                      <td style={{ ...S.td, color: (sig.macd_hist || 0) >= 0 ? '#4ade80' : '#f87171' }}>
                        {(sig.macd_hist || 0).toFixed(5)}
                      </td>
                      <td style={S.td}>{(sig.ema50  || 0).toFixed(4)}</td>
                      <td style={S.td}>{(sig.ema200 || 0).toFixed(4)}</td>
                    </tr>
                  )
                })}
              </tbody>
            </table>
          </div>
        )}
      </div>
    </div>
  )
}