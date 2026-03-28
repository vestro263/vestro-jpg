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

export default function Positions() {
  const { positions, fetchPositions, account } = useBotStore()
  const isMobile = useIsMobile()

  useEffect(() => {
    fetchPositions()
    const id = setInterval(fetchPositions, 5000)
    return () => clearInterval(id)
  }, [])

  const totalProfit = positions.reduce((s, p) => s + (p.profit || 0), 0)
  const totalVolume = positions.reduce((s, p) => s + (p.volume || 0), 0)
  const buys        = positions.filter(p => p.type === 'buy').length
  const sells       = positions.filter(p => p.type === 'sell').length

  const grid4 = {
    display: 'grid',
    gridTemplateColumns: isMobile ? 'repeat(2,minmax(0,1fr))' : 'repeat(4,minmax(0,1fr))',
    gap: 10,
  }

  return (
    <div style={S.page}>

      {/* Stats */}
      <div style={grid4}>
        <StatCard label="Open Positions" value={positions.length} color="#93c5fd" />
        <StatCard
          label="Total P&L"
          value={`${totalProfit >= 0 ? '+' : ''}$${totalProfit.toFixed(2)}`}
          color={totalProfit >= 0 ? '#4ade80' : '#f87171'}
          sub="unrealised"
        />
        <StatCard label="Total Volume" value={totalVolume.toFixed(2)} color="#e5e7eb" sub="lots" />
        <StatCard label="Long / Short"  value={`${buys} / ${sells}`}  color="#fbbf24" />
      </div>

      {/* Table card */}
      <div style={S.card}>
        <div style={{ display: 'flex', alignItems: 'center', marginBottom: 14 }}>
          <span style={S.h3}>Open Positions</span>
          <button
            onClick={fetchPositions}
            style={{
              marginLeft: 'auto', padding: '6px 14px', borderRadius: 7,
              border: '1px solid #1f2937', background: 'transparent',
              color: '#6b7280', fontSize: 11, cursor: 'pointer', minHeight: 34,
            }}
          >
            ↻ Refresh
          </button>
        </div>

        {positions.length === 0 ? (
          <Empty icon="📉" text="No open positions" />
        ) : (
          <div style={{ overflowX: 'auto', WebkitOverflowScrolling: 'touch' }}>
            <table style={{ width: '100%', borderCollapse: 'collapse', minWidth: 640 }}>
              <thead>
                <tr>
                  {['Ticket','Symbol','Type','Volume','Open Price','Current','SL','TP','Swap','P&L'].map(h => (
                    <th key={h} style={S.th}>{h}</th>
                  ))}
                </tr>
              </thead>
              <tbody>
                {positions.map(p => {
                  const profit = p.profit || 0
                  return (
                    <tr key={p.ticket}
                      onMouseEnter={e => e.currentTarget.style.background = '#1f2937'}
                      onMouseLeave={e => e.currentTarget.style.background = 'transparent'}
                      style={{ transition: 'background 0.1s' }}
                    >
                      <td style={{ ...S.td, color: '#6b7280', fontFamily: 'monospace' }}>{p.ticket}</td>
                      <td style={{ ...S.td, fontWeight: 600, color: '#e5e7eb' }}>{p.symbol}</td>
                      <td style={S.td}><DirectionBadge direction={p.type} /></td>
                      <td style={S.td}>{(p.volume || 0).toFixed(2)}</td>
                      <td style={{ ...S.td, fontFamily: 'monospace' }}>{(p.open_price    || p.price_open    || 0).toFixed(5)}</td>
                      <td style={{ ...S.td, fontFamily: 'monospace' }}>{(p.current_price || p.price_current || 0).toFixed(5)}</td>
                      <td style={{ ...S.td, color: '#f87171' }}>{p.sl ? p.sl.toFixed(5) : '—'}</td>
                      <td style={{ ...S.td, color: '#4ade80' }}>{p.tp ? p.tp.toFixed(5) : '—'}</td>
                      <td style={S.td}>{(p.swap || 0).toFixed(2)}</td>
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
              display: 'flex', justifyContent: 'flex-end',
              gap: 8, fontSize: 12, flexWrap: 'wrap',
            }}>
              <span style={{ color: '#6b7280' }}>Total unrealised P&L:</span>
              <span style={{ fontWeight: 700, color: totalProfit >= 0 ? '#4ade80' : '#f87171' }}>
                {totalProfit >= 0 ? '+' : ''}${totalProfit.toFixed(2)}
              </span>
            </div>
          </div>
        )}
      </div>
    </div>
  )
}