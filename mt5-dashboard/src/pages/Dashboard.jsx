import { useEffect, useState } from 'react'
import {
  LineChart, Line, XAxis, YAxis,
  Tooltip, ResponsiveContainer, CartesianGrid
} from 'recharts'

import useBotStore from '../store/botStore'
import NewsBar from '../components/NewsBar'
import RiskGauge from '../components/RiskGauge'
import { StatCard, DirectionBadge, ATRZoneBadge, TSSBar } from '../components/ui'

// ── responsive hook ────────────────────────────────────────────────────────────
function useIsMobile(breakpoint = 640) {
  const [isMobile, setIsMobile] = useState(() => window.innerWidth < breakpoint)
  useEffect(() => {
    const handler = () => setIsMobile(window.innerWidth < breakpoint)
    window.addEventListener('resize', handler)
    return () => window.removeEventListener('resize', handler)
  }, [breakpoint])
  return isMobile
}

// ── base styles ────────────────────────────────────────────────────────────────
const S = {
  page:  { padding: '16px', display: 'flex', flexDirection: 'column', gap: 16 },
  card:  { background: '#111827', border: '1px solid #1f2937', borderRadius: 12, padding: 16 },
  h3:    { fontSize: 13, fontWeight: 600, color: '#e5e7eb', marginBottom: 12 },
  td:    { padding: '7px 10px', fontSize: 12, color: '#d1d5db', borderBottom: '1px solid #1f2937' },
  th:    { padding: '7px 10px', fontSize: 11, color: '#6b7280', textTransform: 'uppercase', letterSpacing: '0.05em', borderBottom: '1px solid #374151', textAlign: 'left' },
}

// ── component ──────────────────────────────────────────────────────────────────
export default function Dashboard() {
  const {
    account, signals, positions, tradeFeed,
    fetchPositions, botRunning, startBot, stopBot,
  } = useBotStore()

  const isMobile = useIsMobile()

  const latest = signals[0]
  const sig    = latest?.signal || {}

  useEffect(() => { fetchPositions() }, [])

  const equityCurve = tradeFeed
    .filter(t => t.trade?.entry)
    .slice(0, 20)
    .reverse()
    .map((t, i) => ({
      i,
      val: parseFloat((account.balance + i * 0.5).toFixed(2)),
    }))

  const totalOpenProfit = positions.reduce((s, p) => s + (p.profit || 0), 0)

  // ── responsive grid helpers ──────────────────────────────────────────────────
  const grid4 = {
    display: 'grid',
    gridTemplateColumns: isMobile ? 'repeat(2, minmax(0, 1fr))' : 'repeat(4, minmax(0, 1fr))',
    gap: 10,
  }

  const grid2 = {
    display: 'grid',
    gridTemplateColumns: isMobile ? '1fr' : 'repeat(2, minmax(0, 1fr))',
    gap: 14,
  }

  const indicatorGrid = {
    display: 'grid',
    gridTemplateColumns: isMobile ? 'repeat(2, 1fr)' : 'repeat(3, 1fr)',
    gap: 8,
  }

  // ── bot controls ─────────────────────────────────────────────────────────────
  const btnBase = {
    display: 'inline-flex', alignItems: 'center', gap: 7,
    padding: '10px 18px',           // slightly taller for touch
    borderRadius: 8, border: 'none',
    color: '#fff', fontSize: 13, fontWeight: 600,
    cursor: 'pointer', transition: 'opacity 0.15s',
    minHeight: 44,                  // touch target
    WebkitTapHighlightColor: 'transparent',
  }

  return (
    <div style={S.page}>

      {/* 📰 NEWS */}
      <NewsBar />

      {/* 🤖 BOT CONTROLS */}
      <div style={{
        display: 'flex',
        flexWrap: 'wrap',           // wraps on narrow screens
        alignItems: 'center',
        gap: 10,
        background: '#111827',
        border: '1px solid #1f2937',
        borderRadius: 12,
        padding: '12px 14px',
      }}>
        <button
          onClick={startBot}
          disabled={botRunning}
          style={{
            ...btnBase,
            background: botRunning ? '#14532d' : '#16a34a',
            cursor: botRunning ? 'not-allowed' : 'pointer',
            opacity: botRunning ? 0.5 : 1,
            flex: isMobile ? '1 1 calc(50% - 5px)' : 'none',
            justifyContent: 'center',
          }}
        >
          <svg width="11" height="11" viewBox="0 0 12 12" fill="currentColor">
            <polygon points="2,1 11,6 2,11" />
          </svg>
          Run Bot
        </button>

        <button
          onClick={stopBot}
          disabled={!botRunning}
          style={{
            ...btnBase,
            background: !botRunning ? '#450a0a' : '#dc2626',
            cursor: !botRunning ? 'not-allowed' : 'pointer',
            opacity: !botRunning ? 0.45 : 1,
            flex: isMobile ? '1 1 calc(50% - 5px)' : 'none',
            justifyContent: 'center',
          }}
        >
          <svg width="10" height="10" viewBox="0 0 12 12" fill="currentColor">
            <rect x="1.5" y="1.5" width="9" height="9" rx="1.5" />
          </svg>
          Stop Bot
        </button>

        {/* status indicator */}
        <div style={{
          display: 'flex', alignItems: 'center', gap: 6,
          ...(isMobile ? { width: '100%', justifyContent: 'center', paddingTop: 2 } : { marginLeft: 4 }),
        }}>
          <span style={{
            width: 7, height: 7, borderRadius: '50%',
            background: botRunning ? '#4ade80' : '#4b5563',
            boxShadow: botRunning ? '0 0 6px #4ade80' : 'none',
            transition: 'all 0.3s', display: 'inline-block', flexShrink: 0,
          }} />
          <span style={{ fontSize: 12, color: '#6b7280' }}>
            {botRunning ? 'Bot is running…' : 'Bot is stopped'}
          </span>
        </div>
      </div>

      {/* 📊 STATS */}
      <div style={grid4}>
        <StatCard
          label="Balance"
          value={`$${(account.balance || 0).toLocaleString('en', { minimumFractionDigits: 2 })}`}
          color="#f1f5f9"
        />
        <StatCard
          label="Open P&L"
          value={`${totalOpenProfit >= 0 ? '+' : ''}$${totalOpenProfit.toFixed(2)}`}
          color={totalOpenProfit >= 0 ? '#4ade80' : '#f87171'}
          sub={`${positions.length} position${positions.length !== 1 ? 's' : ''} open`}
        />
        <StatCard
          label="Signals Today"
          value={signals.filter(s => s.signal?.direction !== 0).length}
          color="#93c5fd"
          sub={`${signals.length} bars evaluated`}
        />
        <StatCard
          label="Daily D/D"
          value={`${Math.abs(((account.balance || 0) - (account.equity || 0)) / (account.balance || 1) * 100).toFixed(2)}%`}
          color="#fbbf24"
          sub="5% max limit"
        />
      </div>

      {/* 🔲 MAIN GRID */}
      <div style={grid2}>

        {/* 📡 SIGNAL */}
        <div style={S.card}>
          <div style={S.h3}>
            Latest signal
            {latest?.symbol && (
              <span style={{ color: '#6b7280', marginLeft: 6 }}>{latest.symbol}</span>
            )}
          </div>

          {latest ? (
            <div style={{ display: 'flex', flexDirection: 'column', gap: 12 }}>

              <div style={{ display: 'flex', gap: 8, flexWrap: 'wrap', alignItems: 'center' }}>
                <DirectionBadge direction={sig.direction} />
                <ATRZoneBadge zone={sig.atr_zone} />
                <span style={{ marginLeft: 'auto', fontSize: 11, color: '#6b7280', whiteSpace: 'nowrap' }}>
                  {latest.receivedAt}
                </span>
              </div>

              <div>
                <div style={{ fontSize: 11, color: '#6b7280' }}>TSS</div>
                <TSSBar score={sig.tss_score || 0} />
              </div>

              <div style={indicatorGrid}>
                {[
                  ['RSI', sig.rsi], ['ADX', sig.adx],
                  ['ATR', sig.atr], ['EMA50', sig.ema50],
                  ['EMA200', sig.ema200], ['MACD', sig.macd_hist],
                ].map(([k, v]) => (
                  <div key={k} style={{ background: '#1f2937', padding: 8, borderRadius: 8 }}>
                    <div style={{ fontSize: 10, color: '#6b7280' }}>{k}</div>
                    <div style={{ fontSize: 13, fontWeight: 500, color: '#e5e7eb', marginTop: 2 }}>
                      {(v || 0).toFixed(4)}
                    </div>
                  </div>
                ))}
              </div>

            </div>
          ) : (
            <div style={{ padding: '24px 0', textAlign: 'center', color: '#4b5563', fontSize: 12 }}>
              No signal yet — waiting for the bot…
            </div>
          )}
        </div>

        {/* 📉 POSITIONS */}
        <div style={S.card}>
          <div style={S.h3}>Open positions ({positions.length})</div>

          {positions.length === 0 ? (
            <div style={{ padding: '24px 0', textAlign: 'center', color: '#4b5563', fontSize: 12 }}>
              No open positions
            </div>
          ) : (
            /* horizontally scrollable on very small screens */
            <div style={{ overflowX: 'auto', WebkitOverflowScrolling: 'touch' }}>
              <table style={{ width: '100%', borderCollapse: 'collapse', minWidth: 260 }}>
                <thead>
                  <tr>
                    {['Symbol', 'Type', 'Lot', 'P&L'].map(h => (
                      <th key={h} style={S.th}>{h}</th>
                    ))}
                  </tr>
                </thead>
                <tbody>
                  {positions.map(p => (
                    <tr
                      key={p.ticket}
                      onMouseEnter={e => e.currentTarget.style.background = '#1f2937'}
                      onMouseLeave={e => e.currentTarget.style.background = 'transparent'}
                      style={{ transition: 'background 0.1s' }}
                    >
                      <td style={{ ...S.td, fontWeight: 600, color: '#e5e7eb' }}>{p.symbol}</td>
                      <td style={S.td}>
                        <DirectionBadge direction={p.type === 'buy' ? 1 : -1} />
                      </td>
                      <td style={S.td}>{p.volume}</td>
                      <td style={{
                        ...S.td, fontWeight: 600,
                        color: (p.profit || 0) >= 0 ? '#4ade80' : '#f87171',
                      }}>
                        {(p.profit || 0) >= 0 ? '+' : ''}{(p.profit || 0).toFixed(2)}
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
        </div>

      </div>

      {/* ⚠️ RISK */}
      <RiskGauge />

      {/* 📈 EQUITY CHART */}
      {equityCurve.length > 1 && (
        <div style={S.card}>
          <div style={S.h3}>Equity</div>
          <ResponsiveContainer width="100%" height={isMobile ? 100 : 120}>
            <LineChart data={equityCurve}>
              <CartesianGrid stroke="#1f2937" />
              <XAxis dataKey="i" hide />
              <YAxis
                tick={{ fontSize: 10, fill: '#6b7280' }}
                tickLine={false}
                axisLine={false}
                width={44}
              />
              <Tooltip
                contentStyle={{
                  background: '#111827', border: '1px solid #1f2937',
                  borderRadius: 8, fontSize: 12,
                }}
              />
              <Line dataKey="val" stroke="#38bdf8" dot={false} strokeWidth={2} />
            </LineChart>
          </ResponsiveContainer>
        </div>
      )}

      {/* 📡 TRADE FEED */}
      <div style={S.card}>
        <div style={S.h3}>Trade feed</div>
        {tradeFeed.length === 0 ? (
          <div style={{ padding: '16px 0', textAlign: 'center', color: '#4b5563', fontSize: 12 }}>
            No trades yet
          </div>
        ) : (
          <div style={{ display: 'flex', flexDirection: 'column', gap: 4 }}>
            {tradeFeed.slice(0, 10).map(t => (
              <div key={t.id} style={{
                background: '#1f2937', borderRadius: 6,
                padding: '6px 10px',
                fontSize: isMobile ? 10 : 11,
                color: '#9ca3af', fontFamily: 'monospace',
                overflowX: 'auto',                     // allow long JSON to scroll
                WebkitOverflowScrolling: 'touch',
                whiteSpace: 'nowrap',
              }}>
                <span style={{ color: '#4b5563', marginRight: 8 }}>{t.time}</span>
                {JSON.stringify(t.trade ?? t)}
              </div>
            ))}
          </div>
        )}
      </div>

    </div>
  )
}