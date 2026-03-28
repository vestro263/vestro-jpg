import { useEffect, useState } from 'react'
import useBotStore from '../store/botStore'
import { S, StatCard, DirectionBadge, Empty } from '../components/ui'

function useIsMobile(bp = 640) {
  const [m, setM] = useState(() => window.innerWidth < bp)
  useEffect(() => {
    const h = () => setM(window.innerWidth < bp)
    window.addEventListener('resize', h)
    return () => window.removeEventListener('resize', h)
  }, [bp])
  return m
}

export default function Journal() {
  const { journal, journalLoading, fetchJournal } = useBotStore()
  const [limit, setLimit] = useState(50)
  const isMobile = useIsMobile()

  useEffect(() => { fetchJournal(limit) }, [limit])

  const closed    = journal.filter(t => t.profit !== undefined)
  const wins      = closed.filter(t => (t.profit || 0) > 0)
  const losses    = closed.filter(t => (t.profit || 0) < 0)
  const netProfit = closed.reduce((s, t) => s + (t.profit || 0), 0)
  const winRate   = closed.length ? ((wins.length / closed.length) * 100).toFixed(1) : '—'
  const avgWin    = wins.length   ? (wins.reduce((s, t)   => s + t.profit, 0) / wins.length).toFixed(2)   : '0.00'
  const avgLoss   = losses.length ? (losses.reduce((s, t) => s + t.profit, 0) / losses.length).toFixed(2) : '0.00'

  const grid4 = {
    display: 'grid',
    gridTemplateColumns: isMobile ? 'repeat(2,minmax(0,1fr))' : 'repeat(4,minmax(0,1fr))',
    gap: 10,
  }

  return (
    <div style={S.page}>

      {/* Stats */}
      <div style={grid4}>
        <StatCard label="Closed Trades" value={closed.length} color="#93c5fd" />
        <StatCard
          label="Net P&L"
          value={`${netProfit >= 0 ? '+' : ''}$${netProfit.toFixed(2)}`}
          color={netProfit >= 0 ? '#4ade80' : '#f87171'}
        />
        <StatCard label="Win Rate" value={`${winRate}%`} color="#fbbf24"
          sub={`${wins.length}W / ${losses.length}L`} />
        <StatCard label="Avg Win / Loss" value={`$${avgWin} / $${avgLoss}`} color="#e5e7eb" />
      </div>

      {/* Table card */}
      <div style={S.card}>

        {/* Header row — wraps on mobile */}
        <div style={{
          display: 'flex', flexWrap: 'wrap', alignItems: 'center',
          gap: 8, marginBottom: 14,
        }}>
          <span style={S.h3}>Trade Journal</span>

          <div style={{
            marginLeft: 'auto', display: 'flex', flexWrap: 'wrap',
            gap: 4, alignItems: 'center',
          }}>
            <span style={{ fontSize: 11, color: '#6b7280' }}>Show</span>
            {[25, 50, 100, 200].map(n => (
              <button key={n} onClick={() => setLimit(n)} style={{
                padding: '4px 10px', borderRadius: 6, border: 'none',
                cursor: 'pointer', fontSize: 11, minHeight: 32,
                background: limit === n ? '#1f2937' : 'transparent',
                color: limit === n ? '#f1f5f9' : '#6b7280',
              }}>{n}</button>
            ))}
            <button onClick={() => fetchJournal(limit)} style={{
              padding: '4px 12px', borderRadius: 7, border: '1px solid #1f2937',
              background: 'transparent', color: '#6b7280', fontSize: 11,
              cursor: 'pointer', marginLeft: 4, minHeight: 32,
            }}>↻ Refresh</button>
          </div>
        </div>

        {journalLoading ? (
          <div style={{ padding: '32px 0', textAlign: 'center', color: '#4b5563', fontSize: 12 }}>
            Loading journal…
          </div>
        ) : closed.length === 0 ? (
          <Empty icon="📓" text="No closed trades yet" />
        ) : (
          <div style={{ overflowX: 'auto', WebkitOverflowScrolling: 'touch' }}>
            <table style={{ width: '100%', borderCollapse: 'collapse', minWidth: 700 }}>
              <thead>
                <tr>
                  {['Ticket','Symbol','Type','Volume','Open Price','Close Price','Open Time','Close Time','Swap','Commission','P&L'].map(h => (
                    <th key={h} style={S.th}>{h}</th>
                  ))}
                </tr>
              </thead>
              <tbody>
                {closed.map((t, i) => {
                  const profit = t.profit || 0
                  return (
                    <tr key={t.ticket || i}
                      onMouseEnter={e => e.currentTarget.style.background = '#1f2937'}
                      onMouseLeave={e => e.currentTarget.style.background = 'transparent'}
                      style={{ transition: 'background 0.1s' }}
                    >
                      <td style={{ ...S.td, color: '#6b7280', fontFamily: 'monospace' }}>{t.ticket || '—'}</td>
                      <td style={{ ...S.td, fontWeight: 600, color: '#e5e7eb' }}>{t.symbol || '—'}</td>
                      <td style={S.td}><DirectionBadge direction={t.type} /></td>
                      <td style={S.td}>{(t.volume || 0).toFixed(2)}</td>
                      <td style={{ ...S.td, fontFamily: 'monospace' }}>{(t.open_price  || t.price_open  || 0).toFixed(5)}</td>
                      <td style={{ ...S.td, fontFamily: 'monospace' }}>{(t.close_price || t.price_close || 0).toFixed(5)}</td>
                      <td style={{ ...S.td, color: '#6b7280' }}>{t.open_time  || '—'}</td>
                      <td style={{ ...S.td, color: '#6b7280' }}>{t.close_time || '—'}</td>
                      <td style={S.td}>{(t.swap       || 0).toFixed(2)}</td>
                      <td style={S.td}>{(t.commission || 0).toFixed(2)}</td>
                      <td style={{ ...S.td, fontWeight: 600, color: profit >= 0 ? '#4ade80' : '#f87171' }}>
                        {profit >= 0 ? '+' : ''}${profit.toFixed(2)}
                      </td>
                    </tr>
                  )
                })}
              </tbody>
            </table>

            <div style={{
              padding: '10px 12px', borderTop: '1px solid #1f2937',
              display: 'flex', justifyContent: 'flex-end', gap: 8, fontSize: 12,
              flexWrap: 'wrap',
            }}>
              <span style={{ color: '#6b7280' }}>Net P&L ({closed.length} trades):</span>
              <span style={{ fontWeight: 700, color: netProfit >= 0 ? '#4ade80' : '#f87171' }}>
                {netProfit >= 0 ? '+' : ''}${netProfit.toFixed(2)}
              </span>
            </div>
          </div>
        )}
      </div>
    </div>
  )
}