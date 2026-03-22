/**
 * RiskGauge — visual risk dashboard widget.
 * Shows current daily drawdown vs limit, open trade count, session stats.
 */

import useBotStore from '../store/botStore'

function GaugeBar({ label, current, max, color, unit = '%' }) {
  const pct   = Math.min((current / max) * 100, 100)
  const safe  = pct < 60
  const warn  = pct >= 60 && pct < 85
  const bar_c = safe ? '#22c55e' : warn ? '#f59e0b' : '#ef4444'

  return (
    <div style={{ marginBottom: 14 }}>
      <div style={{ display: 'flex', justifyContent: 'space-between',
        fontSize: 11, marginBottom: 5 }}>
        <span style={{ color: '#9ca3af' }}>{label}</span>
        <span style={{ color: bar_c, fontWeight: 600 }}>
          {current.toFixed(2)}{unit} / {max}{unit}
        </span>
      </div>
      <div style={{ height: 6, background: '#1f2937', borderRadius: 3, overflow: 'hidden' }}>
        <div style={{
          width: `${pct}%`, height: '100%',
          background: bar_c, borderRadius: 3,
          transition: 'width .4s, background .3s',
        }} />
      </div>
    </div>
  )
}

function RuleRow({ label, value, ok }) {
  return (
    <div style={{ display: 'flex', justifyContent: 'space-between',
      alignItems: 'center', padding: '5px 0',
      borderBottom: '1px solid #1f2937', fontSize: 12 }}>
      <span style={{ color: '#9ca3af' }}>{label}</span>
      <span style={{ color: ok ? '#4ade80' : '#f87171', fontWeight: 500 }}>
        {value}
      </span>
    </div>
  )
}

export default function RiskGauge() {
  const { account, positions } = useBotStore()

  const balance   = account.balance  || 0
  const equity    = account.equity   || 0
  const dd_pct    = balance > 0 ? Math.max(0, (balance - equity) / balance * 100) : 0
  const open_cnt  = positions.length
  const float_pnl = positions.reduce((s, p) => s + (p.profit || 0), 0)

  const DD_LIMIT      = 5.0
  const MAX_POSITIONS = 3

  return (
    <div style={{
      background: '#111827', border: '1px solid #1f2937',
      borderRadius: 12, padding: 16,
    }}>
      <div style={{ fontSize: 13, fontWeight: 600, color: '#e5e7eb', marginBottom: 14 }}>
        Live risk gauge
      </div>

      <GaugeBar
        label="Daily drawdown"
        current={dd_pct}
        max={DD_LIMIT}
        unit="%"
      />
      <GaugeBar
        label="Open positions"
        current={open_cnt}
        max={MAX_POSITIONS}
        unit=""
      />

      <div style={{ marginTop: 12 }}>
        <RuleRow
          label="DD within limit"
          value={dd_pct <= DD_LIMIT ? `${dd_pct.toFixed(2)}% ✓` : `${dd_pct.toFixed(2)}% — STOP`}
          ok={dd_pct <= DD_LIMIT}
        />
        <RuleRow
          label="Positions OK"
          value={`${open_cnt} / ${MAX_POSITIONS}`}
          ok={open_cnt < MAX_POSITIONS}
        />
        <RuleRow
          label="Float P&L"
          value={`${float_pnl >= 0 ? '+' : ''}$${float_pnl.toFixed(2)}`}
          ok={float_pnl >= 0}
        />
        <RuleRow
          label="Equity vs balance"
          value={`$${equity.toFixed(2)}`}
          ok={equity >= balance * 0.97}
        />
      </div>

      {dd_pct > DD_LIMIT * 0.8 && (
        <div style={{
          marginTop: 12, background: '#1c0a0a',
          border: '1px solid #3b0000', borderRadius: 8,
          padding: '8px 10px', fontSize: 11, color: '#f87171',
        }}>
          ⚠ Daily drawdown approaching limit — reduce or stop trading.
        </div>
      )}
    </div>
  )
}