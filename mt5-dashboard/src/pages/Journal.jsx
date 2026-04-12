import { useEffect, useState, useMemo } from 'react'
import useBotStore from '../store/botStore'
import { S, StatCard, Empty } from '../components/ui'

function useIsMobile(bp = 640) {
  const [m, setM] = useState(() => window.innerWidth < bp)
  useEffect(() => {
    const h = () => setM(window.innerWidth < bp)
    window.addEventListener('resize', h)
    return () => window.removeEventListener('resize', h)
  }, [bp])
  return m
}

function normalizeTrade(t) {
  // profit
  let profit = null
  if (t.profit !== undefined && t.profit !== null)
    profit = parseFloat(t.profit)
  else if (t.pnl !== undefined && t.pnl !== null)
    profit = parseFloat(t.pnl)
  else if (t.net_profit !== undefined)
    profit = parseFloat(t.net_profit)
  else if (t.sell_price !== undefined && t.buy_price !== undefined)
    profit = parseFloat(t.sell_price) - parseFloat(t.buy_price)

  // closed check
  const hasOutcome = t.outcome === 'WIN' || t.outcome === 'LOSS' || t.outcome === 'NEUTRAL'
  const isClosed =
    hasOutcome ||
    t.close_time  !== undefined ||
    t.close_price !== undefined ||
    t.price_close !== undefined ||
    t.sell_time   !== undefined ||
    t.sell_price  !== undefined ||
    t.is_expired  === true ||
    t.is_sold     === true

  if (!isClosed) return null

  // synthesise profit from outcome when not available
  if ((profit === null || isNaN(profit)) && hasOutcome) {
    profit = t.outcome === 'WIN' ? 1 : t.outcome === 'LOSS' ? -1 : 0
  }

  if (profit === null || isNaN(profit)) return null

  const openPrice  = parseFloat(t.open_price  ?? t.price_open  ?? t.entry_spot ?? t.buy_price  ?? 0)
  const closePrice = parseFloat(t.close_price ?? t.price_close ?? t.exit_spot  ?? t.sell_price ?? 0)
  const openTime   = t.open_time  ?? t.purchase_time ?? t.date_start  ?? '—'
  const closeTime  = t.close_time ?? t.sell_time     ?? t.date_expiry ?? '—'
  const type       = t.type ?? t.contract_type ?? t.direction ?? null

  return {
    ticket:     t.ticket ?? t.contract_id ?? t.id ?? '—',
    symbol:     t.symbol ?? t.underlying  ?? '—',
    type,
    volume:     t.volume ?? t.amount ?? t.stake ?? 0,
    openPrice,
    closePrice,
    openTime,
    closeTime,
    swap:       parseFloat(t.swap       ?? 0),
    commission: parseFloat(t.commission ?? 0),
    profit,
    outcome:    t.outcome ?? null,
  }
}

function NormBadge({ type }) {
  let label = '—', bg = '#374151', color = '#9ca3af'
  if (type !== null && type !== undefined) {
    if (typeof type === 'number') {
      label = type > 0 ? 'BUY' : 'SELL'
      bg    = type > 0 ? '#14532d' : '#450a0a'
      color = type > 0 ? '#4ade80' : '#f87171'
    } else {
      const u = String(type).toUpperCase()
      if (['BUY','CALL','RISE','UP'].includes(u))   { label = 'BUY';  bg = '#14532d'; color = '#4ade80' }
      if (['SELL','PUT','FALL','DOWN'].includes(u)) { label = 'SELL'; bg = '#450a0a'; color = '#f87171' }
      if (!['BUY','CALL','RISE','UP','SELL','PUT','FALL','DOWN'].includes(u)) label = u
    }
  }
  return (
    <span style={{ fontSize: 10, fontWeight: 700, padding: '2px 7px', borderRadius: 4, background: bg, color, whiteSpace: 'nowrap' }}>
      {label}
    </span>
  )
}

function OutcomeBadge({ outcome }) {
  if (!outcome) return null
  const bg    = outcome === 'WIN' ? '#14532d' : outcome === 'LOSS' ? '#450a0a' : '#1f2937'
  const color = outcome === 'WIN' ? '#4ade80' : outcome === 'LOSS' ? '#f87171' : '#9ca3af'
  return (
    <span style={{ fontSize: 10, fontWeight: 700, padding: '2px 7px', borderRadius: 4, background: bg, color }}>
      {outcome}
    </span>
  )
}

function SortTh({ label, field, sort, onSort }) {
  const active = sort.field === field
  return (
    <th onClick={() => onSort(field)} style={{ ...S.th, cursor: 'pointer', userSelect: 'none', color: active ? '#e5e7eb' : '#6b7280', whiteSpace: 'nowrap' }}>
      {label} <span style={{ opacity: active ? 1 : 0.3 }}>{active ? (sort.asc ? 'A' : 'D') : 'A'}</span>
    </th>
  )
}

export default function Journal() {
  const { journal, journalLoading, fetchJournal } = useBotStore()
  const [limit,  setLimit]  = useState(50)
  const [search, setSearch] = useState('')
  const [sort,   setSort]   = useState({ field: 'openTime', asc: false })
  const isMobile = useIsMobile()

  useEffect(() => { fetchJournal(limit) }, [limit])

  const closed = useMemo(() => {
    const normalized = journal.map(normalizeTrade).filter(Boolean)
    if (!search.trim()) return normalized
    const q = search.trim().toLowerCase()
    return normalized.filter(t =>
      String(t.symbol).toLowerCase().includes(q) ||
      String(t.ticket).toLowerCase().includes(q)  ||
      String(t.type  ).toLowerCase().includes(q)
    )
  }, [journal, search])

  const sorted = useMemo(() => {
    const arr = [...closed]
    arr.sort((a, b) => {
      let av = a[sort.field], bv = b[sort.field]
      if (av === '—') av = ''
      if (bv === '—') bv = ''
      if (typeof av === 'string') av = av.toLowerCase()
      if (typeof bv === 'string') bv = bv.toLowerCase()
      if (av < bv) return sort.asc ? -1 : 1
      if (av > bv) return sort.asc ? 1 : -1
      return 0
    })
    return arr
  }, [closed, sort])

  const handleSort = (field) =>
    setSort(s => ({ field, asc: s.field === field ? !s.asc : true }))

  const wins      = closed.filter(t => t.outcome === 'WIN'  || (!t.outcome && t.profit > 0))
  const losses    = closed.filter(t => t.outcome === 'LOSS' || (!t.outcome && t.profit < 0))
  const netProfit = closed.reduce((s, t) => s + (t.profit || 0), 0)
  const winRate   = closed.length ? ((wins.length / closed.length) * 100).toFixed(1) : '—'
  const avgWin    = wins.length   ? (wins.reduce((s,t)   => s + t.profit, 0) / wins.length).toFixed(2)   : '0.00'
  const avgLoss   = losses.length ? (losses.reduce((s,t) => s + t.profit, 0) / losses.length).toFixed(2) : '0.00'

  const grid4 = {
    display: 'grid',
    gridTemplateColumns: isMobile ? 'repeat(2,minmax(0,1fr))' : 'repeat(4,minmax(0,1fr))',
    gap: 10,
  }

  return (
    <div style={S.page}>

      <div style={grid4}>
        <StatCard label="Closed Trades" value={closed.length} color="#93c5fd" />
        <StatCard
          label="Net P&L"
          value={`${netProfit >= 0 ? '+' : ''}$${netProfit.toFixed(2)}`}
          color={netProfit >= 0 ? '#4ade80' : '#f87171'}
        />
        <StatCard label="Win Rate" value={`${winRate}%`} color="#fbbf24" sub={`${wins.length}W / ${losses.length}L`} />
        <StatCard label="Avg Win / Loss" value={`$${avgWin} / $${avgLoss}`} color="#e5e7eb" />
      </div>

      <div style={S.card}>
        <div style={{ display: 'flex', flexWrap: 'wrap', alignItems: 'center', gap: 8, marginBottom: 14 }}>
          <span style={S.h3}>Trade Journal</span>
          <input
            value={search}
            onChange={e => setSearch(e.target.value)}
            placeholder="Filter symbol / ticket..."
            style={{
              background: '#1f2937', border: '1px solid #374151',
              borderRadius: 7, color: '#e5e7eb', fontSize: 11,
              padding: '5px 10px', outline: 'none',
              width: isMobile ? '100%' : 170,
            }}
          />
          <div style={{ marginLeft: 'auto', display: 'flex', flexWrap: 'wrap', gap: 4, alignItems: 'center' }}>
            <span style={{ fontSize: 11, color: '#6b7280' }}>Show</span>
            {[25, 50, 100, 200].map(n => (
              <button key={n} onClick={() => setLimit(n)} style={{
                padding: '4px 10px', borderRadius: 6, border: 'none',
                cursor: 'pointer', fontSize: 11, minHeight: 32,
                background: limit === n ? '#1f2937' : 'transparent',
                color:      limit === n ? '#f1f5f9' : '#6b7280',
              }}>{n}</button>
            ))}
            <button onClick={() => fetchJournal(limit)} style={{
              padding: '4px 12px', borderRadius: 7, border: '1px solid #1f2937',
              background: 'transparent', color: '#6b7280', fontSize: 11,
              cursor: 'pointer', marginLeft: 4, minHeight: 32,
            }}>Refresh</button>
          </div>
        </div>

        {journalLoading ? (
          <div style={{ padding: '32px 0', textAlign: 'center', color: '#4b5563', fontSize: 12 }}>
            Loading journal...
          </div>
        ) : sorted.length === 0 ? (
          <Empty icon="📓" text={search ? 'No trades match your filter' : 'No closed trades yet'} />
        ) : (
          <div style={{ overflowX: 'auto', WebkitOverflowScrolling: 'touch' }}>
            <table style={{ width: '100%', borderCollapse: 'collapse', minWidth: 680 }}>
              <thead>
                <tr>
                  {[
                    { label: 'Ticket',      field: 'ticket'     },
                    { label: 'Symbol',      field: 'symbol'     },
                    { label: 'Type',        field: 'type'       },
                    { label: 'Open Price',  field: 'openPrice'  },
                    { label: 'Close Price', field: 'closePrice' },
                    { label: 'Open Time',   field: 'openTime'   },
                    { label: 'Outcome',     field: 'outcome'    },
                    { label: 'P&L',         field: 'profit'     },
                  ].map(col => (
                    <SortTh key={col.field} {...col} sort={sort} onSort={handleSort} />
                  ))}
                </tr>
              </thead>
              <tbody>
                {sorted.map((t, i) => (
                  <tr
                    key={`${t.ticket}-${i}`}
                    onMouseEnter={e => e.currentTarget.style.background = '#1f2937'}
                    onMouseLeave={e => e.currentTarget.style.background = 'transparent'}
                    style={{ transition: 'background 0.1s' }}
                  >
                    <td style={{ ...S.td, color: '#6b7280', fontFamily: 'monospace', fontSize: 10 }}>
                      {String(t.ticket).slice(0, 8)}...
                    </td>
                    <td style={{ ...S.td, fontWeight: 600, color: '#e5e7eb' }}>{t.symbol}</td>
                    <td style={S.td}><NormBadge type={t.type} /></td>
                    <td style={{ ...S.td, fontFamily: 'monospace' }}>
                      {t.openPrice ? t.openPrice.toFixed(5) : '—'}
                    </td>
                    <td style={{ ...S.td, fontFamily: 'monospace' }}>
                      {t.closePrice ? t.closePrice.toFixed(5) : '—'}
                    </td>
                    <td style={{ ...S.td, color: '#6b7280', fontSize: 11 }}>{t.openTime}</td>
                    <td style={S.td}><OutcomeBadge outcome={t.outcome} /></td>
                    <td style={{ ...S.td, fontWeight: 600, color: t.profit >= 0 ? '#4ade80' : '#f87171' }}>
                      {t.profit >= 0 ? '+' : ''}${t.profit.toFixed(2)}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>

            <div style={{
              padding: '10px 12px', borderTop: '1px solid #1f2937',
              display: 'flex', justifyContent: 'space-between',
              alignItems: 'center', fontSize: 12, flexWrap: 'wrap', gap: 8,
            }}>
              <span style={{ color: '#4b5563', fontSize: 11 }}>
                {sorted.length} of {closed.length} trades{search ? ' (filtered)' : ''}
              </span>
              <div style={{ display: 'flex', gap: 8 }}>
                <span style={{ color: '#6b7280' }}>Net P&L ({closed.length} trades):</span>
                <span style={{ fontWeight: 700, color: netProfit >= 0 ? '#4ade80' : '#f87171' }}>
                  {netProfit >= 0 ? '+' : ''}${netProfit.toFixed(2)}
                </span>
              </div>
            </div>
          </div>
        )}
      </div>
    </div>
  )
}