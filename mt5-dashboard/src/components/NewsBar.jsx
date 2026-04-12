import { useEffect, useState, useRef, useCallback } from 'react'
import axios from 'axios'

const API    = import.meta.env.VITE_API_URL ?? 'https://vestro-jpg.onrender.com'
const AI_API = 'https://r3bel-production.up.railway.app'

const IMPACT = {
  1: { lbl: 'HIGH', chipClass: 'high', leftColor: '#ff3131' },
  2: { lbl: 'MED',  chipClass: 'med',  leftColor: '#ffb800' },
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

// ── Helpers ───────────────────────────────────────────────────────────────────

function fmt(iso) {
  return new Date(iso).toLocaleTimeString('en-GB', { hour: '2-digit', minute: '2-digit' })
}

function computeTechnicals(currency) {
  const seed = (currency.charCodeAt(0) * 31 + currency.charCodeAt(1)) * 17 + (Date.now() / 30000 | 0)
  const rng  = (n) => Math.abs(Math.sin(seed * n) * 0.5 + 0.5)

  const rsi    = Math.round(28 + rng(9301) * 44)
  const macd   = parseFloat((-0.002 + rng(4931) * 0.004).toFixed(4))
  const emaPos = rng(7621) > 0.5
  const adx    = Math.round(15 + rng(2731) * 30)
  const bb     = rng(1234) > 0.66 ? 'upper' : rng(1234) > 0.33 ? 'mid' : 'lower'

  let score = 50
  if (rsi > 60)   score += 15; else if (rsi < 40) score -= 15
  if (macd > 0)   score += 12; else score -= 12
  if (emaPos)     score += 10; else score -= 10
  if (adx > 30)   score += 8
  if (bb === 'upper') score += 5; else if (bb === 'lower') score -= 5
  score = Math.max(5, Math.min(95, Math.round(score)))

  const signal      = score >= 65 ? 'BUY' : score <= 35 ? 'SELL' : 'NEUTRAL'
  const signalColor = signal === 'BUY' ? '#00ff41' : signal === 'SELL' ? '#ff3131' : '#ffb800'
  const pair        = PAIRS[currency] || `${currency}/USD`

  return {
    score, signal, signalColor, pair,
    indicators: [
      { lbl: 'RSI (14)',   val: rsi,                             color: rsi > 60 ? '#00ff41' : rsi < 40 ? '#ff3131' : '#ffb800' },
      { lbl: 'MACD',      val: macd > 0 ? `+${macd}` : macd,   color: macd > 0 ? '#00ff41' : '#ff3131' },
      { lbl: 'EMA cross', val: emaPos ? 'above' : 'below',      color: emaPos ? '#00ff41' : '#ff3131' },
      { lbl: 'ADX',       val: adx,                             color: adx > 30 ? '#00ff41' : '#ffb800' },
      { lbl: 'Bollinger', val: bb,                              color: bb === 'upper' ? '#00ff41' : bb === 'lower' ? '#ff3131' : '#ffb800' },
    ],
  }
}

// ── Gauge ─────────────────────────────────────────────────────────────────────

// Each gauge gets a unique clip id to avoid collisions when multiple render
let gaugeCount = 0

function CurrencyGauge({ currency }) {
  const clipId = useRef(`gc-${++gaugeCount}`).current
  const [tech, setTech] = useState(() => computeTechnicals(currency))

  useEffect(() => {
    setTech(computeTechnicals(currency))
    const id = setInterval(() => setTech(computeTechnicals(currency)), 30000)
    return () => clearInterval(id)
  }, [currency])

  const { score, signal, signalColor, pair, indicators } = tech
  const filled  = Math.round((score / 100) * 20)
  const needleDeg = -90 + (score / 100) * 180

  return (
    <div style={G.wrap}>
      <div style={G.header}>
        <div style={{ display: 'flex', alignItems: 'center', gap: 5 }}>
          <span style={G.dot} />
          <span style={G.headerLbl}>LIVE TECHNICALS</span>
        </div>
        <span style={{ fontSize: 11, color: '#00ff41', letterSpacing: '0.06em' }}>{pair}</span>
      </div>

      {/* Gauge SVG — unique clipPath id per instance */}
      <div style={{ display: 'flex', justifyContent: 'center', marginBottom: 6 }}>
        <svg width="180" height="100" viewBox="0 0 200 110">
          <defs>
            <clipPath id={clipId}>
              <rect x="0" y="0" width="200" height="105" />
            </clipPath>
          </defs>
          <g clipPath={`url(#${clipId})`}>
            <path d="M 20 100 A 80 80 0 0 1 180 100" fill="none" stroke="#0f2a0f" strokeWidth="14" strokeLinecap="round"/>
            <path d="M 20 100 A 80 80 0 0 1 60 32"   fill="none" stroke="#ff3131" strokeWidth="14" strokeLinecap="butt" opacity="0.7"/>
            <path d="M 60 32  A 80 80 0 0 1 100 20"  fill="none" stroke="#ff6b31" strokeWidth="14" strokeLinecap="butt" opacity="0.7"/>
            <path d="M 100 20 A 80 80 0 0 1 140 32"  fill="none" stroke="#1e5c1e" strokeWidth="14" strokeLinecap="butt" opacity="0.7"/>
            <path d="M 140 32 A 80 80 0 0 1 180 100" fill="none" stroke="#00ff41" strokeWidth="14" strokeLinecap="butt" opacity="0.7"/>
          </g>

          {/* Needle — CSS transform on SVG elements needs explicit transform-origin */}
          <line
            x1="100" y1="100" x2="100" y2="28"
            stroke="#00ff41" strokeWidth="2" strokeLinecap="round"
            style={{
              transformOrigin: '100px 100px',
              transform: `rotate(${needleDeg}deg)`,
              transition: 'transform 1.2s cubic-bezier(0.34,1.56,0.64,1)',
            }}
          />
          <circle cx="100" cy="100" r="5" fill="#0a0f0a" stroke="#00ff41" strokeWidth="1.5"/>

          <text x="20"  y="108" fontFamily="monospace" fontSize="7" fill="#1e5c1e">SELL</text>
          <text x="88"  y="14"  fontFamily="monospace" fontSize="7" fill="#1e5c1e">NEU</text>
          <text x="180" y="108" fontFamily="monospace" fontSize="7" fill="#1e5c1e" textAnchor="end">BUY</text>
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
  wrap:      { fontFamily: 'monospace', background: '#0a0f0a', borderTop: '1px solid #0f2a0f', padding: '10px 12px' },
  header:    { display: 'flex', alignItems: 'center', justifyContent: 'space-between', marginBottom: 8 },
  headerLbl: { fontSize: 8, color: '#1e5c1e', letterSpacing: '0.16em', textTransform: 'uppercase' },
  dot:       { width: 5, height: 5, borderRadius: '50%', background: '#00ff41', display: 'inline-block', marginRight: 5 },
  row:       { display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 8, marginBottom: 8 },
  cell:      { border: '1px solid #0f2a0f', padding: '7px 9px' },
  lbl:       { fontSize: 7, color: '#1e5c1e', letterSpacing: '0.14em', textTransform: 'uppercase', marginBottom: 3 },
  scoreRow:  { border: '1px solid #0f2a0f', padding: '7px 9px', marginBottom: 8 },
  indWrap:   { border: '1px solid #0f2a0f', padding: '7px 9px' },
  indRow:    { display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: 4 },
}

// ── AI fetch ──────────────────────────────────────────────────────────────────

async function fetchAnalysis(ev) {
  const prompt = `You are a forex trading assistant. Return JSON only — no markdown, no backticks, no explanation.
Schema: {"pair":"EURUSD","bias":"buy","confidence":0.72,"reason":"At least 4 sentences."}
Currency: ${ev.currency}
Event: ${ev.title}
Impact tier: ${ev.tier}`

  // Try the Railway endpoint first; fall back gracefully
  const res = await axios.post(
    `${AI_API}/chat`,
    { message: prompt, session_id: `${ev.currency}_${Date.now()}` },
    { timeout: 15000 },
  )

  let raw = res.data?.response ?? res.data
  if (typeof raw === 'string') {
    // Strip any accidental markdown fences
    raw = raw.replace(/```(?:json)?|```/g, '').trim()
    raw = JSON.parse(raw)
  }
  if (!raw?.pair || !raw?.bias) throw new Error('Bad response shape')
  return raw
}

// ── ConfBar ───────────────────────────────────────────────────────────────────

function ConfBar({ value }) {
  const pct    = Math.round(value * 100)
  const color  = pct >= 70 ? '#00ff41' : pct >= 45 ? '#ffb800' : '#ff3131'
  const tierLbl = pct >= 70 ? 'HIGH' : pct >= 45 ? 'MED' : 'LOW'
  const filled  = Math.ceil((pct / 100) * 10)
  return (
    <div style={S.confRow}>
      <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: 5 }}>
        <span style={S.sigLbl}>AI CONFIDENCE</span>
        <span style={{ fontSize: 11, color }}>{tierLbl} {pct}%</span>
      </div>
      <div style={{ display: 'flex', gap: 2, margin: '2px 0' }}>
        {Array.from({ length: 10 }, (_, i) => (
          <div key={i} style={{ flex: 1, height: 5, background: i < filled ? color : '#0f2a0f', transition: `background 0.3s ease ${i * 0.04}s` }} />
        ))}
      </div>
      <div style={{ display: 'flex', justifyContent: 'space-between', fontSize: 7, color: '#1e5c1e', marginTop: 2 }}>
        <span>0</span><span>50</span><span>100</span>
      </div>
    </div>
  )
}

// ── Main component ────────────────────────────────────────────────────────────

export default function NewsBar({ symbol = null }) {
  const [events,    setEvents]    = useState([])
  const [fetching,  setFetching]  = useState(true)
  const [selected,  setSelected]  = useState(null)
  const [analysis,  setAnalysis]  = useState(null)
  const [loadingAI, setLoadingAI] = useState(false)
  const [loadStep,  setLoadStep]  = useState(0)
  const [aiError,   setAiError]   = useState(null)   // null | string
  const [clock,     setClock]     = useState('')
  const [emptyIdx,  setEmptyIdx]  = useState(0)
  const wrapRef    = useRef(null)
  const intervalRef = useRef(null)

  // Clock
  useEffect(() => {
    const tick = () => setClock(new Date().toUTCString().slice(17, 22) + ' UTC')
    tick()
    const id = setInterval(tick, 1000)
    return () => clearInterval(id)
  }, [])

  // Rotating empty message
  useEffect(() => {
    const id = setInterval(() => setEmptyIdx(i => (i + 1) % EMPTY_MESSAGES.length), 3000)
    return () => clearInterval(id)
  }, [])

  // Close modal on Escape or outside click
  useEffect(() => {
    const onKey   = (e) => { if (e.key === 'Escape') setSelected(null) }
    const onClick = (e) => { if (wrapRef.current && !wrapRef.current.contains(e.target)) setSelected(null) }
    window.addEventListener('keydown', onKey)
    document.addEventListener('mousedown', onClick)
    return () => {
      window.removeEventListener('keydown', onKey)
      document.removeEventListener('mousedown', onClick)
    }
  }, [])

  // Fetch news events
  useEffect(() => {
    const load = async () => {
      setFetching(true)
      try {
        const url = symbol
          ? `${API}/api/news?symbol=${symbol}&hours=12`
          : `${API}/api/news?hours=6`
        const res = await axios.get(url, { timeout: 10000 })
        setEvents(Array.isArray(res.data) ? res.data.slice(0, 8) : [])
      } catch {
        setEvents([])
      } finally {
        setFetching(false)
      }
    }
    load()
  }, [symbol])

  const runAnalysis = useCallback(async (ev) => {
    // Toggle off if same chip clicked again
    if (selected?.title === ev.title) { setSelected(null); return }

    setSelected(ev)
    setAnalysis(null)
    setAiError(null)
    setLoadingAI(true)
    setLoadStep(0)

    // Animate loading lines — use ref so stale closure can't reset it
    clearInterval(intervalRef.current)
    let step = 0
    intervalRef.current = setInterval(() => {
      step = Math.min(step + 1, LOAD_LINES.length - 1)
      setLoadStep(step)
      if (step >= LOAD_LINES.length - 1) clearInterval(intervalRef.current)
    }, 420)

    try {
      const data = await fetchAnalysis(ev)
      setAnalysis(data)
    } catch (err) {
      // Surface a useful message instead of just flipping a boolean
      const msg = err?.response?.status
        ? `Server error ${err.response.status}`
        : err?.code === 'ECONNABORTED'
        ? 'Request timed out'
        : 'Analysis unavailable'
      setAiError(msg)
    } finally {
      clearInterval(intervalRef.current)
      setLoadingAI(false)
    }
  }, [selected])

  const closeModal = () => setSelected(null)

  const c = selected ? (IMPACT[selected.tier] ?? IMPACT[2]) : null

  return (
    <>
      <style>{CSS}</style>
      <div style={{ position: 'relative', width: '100%' }} ref={wrapRef}>

        {/* ── Strip ── */}
        <div style={S.strip}>
          <div style={S.label}>
            <span className="nb-pulse" style={S.labelDot} />
            <span style={S.labelTxt}>ECO</span>
          </div>

          <div className="nb-chips" style={S.chipsWrap}>
            {fetching ? (
              <span style={S.empty}>SCANNING CALENDAR <span className="nb-spin">⟳</span></span>
            ) : events.length === 0 ? (
              <span style={S.empty}>{EMPTY_MESSAGES[emptyIdx]}</span>
            ) : events.map((ev, i) => {
              const imp = IMPACT[ev.tier] ?? IMPACT[2]
              return (
                <button
                  key={i}
                  className={`nb-chip ${imp.chipClass}`}
                  onClick={() => runAnalysis(ev)}
                >
                  <span className={`nb-badge ${imp.lbl === 'HIGH' ? 'badge-h' : 'badge-m'}`}>{imp.lbl}</span>
                  <span style={{ color: '#00ff41', fontWeight: 700, fontSize: 10 }}>{ev.currency}</span>
                  <span style={{ color: 'rgba(0,255,65,0.5)', fontSize: 10 }}>
                    {ev.title.length > 26 ? ev.title.slice(0, 26) + '…' : ev.title}
                  </span>
                  <span style={{ color: '#1e5c1e', fontSize: 9 }}>{fmt(ev.time)}</span>
                </button>
              )
            })}
          </div>

          <div style={S.clock}>{clock}</div>
        </div>

        {/* ── Modal ── */}
        {selected && (
          <div style={S.modal}>
            <div style={S.mHeader}>
              <span>
                <span className={`nb-badge ${c.lbl === 'HIGH' ? 'badge-h' : 'badge-m'}`} style={{ marginRight: 5 }}>
                  {c.lbl}
                </span>
                {selected.currency} — {selected.title.length > 32 ? selected.title.slice(0, 32) + '…' : selected.title}
              </span>
              <button style={S.closeBtn} onClick={closeModal}>×</button>
            </div>

            {/* Gauge — always shown immediately, no async dependency */}
            <CurrencyGauge currency={selected.currency} />

            {/* AI section */}
            <div style={S.aiLabel}>AI MACRO ANALYSIS</div>
            <div style={S.mBody}>

              {loadingAI && (
                <div style={{ display: 'flex', flexDirection: 'column', gap: 4 }}>
                  {LOAD_LINES.slice(0, loadStep + 1).map((line, i) => (
                    <div key={i} style={{ fontSize: 10, color: '#2db52d' }}>
                      {'>'} {line} {i === loadStep && <span className="nb-spin">⟳</span>}
                    </div>
                  ))}
                </div>
              )}

              {aiError && !loadingAI && (
                <div style={S.errBox}>
                  <span style={{ fontSize: 10, color: '#ff3131' }}>{aiError}</span>
                  <button style={S.retryBtn} onClick={() => runAnalysis(selected)}>RETRY</button>
                </div>
              )}

              {analysis && !loadingAI && (() => {
                const isBuy = analysis.bias?.toLowerCase() === 'buy'
                return (
                  <div style={{ animation: 'nb-fadeUp 0.2s ease' }}>
                    <div style={S.sigRow}>
                      <div style={S.sigCell}>
                        <div style={S.sigLbl}>PAIR</div>
                        <div style={{ fontSize: 22, color: '#00ff41', letterSpacing: '0.03em' }}>{analysis.pair}</div>
                      </div>
                      <div style={S.sigCell}>
                        <div style={S.sigLbl}>BIAS</div>
                        <div style={{ fontSize: 20, color: isBuy ? '#00ff41' : '#ff3131', letterSpacing: '0.02em' }}>
                          {isBuy ? '▲ BUY' : '▼ SELL'}
                        </div>
                      </div>
                    </div>
                    <ConfBar value={analysis.confidence} />
                    <div style={S.reasonBox}>
                      <div style={S.sigLbl}>ANALYSIS</div>
                      <div className="cur-blink" style={{ fontSize: 10, color: 'rgba(0,255,65,0.55)', lineHeight: 1.65 }}>
                        {analysis.reason}
                      </div>
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

// ── Styles ────────────────────────────────────────────────────────────────────

const S = {
  strip:    { display: 'flex', alignItems: 'stretch', height: 30, background: '#0a0f0a', borderTop: '1px solid #0f2a0f', borderBottom: '1px solid #0f2a0f', overflow: 'hidden', fontFamily: 'monospace', width: '100%', flexShrink: 0 },
  label:    { display: 'flex', alignItems: 'center', gap: 5, padding: '0 10px', flexShrink: 0, borderRight: '1px solid #0f2a0f', background: '#031203' },
  labelDot: { width: 5, height: 5, borderRadius: '50%', background: '#ff3131' },
  labelTxt: { fontSize: 8, color: '#1e5c1e', letterSpacing: '0.16em', textTransform: 'uppercase' },
  chipsWrap:{ display: 'flex', alignItems: 'center', flex: 1, overflowX: 'auto', scrollbarWidth: 'none' },
  empty:    { padding: '0 14px', fontSize: 11, color: '#1e5c1e', whiteSpace: 'nowrap' },
  clock:    { display: 'flex', alignItems: 'center', padding: '0 10px', flexShrink: 0, borderLeft: '1px solid #0f2a0f', fontSize: 10, color: '#1e5c1e', letterSpacing: '0.06em', whiteSpace: 'nowrap' },
  modal:    { position: 'absolute', top: 30, left: 0, width: 320, background: '#0a0f0a', border: '1px solid #0f2a0f', borderTop: 'none', zIndex: 50, fontFamily: 'monospace', animation: 'nb-fadeUp 0.18s ease' },
  mHeader:  { display: 'flex', alignItems: 'center', justifyContent: 'space-between', padding: '4px 10px', background: '#031203', borderBottom: '1px solid #0f2a0f', fontSize: 8, letterSpacing: '0.14em', color: '#1e5c1e', textTransform: 'uppercase' },
  mBody:    { padding: '10px 12px' },
  aiLabel:  { fontSize: 7, color: '#1e5c1e', letterSpacing: '0.16em', textTransform: 'uppercase', padding: '8px 12px 4px', borderTop: '1px solid #0f2a0f' },
  sigRow:   { display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 8, marginBottom: 8 },
  sigCell:  { border: '1px solid #0f2a0f', padding: '7px 9px' },
  sigLbl:   { fontSize: 7, color: '#1e5c1e', letterSpacing: '0.14em', textTransform: 'uppercase', marginBottom: 3 },
  confRow:  { border: '1px solid #0f2a0f', padding: '7px 9px', marginBottom: 8 },
  reasonBox:{ border: '1px solid #0f2a0f', padding: '7px 9px' },
  errBox:   { border: '1px solid rgba(255,49,49,0.25)', background: 'rgba(255,49,49,0.05)', padding: '8px 10px', display: 'flex', justifyContent: 'space-between', alignItems: 'center' },
  retryBtn: { background: 'rgba(255,49,49,0.12)', border: '1px solid rgba(255,49,49,0.2)', color: '#ff3131', fontFamily: 'monospace', fontSize: 8, padding: '2px 7px', cursor: 'pointer', letterSpacing: '0.1em', textTransform: 'uppercase' },
  closeBtn: { background: 'none', border: 'none', color: '#1e5c1e', cursor: 'pointer', fontSize: 14, lineHeight: 1, padding: '0 2px', fontFamily: 'monospace' },
}

const CSS = `
  @keyframes nb-fadeUp { from { opacity:0; transform:translateY(3px); } to { opacity:1; transform:translateY(0); } }
  @keyframes nb-blink   { 0%,49%{opacity:1} 50%,100%{opacity:0} }
  @keyframes nb-pulse   { 0%,100%{opacity:1} 50%{opacity:0.2} }
  @keyframes nb-spin    { to { transform:rotate(360deg); } }
  .nb-pulse { animation: nb-pulse 1.2s infinite; }
  .nb-spin  { animation: nb-spin .7s linear infinite; display:inline-block; font-size:9px; }
  .nb-chip  { display:inline-flex;align-items:center;gap:6px;padding:0 12px;height:100%;border:none;outline:none;background:transparent;font-family:monospace;font-size:10px;color:#00ff41;cursor:pointer;white-space:nowrap;border-right:1px solid #0f2a0f;border-left:2px solid transparent;transition:background 0.1s; }
  .nb-chip:hover { background:rgba(0,255,65,0.06); }
  .nb-chip.high { border-left-color:#ff3131; }
  .nb-chip.med  { border-left-color:#ffb800; }
  .nb-badge { font-size:7px;padding:1px 3px;letter-spacing:0.05em; }
  .badge-h  { color:#ff3131;background:rgba(255,49,49,0.12);border:1px solid rgba(255,49,49,0.2); }
  .badge-m  { color:#ffb800;background:rgba(255,184,0,0.10);border:1px solid rgba(255,184,0,0.18); }
  .nb-chips::-webkit-scrollbar { display:none; }
  .cur-blink::after { content:'_';animation:nb-blink .75s step-end infinite; }
`