import { useEffect, useState } from 'react'
import {
  LineChart, Line, XAxis, YAxis,
  Tooltip, ResponsiveContainer, CartesianGrid
} from 'recharts'

import useBotStore from '../store/botStore'
import NewsBar from '../components/NewsBar'
import RiskGauge from '../components/RiskGauge'
import { StatCard, DirectionBadge, ATRZoneBadge, TSSBar } from '../components/ui'

const API = 'https://vestro-jpg.onrender.com'

const SYMBOLS = [
  { label: 'Volatility 10',  value: 'R_10' },
  { label: 'Volatility 25',  value: 'R_25' },
  { label: 'Volatility 50',  value: 'R_50' },
  { label: 'Volatility 75',  value: 'R_75' },
  { label: 'Volatility 100', value: 'R_100' },
  { label: 'Boom 500',       value: 'BOOM500' },
  { label: 'Boom 1000',      value: 'BOOM1000' },
  { label: 'Crash 500',      value: 'CRASH500' },
  { label: 'Crash 1000',     value: 'CRASH1000' },
]

const CONTRACT_TYPES = [
  { label: 'Rise / Fall',      value: 'rise_fall' },
  { label: 'Higher / Lower',   value: 'higher_lower' },
  { label: 'Touch / No Touch', value: 'touch' },
]

// Active strategies — add new ones here and they appear automatically
const STRATEGY_SYMBOLS = [
  { key: 'R_75', label: 'V75', color: '#f59e0b' },
  { key: 'R_25', label: 'V25', color: '#38bdf8' },
]

function useIsMobile(breakpoint = 640) {
  const [isMobile, setIsMobile] = useState(() => window.innerWidth < breakpoint)
  useEffect(() => {
    const handler = () => setIsMobile(window.innerWidth < breakpoint)
    window.addEventListener('resize', handler)
    return () => window.removeEventListener('resize', handler)
  }, [breakpoint])
  return isMobile
}

const S = {
  page: { padding: '16px', display: 'flex', flexDirection: 'column', gap: 16 },
  card: { background: '#111827', border: '1px solid #1f2937', borderRadius: 12, padding: 16 },
  h3:   { fontSize: 13, fontWeight: 600, color: '#e5e7eb', marginBottom: 12 },
  td:   { padding: '7px 10px', fontSize: 12, color: '#d1d5db', borderBottom: '1px solid #1f2937' },
  th:   { padding: '7px 10px', fontSize: 11, color: '#6b7280', textTransform: 'uppercase', letterSpacing: '0.05em', borderBottom: '1px solid #374151', textAlign: 'left' },
}

// ── Active Account Banner ──────────────────────────────────────────────────────
function ActiveAccountBanner() {
  const accountId = useBotStore(s => s.accountId)
  const account   = useBotStore(s => s.account)
  const isDemo    = accountId?.startsWith('VRT') ?? false

  if (!accountId) return null

  return (
    <div style={{
      display: 'flex', alignItems: 'center', gap: 10, flexWrap: 'wrap',
      background: '#0b1120', border: '1px solid #1e2d45',
      borderRadius: 10, padding: '8px 14px',
    }}>
      <span style={{ fontSize: 11, color: '#4b5563' }}>Trading on</span>
      <span style={{
        fontSize: 10, fontWeight: 700, padding: '2px 7px', borderRadius: 4,
        background: isDemo ? '#1e3a5f' : '#14532d',
        color:      isDemo ? '#60a5fa' : '#4ade80',
      }}>
        {isDemo ? 'DEMO' : 'REAL'}
      </span>
      <span style={{ color: '#f1f5f9', fontSize: 13, fontWeight: 600 }}>
        {accountId}
      </span>
      <span style={{ color: '#94a3b8', fontSize: 12, fontFamily: 'monospace' }}>
        {Number(account.balance || 0).toLocaleString(undefined, {
          minimumFractionDigits: 2, maximumFractionDigits: 2,
        })} {account.currency || 'USD'}
      </span>
      {isDemo && (
        <span style={{
          marginLeft: 'auto', fontSize: 10, color: '#ca8a04',
          background: '#1c1a08', border: '1px solid #713f12',
          padding: '2px 8px', borderRadius: 4,
        }}>
          Demo — P&amp;L not real
        </span>
      )}
    </div>
  )
}

// ── Strategy Signal Cards ──────────────────────────────────────────────────────
function StrategySignalCards({ signalMap, isMobile }) {
  return (
    <div style={{
      display: 'grid',
      gridTemplateColumns: isMobile
        ? '1fr'
        : `repeat(${STRATEGY_SYMBOLS.length}, minmax(0,1fr))`,
      gap: 12,
    }}>
      {STRATEGY_SYMBOLS.map(({ key, label, color }) => {
        const entry = signalMap?.[key]
        const sig   = entry?.signal || {}

        const confPct   = ((sig.confidence || 0) * 100)
        const confColor = confPct >= 70 ? '#4ade80' : confPct >= 50 ? '#fbbf24' : '#f87171'

        return (
          <div key={key} style={{
            ...S.card,
            borderTop: `2px solid ${color}`,
            position: 'relative',
            overflow: 'hidden',
          }}>
            {/* subtle color glow in top-right corner */}
            <div style={{
              position: 'absolute', top: -30, right: -30,
              width: 80, height: 80, borderRadius: '50%',
              background: color, opacity: 0.05, pointerEvents: 'none',
            }} />

            <div style={{
              ...S.h3,
              display: 'flex', justifyContent: 'space-between', alignItems: 'center',
            }}>
              <span style={{ display: 'flex', alignItems: 'center', gap: 6 }}>
                <span style={{
                  width: 8, height: 8, borderRadius: '50%',
                  background: entry ? color : '#374151',
                  boxShadow: entry ? `0 0 6px ${color}` : 'none',
                  display: 'inline-block', flexShrink: 0,
                  transition: 'all 0.3s',
                }} />
                {label} Signal
              </span>
              <span style={{
                fontSize: 10, color: '#4b5563',
                background: '#1f2937', padding: '2px 6px', borderRadius: 4,
                fontFamily: 'monospace',
              }}>
                {key}
              </span>
            </div>

            {entry ? (
              <div style={{ display: 'flex', flexDirection: 'column', gap: 10 }}>

                {/* Direction + ATR zone + time */}
                <div style={{ display: 'flex', gap: 8, flexWrap: 'wrap', alignItems: 'center' }}>
                  <DirectionBadge direction={sig.direction} />
                  <ATRZoneBadge zone={sig.atr_zone} />
                  <span style={{ marginLeft: 'auto', fontSize: 11, color: '#6b7280', whiteSpace: 'nowrap' }}>
                    {entry.receivedAt}
                  </span>
                </div>

                {/* TSS */}
                <div>
                  <div style={{ fontSize: 11, color: '#6b7280', marginBottom: 4 }}>TSS</div>
                  <TSSBar score={sig.tss_score || 0} />
                </div>

                {/* Confidence bar */}
                <div>
                  <div style={{ display: 'flex', justifyContent: 'space-between', marginBottom: 3 }}>
                    <span style={{ fontSize: 11, color: '#6b7280' }}>Confidence</span>
                    <span style={{ fontSize: 11, fontWeight: 600, color: confColor }}>
                      {confPct.toFixed(0)}%
                    </span>
                  </div>
                  <div style={{ height: 4, background: '#1f2937', borderRadius: 2, overflow: 'hidden' }}>
                    <div style={{
                      height: '100%',
                      width:  `${confPct}%`,
                      background: confColor,
                      borderRadius: 2,
                      transition: 'width 0.5s ease, background 0.3s',
                    }} />
                  </div>
                </div>

                {/* Reason */}
                {sig.reason && (
                  <div style={{
                    fontSize: 10, color: '#6b7280',
                    background: '#1f2937', borderRadius: 6,
                    padding: '5px 8px', lineHeight: 1.5,
                  }}>
                    {sig.reason}
                  </div>
                )}

                {/* Indicators 2-col grid */}
                <div style={{ display: 'grid', gridTemplateColumns: 'repeat(2,1fr)', gap: 6 }}>
                  {[
                    ['RSI',    sig.rsi],
                    ['ADX',    sig.adx],
                    ['EMA50',  sig.ema50],
                    ['EMA200', sig.ema200],
                    ['MACD',   sig.macd_hist],
                    ['ATR',    sig.atr],
                  ].map(([k, v]) => (
                    <div key={k} style={{
                      background: '#1a2235', padding: '6px 8px', borderRadius: 6,
                    }}>
                      <div style={{ fontSize: 10, color: '#6b7280' }}>{k}</div>
                      <div style={{ fontSize: 12, fontWeight: 500, color: '#e5e7eb', marginTop: 1 }}>
                        {v != null ? Number(v).toFixed(4) : '—'}
                      </div>
                    </div>
                  ))}
                </div>

              </div>
            ) : (
              <div style={{
                padding: '28px 0', textAlign: 'center',
                color: '#4b5563', fontSize: 12,
              }}>
                Waiting for {label} signal…
              </div>
            )}
          </div>
        )
      })}
    </div>
  )
}

// ── Quick Trade ────────────────────────────────────────────────────────────────
function QuickTrade({ isMobile }) {
  const { signals, accountId } = useBotStore()
  const [symbol,       setSymbol]       = useState('R_100')
  const [contractType, setContractType] = useState('rise_fall')
  const [stake,        setStake]        = useState('1')
  const [loading,      setLoading]      = useState(null)
  const [result,       setResult]       = useState(null)
  const [error,        setError]        = useState(null)

  const latestSymbol = signals[0]?.symbol
  useEffect(() => { if (latestSymbol) setSymbol(latestSymbol) }, [latestSymbol])

  async function executeTrade(action) {
    if (!accountId) { setError('No account selected'); return }
    setLoading(action); setResult(null); setError(null)
    try {
      const res = await fetch(`${API}/api/trade`, {
        method:  'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          broker:        'deriv',
          symbol,
          action,
          amount:        parseFloat(stake) || 1,
          account_id:    accountId,
          contract_type: contractType,
        }),
      })
      const data = await res.json()
      if (!res.ok) throw new Error(data.detail || 'Trade failed')
      setResult({ action, symbol, stake, time: new Date().toLocaleTimeString() })
    } catch (err) {
      setError(err.message)
    } finally {
      setLoading(null)
    }
  }

  const sel = {
    background: '#1f2937', border: '1px solid #374151',
    borderRadius: 8, color: '#e5e7eb', fontSize: 13,
    padding: '9px 12px', outline: 'none', width: '100%',
  }

  return (
    <div style={S.card}>
      <div style={S.h3}>⚡ Quick Trade</div>
      <div style={{ display: 'grid', gridTemplateColumns: isMobile ? '1fr' : '1fr 1fr 1fr', gap: 10, marginBottom: 14 }}>
        <div>
          <div style={{ fontSize: 11, color: '#6b7280', marginBottom: 5 }}>Symbol</div>
          <select style={sel} value={symbol} onChange={e => setSymbol(e.target.value)}>
            {SYMBOLS.map(s => <option key={s.value} value={s.value}>{s.label}</option>)}
          </select>
        </div>
        <div>
          <div style={{ fontSize: 11, color: '#6b7280', marginBottom: 5 }}>Contract Type</div>
          <select style={sel} value={contractType} onChange={e => setContractType(e.target.value)}>
            {CONTRACT_TYPES.map(c => <option key={c.value} value={c.value}>{c.label}</option>)}
          </select>
        </div>
        <div>
          <div style={{ fontSize: 11, color: '#6b7280', marginBottom: 5 }}>Stake (USD)</div>
          <input
            style={{ ...sel, fontFamily: 'monospace' }}
            type="number" min="0.35" step="0.5"
            value={stake} onChange={e => setStake(e.target.value)}
            placeholder="1.00"
          />
        </div>
      </div>
      <div style={{ display: 'flex', gap: 10 }}>
        <button onClick={() => executeTrade('BUY')} disabled={!!loading} style={{
          flex: 1, padding: '13px 0', borderRadius: 8, border: 'none',
          background: loading === 'BUY' ? '#14532d' : '#16a34a',
          color: '#fff', fontSize: 14, fontWeight: 700,
          cursor: loading ? 'not-allowed' : 'pointer',
          opacity: loading && loading !== 'BUY' ? 0.5 : 1,
        }}>
          {loading === 'BUY' ? 'Placing…' : '▲ BUY / RISE'}
        </button>
        <button onClick={() => executeTrade('SELL')} disabled={!!loading} style={{
          flex: 1, padding: '13px 0', borderRadius: 8, border: 'none',
          background: loading === 'SELL' ? '#450a0a' : '#dc2626',
          color: '#fff', fontSize: 14, fontWeight: 700,
          cursor: loading ? 'not-allowed' : 'pointer',
          opacity: loading && loading !== 'SELL' ? 0.5 : 1,
        }}>
          {loading === 'SELL' ? 'Placing…' : '▼ SELL / FALL'}
        </button>
      </div>
      {result && (
        <div style={{ marginTop: 12, background: '#052e16', border: '1px solid #166534', borderRadius: 8, padding: '10px 14px', fontSize: 12, color: '#4ade80' }}>
          ✓ {result.action} placed — {result.symbol} · ${result.stake} · {result.time}
        </div>
      )}
      {error && (
        <div style={{ marginTop: 12, background: '#1f1217', border: '1px solid #7f1d1d', borderRadius: 8, padding: '10px 14px', fontSize: 12, color: '#f87171' }}>
          ✗ {error}
        </div>
      )}
    </div>
  )
}

// ── Dashboard ──────────────────────────────────────────────────────────────────
export default function Dashboard() {
  const {
    account, accountId, signals, signalMap, positions, tradeFeed,
    fetchPositions, fetchAccount, botRunning, startBot, stopBot,
  } = useBotStore()

  const isMobile = useIsMobile()

  // Refresh account + positions whenever accountId changes
  useEffect(() => {
    if (accountId) {
      fetchAccount()
      fetchPositions()
    }
  }, [accountId])

  const totalOpenProfit = tradeFeed
    .filter(t => !t.is_expired && !t.is_sold && t.contract_id)
    .reduce((s, t) => s + (t.profit ?? 0), 0)

  const equityCurve = tradeFeed
    .filter(t => t.is_expired || t.is_sold)
    .slice(0, 30)
    .reverse()
    .reduce((acc, t, i) => {
      const prev = acc[i - 1]?.val ?? account.balance
      acc.push({ i, val: parseFloat((prev + (t.profit || 0)).toFixed(2)) })
      return acc
    }, [])

  const grid4 = {
    display: 'grid',
    gridTemplateColumns: isMobile ? 'repeat(2, minmax(0,1fr))' : 'repeat(4, minmax(0,1fr))',
    gap: 10,
  }
  const grid2 = {
    display: 'grid',
    gridTemplateColumns: isMobile ? '1fr' : 'repeat(2, minmax(0,1fr))',
    gap: 14,
  }
  const btnBase = {
    display: 'inline-flex', alignItems: 'center', gap: 7,
    padding: '10px 18px', borderRadius: 8, border: 'none',
    color: '#fff', fontSize: 13, fontWeight: 600,
    cursor: 'pointer', transition: 'opacity 0.15s',
    minHeight: 44, WebkitTapHighlightColor: 'transparent',
  }

  return (
    <div style={S.page}>

      <NewsBar />

      {/* 🏦 ACTIVE ACCOUNT BANNER */}
      <ActiveAccountBanner />

      {/* 🤖 BOT CONTROLS */}
      <div style={{
        display: 'flex', flexWrap: 'wrap', alignItems: 'center', gap: 10,
        background: '#111827', border: '1px solid #1f2937', borderRadius: 12, padding: '12px 14px',
      }}>
        <button onClick={startBot} disabled={botRunning} style={{
          ...btnBase,
          background: botRunning ? '#14532d' : '#16a34a',
          cursor: botRunning ? 'not-allowed' : 'pointer',
          opacity: botRunning ? 0.5 : 1,
          flex: isMobile ? '1 1 calc(50% - 5px)' : 'none',
          justifyContent: 'center',
        }}>
          <svg width="11" height="11" viewBox="0 0 12 12" fill="currentColor"><polygon points="2,1 11,6 2,11" /></svg>
          Run Bot
        </button>
        <button onClick={stopBot} disabled={!botRunning} style={{
          ...btnBase,
          background: !botRunning ? '#450a0a' : '#dc2626',
          cursor: !botRunning ? 'not-allowed' : 'pointer',
          opacity: !botRunning ? 0.45 : 1,
          flex: isMobile ? '1 1 calc(50% - 5px)' : 'none',
          justifyContent: 'center',
        }}>
          <svg width="10" height="10" viewBox="0 0 12 12" fill="currentColor"><rect x="1.5" y="1.5" width="9" height="9" rx="1.5" /></svg>
          Stop Bot
        </button>
        <div style={{ display: 'flex', alignItems: 'center', gap: 6, ...(isMobile ? { width: '100%', justifyContent: 'center', paddingTop: 2 } : { marginLeft: 4 }) }}>
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
          sub={account.currency || 'USD'}
        />
        <StatCard
          label="Equity"
          value={`$${(account.equity || account.balance || 0).toLocaleString('en', { minimumFractionDigits: 2 })}`}
          color="#4ade80"
          sub={accountId?.startsWith('VRT') ? 'Demo account' : 'Real account'}
        />
        <StatCard
          label="Open P&L"
          value={`${totalOpenProfit >= 0 ? '+' : ''}$${totalOpenProfit.toFixed(2)}`}
          color={totalOpenProfit >= 0 ? '#4ade80' : '#f87171'}
          sub={`${positions.length} position${positions.length !== 1 ? 's' : ''} open`}
        />
        <StatCard
          label="Daily D/D"
          value={`${Math.abs(((account.balance || 0) - (account.equity || 0)) / (account.balance || 1) * 100).toFixed(2)}%`}
          color="#fbbf24"
          sub="5% max limit"
        />
      </div>

      {/* ⚡ QUICK TRADE */}
      <QuickTrade isMobile={isMobile} />

      {/* 📡 STRATEGY SIGNAL CARDS — V75 + V25 side by side */}
      <StrategySignalCards signalMap={signalMap} isMobile={isMobile} />

      {/* 🔲 POSITIONS */}
      <div style={S.card}>
        <div style={S.h3}>Open positions ({positions.length})</div>
        {positions.length === 0 ? (
          <div style={{ padding: '24px 0', textAlign: 'center', color: '#4b5563', fontSize: 12 }}>
            No open positions
          </div>
        ) : (
          <div style={{ overflowX: 'auto', WebkitOverflowScrolling: 'touch' }}>
            <table style={{ width: '100%', borderCollapse: 'collapse', minWidth: 260 }}>
              <thead>
                <tr>{['Symbol', 'Type', 'Lot', 'P&L'].map(h => <th key={h} style={S.th}>{h}</th>)}</tr>
              </thead>
              <tbody>
                {positions.map(p => (
                  <tr key={p.ticket}
                    onMouseEnter={e => e.currentTarget.style.background = '#1f2937'}
                    onMouseLeave={e => e.currentTarget.style.background = 'transparent'}
                    style={{ transition: 'background 0.1s' }}
                  >
                    <td style={{ ...S.td, fontWeight: 600, color: '#e5e7eb' }}>{p.symbol}</td>
                    <td style={S.td}><DirectionBadge direction={p.type === 'buy' ? 1 : -1} /></td>
                    <td style={S.td}>{p.volume}</td>
                    <td style={{ ...S.td, fontWeight: 600, color: (p.profit || 0) >= 0 ? '#4ade80' : '#f87171' }}>
                      {(p.profit || 0) >= 0 ? '+' : ''}{(p.profit || 0).toFixed(2)}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </div>

      <RiskGauge />

      {/* 📈 EQUITY CURVE */}
      {equityCurve.length > 1 && (
        <div style={S.card}>
          <div style={S.h3}>Equity curve</div>
          <ResponsiveContainer width="100%" height={isMobile ? 100 : 120}>
            <LineChart data={equityCurve}>
              <CartesianGrid stroke="#1f2937" />
              <XAxis dataKey="i" hide />
              <YAxis tick={{ fontSize: 10, fill: '#6b7280' }} tickLine={false} axisLine={false} width={44} />
              <Tooltip contentStyle={{ background: '#111827', border: '1px solid #1f2937', borderRadius: 8, fontSize: 12 }} />
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
          <div style={{ display: 'flex', flexDirection: 'column', gap: 6 }}>
            {tradeFeed.slice(0, 15).map(t => {
              const isLive    = !t.is_expired && !t.is_sold && !!t.contract_id
              const profit    = t.profit ?? 0
              const profitPct = t.profit_pct ?? 0
              const status    = t.is_expired || t.is_sold ? 'closed' : isLive ? 'live' : 'pending'

              // color-code by strategy
              const stratColor = t.symbol === 'R_25' ? '#38bdf8'
                               : t.symbol === 'R_75' ? '#f59e0b'
                               : '#6b7280'

              return (
                <div key={t.id ?? t.contract_id} style={{
                  background:   '#1f2937',
                  borderRadius: 8,
                  padding:      '10px 12px',
                  border:       `1px solid ${isLive ? '#1d4ed8' : profit >= 0 ? '#166534' : '#7f1d1d'}`,
                  transition:   'border-color 0.3s',
                }}>
                  <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center' }}>
                    <div style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
                      {isLive && (
                        <span style={{
                          width: 6, height: 6, borderRadius: '50%',
                          background: '#3b82f6', boxShadow: '0 0 6px #3b82f6',
                          display: 'inline-block', flexShrink: 0,
                        }} />
                      )}
                      <span style={{ color: stratColor, fontSize: 12, fontWeight: 700 }}>
                        {t.symbol ?? '—'}
                      </span>
                      {t.contract_type && (
                        <span style={{
                          fontSize: 10, fontWeight: 600, padding: '2px 6px', borderRadius: 4,
                          background: t.contract_type === 'CALL' ? '#14532d' : '#450a0a',
                          color:      t.contract_type === 'CALL' ? '#4ade80'  : '#f87171',
                        }}>
                          {t.contract_type === 'CALL' ? '▲ RISE' : '▼ FALL'}
                        </span>
                      )}
                      <span style={{ fontSize: 10, color: '#4b5563' }}>{t.time}</span>
                    </div>

                    <div style={{ textAlign: 'right' }}>
                      <div style={{ fontSize: 13, fontWeight: 700, color: profit >= 0 ? '#4ade80' : '#f87171' }}>
                        {profit >= 0 ? '+' : ''}${profit.toFixed(2)}
                        <span style={{ fontSize: 10, marginLeft: 4, color: profit >= 0 ? '#4ade80' : '#f87171' }}>
                          ({profitPct >= 0 ? '+' : ''}{profitPct.toFixed(1)}%)
                        </span>
                      </div>
                      <div style={{ fontSize: 10, color: '#6b7280' }}>
                        {status === 'live' ? '● LIVE' : status === 'closed' ? 'Closed' : 'Pending'}
                      </div>
                    </div>
                  </div>

                  {isLive && t.buy_price && (
                    <div style={{ marginTop: 8 }}>
                      <div style={{ height: 3, background: '#374151', borderRadius: 2, overflow: 'hidden' }}>
                        <div style={{
                          height:     '100%',
                          width:      `${Math.min(100, Math.max(0, 50 + profitPct))}%`,
                          background: profit >= 0 ? '#4ade80' : '#f87171',
                          borderRadius: 2,
                          transition: 'width 0.5s ease, background 0.3s',
                        }} />
                      </div>
                      <div style={{ display: 'flex', justifyContent: 'space-between', marginTop: 4 }}>
                        <span style={{ fontSize: 10, color: '#6b7280' }}>Entry: {t.entry_spot ?? '—'}</span>
                        <span style={{ fontSize: 10, color: '#6b7280' }}>Now: {t.current_spot ?? '—'}</span>
                      </div>
                    </div>
                  )}
                </div>
              )
            })}
          </div>
        )}
      </div>

    </div>
  )
}