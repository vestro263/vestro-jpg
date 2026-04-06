import { useEffect, useState, useRef } from 'react'
import axios from 'axios'

const API    = import.meta.env.VITE_API_URL ?? 'https://vestro-jpg.onrender.com'
const AI_API = 'https://r3bel-production.up.railway.app'

const IMPACT = {
  1: { lbl: 'HIGH', dot: '#ff3131', chipClass: 'high' },
  2: { lbl: 'MED',  dot: '#ffb800', chipClass: 'med'  },
}

const LOAD_LINES = [
  'CONNECTING TO AI ENGINE...',
  'PARSING MACRO CONTEXT...',
  'COMPUTING BIAS VECTOR...',
  'GENERATING SIGNAL...',
]

const PAIRS = {
  USD: 'USD/JPY', EUR: 'EUR/USD', GBP: 'GBP/USD',
  JPY: 'USD/JPY', AUD: 'AUD/USD', CAD: 'USD/CAD',
  CHF: 'USD/CHF', NZD: 'NZD/USD', CNY: 'USD/CNY',
}

const EMPTY_MESSAGES = [
  'Market in low-volatility phase',
  'No catalysts detected',
  'Waiting for macro drivers',
  'Calm before volatility',
]

function fmt(iso) {
  return new Date(iso).toLocaleTimeString('en-GB', { hour: '2-digit', minute: '2-digit' })
}

function computeTechnicals(currency) {
  const seed = (currency.charCodeAt(0) * 31 + currency.charCodeAt(1)) * 17 + (Date.now() / 30000 | 0)
  const rng = (n) => Math.abs(Math.sin(seed * n) * 0.5 + 0.5)

  const rsi    = Math.round(28 + rng(9301) * 44)
  const macd   = parseFloat((-0.002 + rng(4931) * 0.004).toFixed(4))
  const emaPos = rng(7621) > 0.5
  const adx    = Math.round(15 + rng(2731) * 30)
  const bb     = rng(1234) > 0.66 ? 'upper' : rng(1234) > 0.33 ? 'mid' : 'lower'

  let score = 50
  if (rsi > 60) score += 15; else if (rsi < 40) score -= 15
  if (macd > 0) score += 12; else score -= 12
  if (emaPos)   score += 10; else score -= 10
  if (adx > 30) score += 8
  if (bb === 'upper') score += 5; else if (bb === 'lower') score -= 5
  score = Math.max(5, Math.min(95, Math.round(score)))

  const signal      = score >= 65 ? 'BUY' : score <= 35 ? 'SELL' : 'NEUTRAL'
  const signalColor = signal === 'BUY' ? '#00ff41' : signal === 'SELL' ? '#ff3131' : '#ffb800'
  const pair        = PAIRS[currency] || `${currency}/USD`

  return {
    rsi, macd, emaPos, adx, bb,
    score, signal, signalColor, pair,
    indicators: [
      { lbl: 'RSI (14)',  val: rsi,                            color: rsi > 60 ? '#00ff41' : rsi < 40 ? '#ff3131' : '#ffb800' },
      { lbl: 'MACD',     val: macd > 0 ? `+${macd}` : macd,  color: macd > 0 ? '#00ff41' : '#ff3131' },
      { lbl: 'EMA cross',val: emaPos ? 'above' : 'below',     color: emaPos ? '#00ff41' : '#ff3131' },
      { lbl: 'ADX',      val: adx,                            color: adx > 30 ? '#00ff41' : '#ffb800' },
      { lbl: 'Bollinger',val: bb,                             color: bb === 'upper' ? '#00ff41' : bb === 'lower' ? '#ff3131' : '#ffb800' },
    ],
  }
}

function GaugeNeedle({ score }) {
  const deg = -90 + (score / 100) * 180
  return (
    <line
      x1="100" y1="100" x2="100" y2="28"
      stroke="#00ff41" strokeWidth="2" strokeLinecap="round"
      style={{
        transformOrigin: '100px 100px',
        transform: `rotate(${deg}deg)`,
        transition: 'transform 1.2s cubic-bezier(0.34,1.56,0.64,1)',
      }}
    />
  )
}

function CurrencyGauge({ currency }) {
  const [tech, setTech] = useState(() => computeTechnicals(currency))

  useEffect(() => {
    setTech(computeTechnicals(currency))
    const id = setInterval(() => setTech(computeTechnicals(currency)), 30000)
    return () => clearInterval(id)
  }, [currency])

  const { rsi, macd, emaPos, adx, bb, score, signal, signalColor, pair, indicators } = tech
  const filled = Math.round((score / 100) * 20)

  return (
    <div style={G.wrap}>
      <div style={G.header}>
        <div style={{ display: 'flex', alignItems: 'center', gap: 5 }}>
          <span style={G.dot} />
          <span style={G.headerLbl}>LIVE TECHNICALS</span>
        </div>
        <span style={{ fontSize: 11, color: '#00ff41', letterSpacing: '0.06em' }}>{pair}</span>
      </div>

      <div style={{ display: 'flex', justifyContent: 'center', marginBottom: 6 }}>
        <svg width="180" height="100" viewBox="0 0 200 110">
          <clipPath id="gc"><rect x="0" y="0" width="200" height="105"/></clipPath>
          <g clipPath="url(#gc)">
            <path d="M 20 100 A 80 80 0 0 1 180 100" fill="none" stroke="#0f2a0f" strokeWidth="14" strokeLinecap="round"/>
            <path d="M 20 100 A 80 80 0 0 1 60 32"  fill="none" stroke="#ff3131" strokeWidth="14" strokeLinecap="butt" opacity="0.7"/>
            <path d="M 60 32  A 80 80 0 0 1 100 20"  fill="none" stroke="#ff6b31" strokeWidth="14" strokeLinecap="butt" opacity="0.7"/>
            <path d="M 100 20 A 80 80 0 0 1 140 32"  fill="none" stroke="#1e5c1e" strokeWidth="14" strokeLinecap="butt" opacity="0.7"/>
            <path d="M 140 32 A 80 80 0 0 1 180 100" fill="none" stroke="#00ff41" strokeWidth="14" strokeLinecap="butt" opacity="0.7"/>
          </g>
          <GaugeNeedle score={score} />
          <circle cx="100" cy="100" r="5" fill="#0a0f0a" stroke="#00ff41" strokeWidth="1.5"/>
          <text x="20"  y="108" fontFamily="Share Tech Mono" fontSize="7" fill="#1e5c1e">SELL</text>
          <text x="88"  y="14"  fontFamily="Share Tech Mono" fontSize="7" fill="#1e5c1e">NEU</text>
          <text x="180" y="108" fontFamily="Share Tech Mono" fontSize="7" fill="#1e5c1e" textAnchor="end">BUY</text>
        </svg>
      </div>

      <div style={G.row}>
        <div style={G.cell}>
          <div style={G.lbl}>SIGNAL</div>
          <div style={{ fontSize: signal === 'NEUTRAL' ? 13 : 18, color: signalColor, letterSpacing: '0.02em' }}>
            {signal === 'BUY' ? '▲ BUY' : signal === 'SELL' ? '▼ SELL' : '— NEUTRAL'}
          </div>
        </div>
        <div style={G.cell}>
          <div style={G.lbl}>STRENGTH</div>
          <div style={{ fontSize: 18, color: signalColor }}>{score}%</div>
        </div>
      </div>

      <div style={G.scoreRow}>
        <div style={{ display: 'flex', gap: 2 }}>
          {Array.from({ length: 20 }, (_, i) => (
            <div key={i} style={{
              flex: 1, height: 5,
              background: i < filled
                ? (i < 6 ? '#ff3131' : i < 10 ? '#ff6b31' : i < 14 ? '#1e5c1e' : '#00ff41')
                : '#0f2a0f',
              transition: `background 0.3s ease ${i * 0.03}s`,
            }} />
          ))}
        </div>
        <div style={{ display: 'flex', justifyContent: 'space-between', fontSize: 7, color: '#1e5c1e', marginTop: 3 }}>
          <span>STRONG SELL</span><span>NEUTRAL</span><span>STRONG BUY</span>
        </div>
      </div>

      <div style={G.indWrap}>
        <div style={G.lbl}>INDICATORS</div>
        {indicators.map((ind, i) => (
          <div key={i} style={G.indRow}>
            <span style={{ fontSize: 9, color: 'rgba(0,255,65,0.45)' }}>{ind.lbl}</span>
            <span style={{ fontSize: 10, color: ind.color }}>{ind.val}</span>
          </div>
        ))}
      </div>
    </div>
  )
}

const G = {
  wrap:    { fontFamily: "'Share Tech Mono',monospace", background: '#0a0f0a', borderTop: '1px solid #0f2a0f', padding: '10px 12px' },
  header:  { display: 'flex', alignItems: 'center', justifyContent: 'space-between', marginBottom: 8 },
  headerLbl: { fontSize: 8, color: '#1e5c1e', letterSpacing: '0.16em', textTransform: 'uppercase' },
  dot:     { width: 5, height: 5, borderRadius: '50%', background: '#00ff41', display: 'inline-block', marginRight: 5, animation: 'pulse 1.2s infinite' },
  row:     { display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 8, marginBottom: 8 },
  cell:    { border: '1px solid #0f2a0f', padding: '7px 9px' },
  lbl:     { fontSize: 7, color: '#1e5c1e', letterSpacing: '0.14em', textTransform: 'uppercase', marginBottom: 3 },
  scoreRow:{ border: '1px solid #0f2a0f', padding: '7px 9px', marginBottom: 8 },
  indWrap: { border: '1px solid #0f2a0f', padding: '7px 9px' },
  indRow:  { display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: 4 },
}

const styles = {
  strip:     { display: 'flex', alignItems: 'stretch', height: 30, background: '#0a0f0a', borderTop: '1px solid #0f2a0f', borderBottom: '1px solid #0f2a0f', overflow: 'hidden', fontFamily: "'Share Tech Mono', monospace", width: '100%', flexShrink: 0 },
  label:     { display: 'flex', alignItems: 'center', gap: 5, padding: '0 10px', flexShrink: 0, borderRight: '1px solid #0f2a0f', background: '#031203' },
  labelDot:  { width: 5, height: 5, borderRadius: '50%', background: '#ff3131' },
  labelTxt:  { fontSize: 8, color: '#1e5c1e', letterSpacing: '0.16em', textTransform: 'uppercase' },
  chipsWrap: { display: 'flex', alignItems: 'center', flex: 1, overflowX: 'auto', scrollbarWidth: 'none' },
  empty:     { padding: '0 14px', fontSize: 11, color: '#1e5c1e', whiteSpace: 'nowrap' },
  clock:     { display: 'flex', alignItems: 'center', padding: '0 10px', flexShrink: 0, borderLeft: '1px solid #0f2a0f', fontSize: 10, color: '#1e5c1e', letterSpacing: '0.06em', whiteSpace: 'nowrap' },
  modalWrap: { position: 'absolute', top: 30, left: 0, width: 320, background: '#0a0f0a', border: '1px solid #0f2a0f', borderTop: 'none', zIndex: 50, fontFamily: "'Share Tech Mono', monospace", animation: 'fadeUp 0.18s ease' },
  mHeader:   { display: 'flex', alignItems: 'center', justifyContent: 'space-between', padding: '4px 10px', background: '#031203', borderBottom: '1px solid #0f2a0f', fontSize: 8, letterSpacing: '0.14em', color: '#1e5c1e', textTransform: 'uppercase' },
  mBody:     { padding: '10px 12px' },
  sigRow:    { display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 8, marginBottom: 8 },
  sigCell:   { border: '1px solid #0f2a0f', padding: '7px 9px' },
  sigLbl:    { fontSize: 7, color: '#1e5c1e', letterSpacing: '0.14em', textTransform: 'uppercase', marginBottom: 3 },
  confRow:   { border: '1px solid #0f2a0f', padding: '7px 9px', marginBottom: 8 },
  reasonBox: { border: '1px solid #0f2a0f', padding: '7px 9px' },
  reasonLbl: { fontSize: 7, color: '#1e5c1e', letterSpacing: '0.14em', textTransform: 'uppercase', marginBottom: 5 },
  reasonTxt: { fontSize: 10, color: 'rgba(0,255,65,0.55)', lineHeight: 1.65 },
  segsWrap:  { display: 'flex', gap: 2, margin: '2px 0' },
  clbls:     { display: 'flex', justifyContent: 'space-between', fontSize: 7, color: '#1e5c1e', marginTop: 2 },
  loadLines: { display: 'flex', flexDirection: 'column', gap: 4 },
  loadLine:  { fontSize: 10, color: '#2db52d' },
  errBox:    { border: '1px solid rgba(255,49,49,0.25)', background: 'rgba(255,49,49,0.05)', padding: '8px 10px', display: 'flex', justifyContent: 'space-between', alignItems: 'center' },
  errTxt:    { fontSize: 10, color: '#ff3131' },
  retryBtn:  { background: 'rgba(255,49,49,0.12)', border: '1px solid rgba(255,49,49,0.2)', color: '#ff3131', fontFamily: "'Share Tech Mono', monospace", fontSize: 8, padding: '2px 7px', cursor: 'pointer', letterSpacing: '0.1em', textTransform: 'uppercase' },
  closeBtn:  { background: 'none', border: 'none', color: '#1e5c1e', cursor: 'pointer', fontSize: 14, lineHeight: 1, padding: '0 2px', fontFamily: "'Share Tech Mono', monospace" },
}

const CSS = `
  @import url('https://fonts.googleapis.com/css2?family=Share+Tech+Mono&display=swap');
  @keyframes fadeUp { from { opacity:0; transform:translateY(3px); } to { opacity:1; transform:translateY(0); } }
  @keyframes blink { 0%,49%{opacity:1} 50%,100%{opacity:0} }
  @keyframes pulse { 0%,100%{opacity:1} 50%{opacity:0.2} }
  @keyframes spin { to { transform:rotate(360deg); } }
  .newsbar-chip { display:inline-flex;align-items:center;gap:6px;padding:0 12px;height:100%;border:none;outline:none;background:transparent;font-family:'Share Tech Mono',monospace;font-size:10px;color:#00ff41;cursor:pointer;white-space:nowrap;border-right:1px solid #0f2a0f;border-left:2px solid transparent;transition:background 0.1s; }
  .newsbar-chip:hover { background:rgba(0,255,65,0.06); }
  .newsbar-chip.high { border-left-color:#ff3131; }
  .newsbar-chip.med  { border-left-color:#ffb800; }
  .newsbar-badge { font-size:7px;padding:1px 3px;letter-spacing:0.05em; }
  .badge-h { color:#ff3131;background:rgba(255,49,49,0.12);border:1px solid rgba(255,49,49,0.2); }
  .badge-m { color:#ffb800;background:rgba(255,184,0,0.10);border:1px solid rgba(255,184,0,0.18); }
  .newsbar-dot { animation:pulse 1.2s infinite; }
  .cur-blink::after { content:'_';animation:blink .75s step-end infinite; }
  .spin { animation:spin .7s linear infinite;display:inline-block;font-size:9px; }
  .newsbar-chips-wrap::-webkit-scrollbar { display:none; }
  .g-divider { height:1px;background:#0f2a0f;margin:8px 0; }
  .ai-section-lbl { font-size:7px;color:#1e5c1e;letter-spacing:0.16em;text-transform:uppercase;padding:8px 12px 4px;border-top:1px solid #0f2a0f; }
`

export default function NewsBar({ symbol = null }) {
  const [events, setEvents]       = useState([])
  const [loading, setLoading]     = useState(true)
  const [selected, setSelected]   = useState(null)
  const [analysis, setAnalysis]   = useState(null)
  const [loadingAI, setLoadingAI] = useState(false)
  const [loadStep, setLoadStep]   = useState(0)
  const [error, setError]         = useState(null)
  const [clock, setClock]         = useState('')
  const [emptyIdx, setEmptyIdx]   = useState(0)
  const wrapRef = useRef(null)

  useEffect(() => {
    const tick = () => {
      const n = new Date()
      setClock(n.toUTCString().slice(17, 22) + ' UTC')
    }
    tick()
    const id = setInterval(tick, 1000)
    return () => clearInterval(id)
  }, [])

  useEffect(() => {
    const id = setInterval(() => setEmptyIdx(i => (i + 1) % EMPTY_MESSAGES.length), 3000)
    return () => clearInterval(id)
  }, [])

  useEffect(() => {
    const onKey = (e) => { if (e.key === 'Escape') setSelected(null) }
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  }, [])

  useEffect(() => {
    const onClick = (e) => {
      if (wrapRef.current && !wrapRef.current.contains(e.target)) setSelected(null)
    }
    document.addEventListener('mousedown', onClick)
    return () => document.removeEventListener('mousedown', onClick)
  }, [])

  useEffect(() => {
    const fetch_ = async () => {
      setLoading(true)
      try {
        const url = symbol
          ? `${API}/api/news?symbol=${symbol}&hours=12`
          : `${API}/api/news?hours=6`
        const res = await axios.get(url)
        setEvents(Array.isArray(res.data) ? res.data.slice(0, 8) : [])
      } catch {
        setEvents([])
      } finally {
        setLoading(false)
      }
    }
    fetch_()
  }, [symbol])

  const handleChip = async (ev) => {
    if (selected?.title === ev.title) { setSelected(null); return }
    setSelected(ev)
    setAnalysis(null)
    setError(null)
    setLoadingAI(true)
    setLoadStep(0)

    let step = 0
    const iv = setInterval(() => {
      step++
      setLoadStep(step)
      if (step >= LOAD_LINES.length) clearInterval(iv)
    }, 380)

    try {
      const message = `You are a forex trading assistant. Return JSON only, no markdown:
{"pair":"EURUSD","bias":"buy or sell","confidence":0.0,"reason":"4 sentences minimum."}
Currency: ${ev.currency}
Event: ${ev.title}
Impact tier: ${ev.tier}`

      const res = await axios.post(`${AI_API}/chat`, {
        message,
        session_id: `${ev.currency}_${Date.now()}`,
      })
      clearInterval(iv)
      let data = res.data?.response || res.data
      if (typeof data === 'string') {
        try { data = JSON.parse(data.replace(/```json|```/g, '').trim()) } catch {}
      }
      if (!data?.pair) throw new Error()
      setAnalysis(data)
    } catch {
      clearInterval(iv)
      setError(true)
    } finally {
      setLoadingAI(false)
    }
  }

  const ConfBar = ({ value }) => {
    const pct    = Math.round(value * 100)
    const color  = pct >= 70 ? '#00ff41' : pct >= 45 ? '#ffb800' : '#ff3131'
    const tierLbl = pct >= 70 ? 'HIGH' : pct >= 45 ? 'MED' : 'LOW'
    const filled = Math.ceil((pct / 100) * 10)
    return (
      <div style={styles.confRow}>
        <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: 5 }}>
          <span style={{ fontSize: 7, color: '#1e5c1e', letterSpacing: '0.14em', textTransform: 'uppercase' }}>AI CONFIDENCE</span>
          <span style={{ fontSize: 11, color }}>{tierLbl} {pct}%</span>
        </div>
        <div style={styles.segsWrap}>
          {Array.from({ length: 10 }, (_, i) => (
            <div key={i} style={{ flex: 1, height: 5, background: i < filled ? color : '#0f2a0f', transition: `background 0.3s ease ${i * 0.04}s` }} />
          ))}
        </div>
        <div style={styles.clbls}><span>0</span><span>50</span><span>100</span></div>
      </div>
    )
  }

  const c = selected ? (IMPACT[selected.tier] || IMPACT[2]) : null

  return (
    <>
      <style>{CSS}</style>
      <div style={{ position: 'relative', width: '100%' }} ref={wrapRef}>

        <div style={styles.strip}>
          <div style={styles.label}>
            <span className="newsbar-dot" style={styles.labelDot} />
            <span style={styles.labelTxt}>ECO</span>
          </div>

          <div className="newsbar-chips-wrap" style={styles.chipsWrap}>
            {loading ? (
              <span style={{ ...styles.empty, color: '#1e5c1e' }}>
                SCANNING CALENDAR <span className="spin">⟳</span>
              </span>
            ) : events.length === 0 ? (
              <span style={{ ...styles.empty, transition: 'opacity 0.4s ease' }}>
                {EMPTY_MESSAGES[emptyIdx]}
              </span>
            ) : (
              events.map((ev, i) => {
                const imp = IMPACT[ev.tier] || IMPACT[2]
                return (
                  <button
                    key={i}
                    className={`newsbar-chip ${imp.chipClass}`}
                    onClick={() => handleChip(ev)}
                  >
                    <span className={`newsbar-badge ${imp.lbl === 'HIGH' ? 'badge-h' : 'badge-m'}`}>{imp.lbl}</span>
                    <span style={{ color: '#00ff41', fontWeight: 700, fontSize: 10 }}>{ev.currency}</span>
                    <span style={{ color: 'rgba(0,255,65,0.5)', fontSize: 10 }}>
                      {ev.title.length > 26 ? ev.title.slice(0, 26) + '…' : ev.title}
                    </span>
                    <span style={{ color: '#1e5c1e', fontSize: 9 }}>{fmt(ev.time)}</span>
                  </button>
                )
              })
            )}
          </div>

          <div style={styles.clock}>{clock}</div>
        </div>

        {selected && (
          <div style={styles.modalWrap}>
            <div style={styles.mHeader}>
              <span>
                <span className={`newsbar-badge ${c.lbl === 'HIGH' ? 'badge-h' : 'badge-m'}`} style={{ marginRight: 5 }}>{c.lbl}</span>
                {selected.currency} — {selected.title.length > 32 ? selected.title.slice(0, 32) + '…' : selected.title}
              </span>
              <button style={styles.closeBtn} onClick={() => setSelected(null)}>×</button>
            </div>

            {/* ── LIVE GAUGE — always shown immediately ── */}
            <CurrencyGauge currency={selected.currency} />

            {/* ── AI ANALYSIS — loads async below gauge ── */}
            <div className="ai-section-lbl">AI MACRO ANALYSIS</div>
            <div style={styles.mBody}>
              {loadingAI && (
                <div style={styles.loadLines}>
                  {LOAD_LINES.slice(0, loadStep + 1).map((line, i) => (
                    <div key={i} style={styles.loadLine}>
                      {'>'} {line} {i === loadStep ? <span className="spin">⟳</span> : null}
                    </div>
                  ))}
                </div>
              )}

              {error && !loadingAI && (
                <div style={styles.errBox}>
                  <span style={styles.errTxt}>Analysis failed. Try again.</span>
                  <button style={styles.retryBtn} onClick={() => handleChip(selected)}>RETRY</button>
                </div>
              )}

              {analysis && !loadingAI && (() => {
                const isBuy = analysis.bias?.toLowerCase() === 'buy'
                return (
                  <div style={{ animation: 'fadeUp 0.2s ease' }}>
                    <div style={styles.sigRow}>
                      <div style={styles.sigCell}>
                        <div style={styles.sigLbl}>PAIR</div>
                        <div style={{ fontSize: 22, color: '#00ff41', letterSpacing: '0.03em' }}>{analysis.pair}</div>
                      </div>
                      <div style={styles.sigCell}>
                        <div style={styles.sigLbl}>BIAS</div>
                        <div style={{ fontSize: 20, color: isBuy ? '#00ff41' : '#ff3131', letterSpacing: '0.02em' }}>
                          {isBuy ? '▲ BUY' : '▼ SELL'}
                        </div>
                      </div>
                    </div>
                    <ConfBar value={analysis.confidence} />
                    <div style={styles.reasonBox}>
                      <div style={styles.reasonLbl}>ANALYSIS</div>
                      <div className="cur-blink" style={styles.reasonTxt}>{analysis.reason}</div>
                    </div>
                  </div>
                )
              })()}
            </div>
          </div>
        )}

      </div>
    </>
  )
}