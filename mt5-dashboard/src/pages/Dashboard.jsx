import { useEffect } from 'react'
import {
  LineChart, Line, XAxis, YAxis,
  Tooltip, ResponsiveContainer, CartesianGrid
} from 'recharts'

import useBotStore from '../store/botStore'
import NewsBar from '../components/NewsBar'
import RiskGauge from '../components/RiskGauge'
import { StatCard, DirectionBadge, ATRZoneBadge, TSSBar } from '../components/ui'

const S = {
  page:  { padding:'24px', display:'flex', flexDirection:'column', gap:20 },
  grid4: { display:'grid', gridTemplateColumns:'repeat(4,minmax(0,1fr))', gap:12 },
  grid2: { display:'grid', gridTemplateColumns:'repeat(2,minmax(0,1fr))', gap:16 },
  card:  { background:'#111827', border:'1px solid #1f2937', borderRadius:12, padding:16 },
  h3:    { fontSize:13, fontWeight:600, color:'#e5e7eb', marginBottom:12 },
  td:    { padding:'8px 12px', fontSize:12, color:'#d1d5db', borderBottom:'1px solid #1f2937' },
  th:    { padding:'8px 12px', fontSize:11, color:'#6b7280', textTransform:'uppercase', letterSpacing:'0.05em', borderBottom:'1px solid #374151', textAlign:'left' },
}

export default function Dashboard() {
  const { account, signals, positions, tradeFeed, fetchPositions } = useBotStore()

  const latest = signals[0]
  const sig    = latest?.signal || {}

  useEffect(() => { fetchPositions() }, [])

  const equityCurve = tradeFeed
    .filter(t => t.trade?.entry)
    .slice(0, 20)
    .reverse()
    .map((t, i) => ({
      i,
      val: parseFloat((account.balance + i * 0.5).toFixed(2))
    }))

  const totalOpenProfit = positions.reduce((s, p) => s + (p.profit || 0), 0)

  return (
    <div style={S.page}>

      {/* 📰 NEWS */}
      <NewsBar />

      {/* 📊 STATS */}
      <div style={S.grid4}>
        <StatCard label="Balance"
          value={`$${(account.balance||0).toLocaleString('en',{minimumFractionDigits:2})}`}
          color="#f1f5f9" />

        <StatCard label="Open P&L"
          value={`${totalOpenProfit>=0?'+':''}$${totalOpenProfit.toFixed(2)}`}
          color={totalOpenProfit>=0?'#4ade80':'#f87171'}
          sub={`${positions.length} position${positions.length!==1?'s':''} open`} />

        <StatCard label="Signals Today"
          value={signals.filter(s=>s.signal?.direction!==0).length}
          color="#93c5fd"
          sub={`${signals.length} bars evaluated`} />

        <StatCard label="Daily D/D"
          value={`${Math.abs(((account.balance||0)-(account.equity||0))/(account.balance||1)*100).toFixed(2)}%`}
          color="#fbbf24"
          sub="5% max limit" />
      </div>

      {/* 🔲 MAIN GRID */}
      <div style={S.grid2}>

        {/* 📡 SIGNAL */}
        <div style={S.card}>
          <div style={S.h3}>
            Latest signal
            {latest?.symbol && (
              <span style={{color:'#6b7280',marginLeft:6}}>
                {latest.symbol}
              </span>
            )}
          </div>

          {latest ? (
            <div style={{display:'flex',flexDirection:'column',gap:12}}>

              <div style={{display:'flex',gap:10}}>
                <DirectionBadge direction={sig.direction} />
                <ATRZoneBadge zone={sig.atr_zone} />
                <span style={{marginLeft:'auto',fontSize:11,color:'#6b7280'}}>
                  {latest.receivedAt}
                </span>
              </div>

              <div>
                <div style={{fontSize:11,color:'#6b7280'}}>TSS</div>
                <TSSBar score={sig.tss_score||0} />
              </div>

              <div style={{display:'grid',gridTemplateColumns:'repeat(3,1fr)',gap:8}}>
                {[
                  ['RSI',sig.rsi],['ADX',sig.adx],
                  ['ATR',sig.atr],['EMA50',sig.ema50],
                  ['EMA200',sig.ema200],['MACD',sig.macd_hist]
                ].map(([k,v])=>(
                  <div key={k} style={{background:'#1f2937',padding:8,borderRadius:8}}>
                    <div style={{fontSize:10,color:'#6b7280'}}>{k}</div>
                    <div style={{fontSize:13,fontWeight:500,color:'#e5e7eb',marginTop:2}}>
                      {(v||0).toFixed(4)}
                    </div>
                  </div>
                ))}
              </div>

            </div>
          ) : (
            <div style={{padding:'24px 0',textAlign:'center',color:'#4b5563',fontSize:12}}>
              No signal yet — waiting for the bot…
            </div>
          )}
        </div>

        {/* 📉 POSITIONS */}
        <div style={S.card}>
          <div style={S.h3}>Open positions ({positions.length})</div>

          {positions.length === 0 ? (
            <div style={{padding:'24px 0',textAlign:'center',color:'#4b5563',fontSize:12}}>
              No open positions
            </div>
          ) : (
            <table style={{width:'100%',borderCollapse:'collapse'}}>
              <thead>
                <tr>
                  {['Symbol','Type','Lot','P&L'].map(h=>(
                    <th key={h} style={S.th}>{h}</th>
                  ))}
                </tr>
              </thead>
              <tbody>
                {positions.map(p=>(
                  <tr key={p.ticket}
                    onMouseEnter={e=>e.currentTarget.style.background='#1f2937'}
                    onMouseLeave={e=>e.currentTarget.style.background='transparent'}
                    style={{transition:'background 0.1s'}}
                  >
                    <td style={{...S.td,fontWeight:600,color:'#e5e7eb'}}>{p.symbol}</td>
                    <td style={S.td}>
                      <DirectionBadge direction={p.type==='buy'?1:-1}/>
                    </td>
                    <td style={S.td}>{p.volume}</td>
                    <td style={{
                      ...S.td,
                      fontWeight:600,
                      color:(p.profit||0)>=0?'#4ade80':'#f87171'
                    }}>
                      {(p.profit||0)>=0?'+':''}{(p.profit||0).toFixed(2)}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          )}
        </div>

      </div>

      {/* ⚠️ RISK */}
      <RiskGauge />

      {/* 📈 CHART */}
      {equityCurve.length > 1 && (
        <div style={S.card}>
          <div style={S.h3}>Equity</div>
          <ResponsiveContainer width="100%" height={120}>
            <LineChart data={equityCurve}>
              <CartesianGrid stroke="#1f2937"/>
              <XAxis dataKey="i" hide />
              <YAxis tick={{fontSize:10,fill:'#6b7280'}} tickLine={false} axisLine={false} width={48} />
              <Tooltip contentStyle={{background:'#111827',border:'1px solid #1f2937',borderRadius:8,fontSize:12}} />
              <Line dataKey="val" stroke="#38bdf8" dot={false} strokeWidth={2}/>
            </LineChart>
          </ResponsiveContainer>
        </div>
      )}

      {/* 📡 FEED */}
      <div style={S.card}>
        <div style={S.h3}>Trade feed</div>
        {tradeFeed.length === 0 ? (
          <div style={{padding:'16px 0',textAlign:'center',color:'#4b5563',fontSize:12}}>
            No trades yet
          </div>
        ) : (
          <div style={{display:'flex',flexDirection:'column',gap:4}}>
            {tradeFeed.slice(0,10).map(t=>(
              <div key={t.id} style={{
                background:'#1f2937', borderRadius:6, padding:'6px 10px',
                fontSize:11, color:'#9ca3af', fontFamily:'monospace',
              }}>
                <span style={{color:'#4b5563',marginRight:8}}>{t.time}</span>
                {JSON.stringify(t.trade ?? t)}
              </div>
            ))}
          </div>
        )}
      </div>

    </div>
  )
}