import { useEffect, useState } from 'react'
import {
  LineChart, Line, BarChart, Bar,
  XAxis, YAxis, Tooltip, ResponsiveContainer,
  CartesianGrid, ReferenceLine,
} from 'recharts'
import useBotStore from '../store/botStore'
import { S, StatCard, Empty } from '../components/ui'

export default function Performance() {
  const { stats, statsLoading, fetchStats } = useBotStore()
  const [days, setDays] = useState(30)

  useEffect(() => { fetchStats(days) }, [days])

  const DayBtn = ({ n }) => (
    <button onClick={() => setDays(n)} style={{
      padding: '3px 10px', borderRadius: 6, border: 'none', cursor: 'pointer', fontSize: 11,
      background: days === n ? '#1f2937' : 'transparent',
      color: days === n ? '#f1f5f9' : '#6b7280',
    }}>{n}d</button>
  )

  if (statsLoading) {
    return (
      <div style={{ ...S.page, alignItems: 'center', justifyContent: 'center', minHeight: 300 }}>
        <div style={{ color: '#4b5563', fontSize: 13 }}>Loading stats…</div>
      </div>
    )
  }

  if (!stats) {
    return (
      <div style={S.page}>
        <div style={S.card}>
          <Empty icon="📈" text="No stats available — make sure /stats endpoint is running" />
          <div style={{ textAlign: 'center', marginTop: 12 }}>
            <button onClick={() => fetchStats(days)} style={{
              padding: '6px 18px', borderRadius: 8, border: '1px solid #1f2937',
              background: 'transparent', color: '#6b7280', fontSize: 12, cursor: 'pointer',
            }}>↻ Retry</button>
          </div>
        </div>
      </div>
    )
  }

  // Normalise field names — backend may vary
  const totalTrades  = stats.total_trades  ?? stats.totalTrades  ?? 0
  const winRate      = stats.win_rate      ?? stats.winRate      ?? 0
  const netProfit    = stats.net_profit    ?? stats.netProfit    ?? 0
  const maxDrawdown  = stats.max_drawdown  ?? stats.maxDrawdown  ?? 0
  const profitFactor = stats.profit_factor ?? stats.profitFactor ?? 0
  const avgRR        = stats.avg_rr        ?? stats.avgRR        ?? 0
  const bestTrade    = stats.best_trade    ?? stats.bestTrade    ?? 0
  const worstTrade   = stats.worst_trade   ?? stats.worstTrade   ?? 0

  // Equity curve — array of { date, equity } or similar
  const equityCurve = (stats.equity_curve ?? stats.equityCurve ?? []).map((p, i) => ({
    i:   p.i ?? i,
    val: p.equity ?? p.val ?? p.value ?? 0,
    date: p.date ?? p.time ?? '',
  }))

  // Daily P&L bars — array of { date, pnl }
  const dailyPnl = (stats.daily_pnl ?? stats.dailyPnl ?? []).map(d => ({
    date: d.date ?? d.day ?? '',
    pnl:  d.pnl  ?? d.profit ?? 0,
  }))

  return (
    <div style={S.page}>

      {/* Range picker */}
      <div style={{ display: 'flex', gap: 4, background: '#111827', border: '1px solid #1f2937', borderRadius: 9, padding: 3, alignSelf: 'flex-start' }}>
        {[7, 14, 30, 60, 90].map(n => <DayBtn key={n} n={n} />)}
      </div>

      {/* KPI cards */}
      <div style={S.grid4}>
        <StatCard label="Net Profit"    value={`${netProfit >= 0 ? '+' : ''}$${Number(netProfit).toFixed(2)}`} color={netProfit >= 0 ? '#4ade80' : '#f87171'} />
        <StatCard label="Win Rate"      value={`${Number(winRate).toFixed(1)}%`} color="#fbbf24" sub={`${totalTrades} trades`} />
        <StatCard label="Profit Factor" value={Number(profitFactor).toFixed(2)} color="#93c5fd" />
        <StatCard label="Max Drawdown"  value={`${Number(maxDrawdown).toFixed(2)}%`} color={maxDrawdown > 3 ? '#f87171' : '#e5e7eb'} />
      </div>

      <div style={S.grid4}>
        <StatCard label="Avg R:R"    value={Number(avgRR).toFixed(2)}      color="#e5e7eb" />
        <StatCard label="Best Trade" value={`+$${Number(bestTrade).toFixed(2)}`}  color="#4ade80" />
        <StatCard label="Worst Trade" value={`-$${Math.abs(Number(worstTrade)).toFixed(2)}`} color="#f87171" />
        <StatCard label="Period"     value={`${days} days`}                color="#6b7280" />
      </div>

      {/* Equity curve */}
      {equityCurve.length > 1 && (
        <div style={S.card}>
          <div style={S.h3}>Equity Curve</div>
          <ResponsiveContainer width="100%" height={180}>
            <LineChart data={equityCurve}>
              <CartesianGrid stroke="#1f2937" />
              <XAxis dataKey="date" tick={{ fontSize: 10, fill: '#6b7280' }} tickLine={false} axisLine={false} />
              <YAxis tick={{ fontSize: 10, fill: '#6b7280' }} tickLine={false} axisLine={false} width={60} />
              <Tooltip
                contentStyle={{ background: '#111827', border: '1px solid #1f2937', borderRadius: 8, fontSize: 12 }}
                labelStyle={{ color: '#6b7280' }}
              />
              <Line dataKey="val" stroke="#38bdf8" dot={false} strokeWidth={2} />
            </LineChart>
          </ResponsiveContainer>
        </div>
      )}

      {/* Daily P&L bars */}
      {dailyPnl.length > 0 && (
        <div style={S.card}>
          <div style={S.h3}>Daily P&L</div>
          <ResponsiveContainer width="100%" height={160}>
            <BarChart data={dailyPnl}>
              <CartesianGrid stroke="#1f2937" vertical={false} />
              <XAxis dataKey="date" tick={{ fontSize: 10, fill: '#6b7280' }} tickLine={false} axisLine={false} />
              <YAxis tick={{ fontSize: 10, fill: '#6b7280' }} tickLine={false} axisLine={false} width={52} />
              <Tooltip
                contentStyle={{ background: '#111827', border: '1px solid #1f2937', borderRadius: 8, fontSize: 12 }}
                labelStyle={{ color: '#6b7280' }}
              />
              <ReferenceLine y={0} stroke="#374151" />
              <Bar dataKey="pnl" radius={[3, 3, 0, 0]}
                fill="#38bdf8"
                // colour each bar individually based on value
                label={false}
              />
            </BarChart>
          </ResponsiveContainer>
        </div>
      )}

      {/* Extra raw stats */}
      {Object.keys(stats).length > 0 && (
        <div style={S.card}>
          <div style={S.h3}>All Stats</div>
          <div style={{ display: 'grid', gridTemplateColumns: 'repeat(3,1fr)', gap: 10 }}>
            {Object.entries(stats)
              .filter(([k]) => !['equity_curve','equityCurve','daily_pnl','dailyPnl'].includes(k))
              .map(([k, v]) => (
                <div key={k} style={{ background: '#1f2937', borderRadius: 8, padding: '8px 12px' }}>
                  <div style={{ fontSize: 10, color: '#6b7280', textTransform: 'uppercase', letterSpacing: '0.04em' }}>
                    {k.replace(/_/g, ' ')}
                  </div>
                  <div style={{ fontSize: 14, fontWeight: 600, color: '#e5e7eb', marginTop: 2 }}>
                    {typeof v === 'number' ? v.toFixed(4) : String(v)}
                  </div>
                </div>
              ))}
          </div>
        </div>
      )}

    </div>
  )
}