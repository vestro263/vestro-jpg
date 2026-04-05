import { useEffect, useState, useRef } from 'react'
import axios from 'axios'

const API = import.meta.env.VITE_API_URL ?? 'https://vestro-jpg.onrender.com'
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

function fmt(iso) {
  return new Date(iso).toLocaleTimeString('en-GB', { hour: '2-digit', minute: '2-digit' })
}

const styles = {
  strip: {
    display: 'flex',
    alignItems: 'stretch',
    height: 30,
    background: '#0a0f0a',
    borderTop: '1px solid #0f2a0f',
    borderBottom: '1px solid #0f2a0f',
    overflow: 'hidden',
    fontFamily: "'Share Tech Mono', monospace",
    width: '100%',
    flexShrink: 0,
  },
  label: {
    display: 'flex', alignItems: 'center', gap: 5,
    padding: '0 10px', flexShrink: 0,
    borderRight: '1px solid #0f2a0f',
    background: '#031203',
  },
  labelDot: {
    width: 5, height: 5, borderRadius: '50%',
    background: '#ff3131',
  },
  labelTxt: {
    fontSize: 8, color: '#1e5c1e',
    letterSpacing: '0.16em', textTransform: 'uppercase',
  },
  chipsWrap: {
    display: 'flex', alignItems: 'center', flex: 1,
    overflowX: 'auto', scrollbarWidth: 'none',
  },
  empty: {
    padding: '0 14px', fontSize: 11,
    color: '#1e5c1e', whiteSpace: 'nowrap',
  },
  clock: {
    display: 'flex', alignItems: 'center',
    padding: '0 10px', flexShrink: 0,
    borderLeft: '1px solid #0f2a0f',
    fontSize: 10, color: '#1e5c1e', letterSpacing: '0.06em',
    whiteSpace: 'nowrap',
  },
  // modal
  modalWrap: {
    position: 'absolute',
    top: 30,
    left: 0,
    width: 320,
    background: '#0a0f0a',
    border: '1px solid #0f2a0f',
    borderTop: 'none',
    zIndex: 50,
    fontFamily: "'Share Tech Mono', monospace",
    animation: 'fadeUp 0.18s ease',
  },
  mHeader: {
    display: 'flex', alignItems: 'center', justifyContent: 'space-between',
    padding: '4px 10px',
    background: '#031203',
    borderBottom: '1px solid #0f2a0f',
    fontSize: 8, letterSpacing: '0.14em',
    color: '#1e5c1e', textTransform: 'uppercase',
  },
  mBody: { padding: '10px 12px' },
  sigRow: { display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 8, marginBottom: 8 },
  sigCell: { border: '1px solid #0f2a0f', padding: '7px 9px' },
  sigLbl: { fontSize: 7, color: '#1e5c1e', letterSpacing: '0.14em', textTransform: 'uppercase', marginBottom: 3 },
  confRow: { border: '1px solid #0f2a0f', padding: '7px 9px', marginBottom: 8 },
  reasonBox: { border: '1px solid #0f2a0f', padding: '7px 9px' },
  reasonLbl: { fontSize: 7, color: '#1e5c1e', letterSpacing: '0.14em', textTransform: 'uppercase', marginBottom: 5 },
  reasonTxt: { fontSize: 10, color: 'rgba(0,255,65,0.55)', lineHeight: 1.65 },
  segsWrap: { display: 'flex', gap: 2, margin: '2px 0' },
  clbls: { display: 'flex', justifyContent: 'space-between', fontSize: 7, color: '#1e5c1e', marginTop: 2 },
  loadLines: { display: 'flex', flexDirection: 'column', gap: 4 },
  loadLine: { fontSize: 10, color: '#2db52d' },
  errBox: {
    border: '1px solid rgba(255,49,49,0.25)',
    background: 'rgba(255,49,49,0.05)',
    padding: '8px 10px',
    display: 'flex', justifyContent: 'space-between', alignItems: 'center',
  },
  errTxt: { fontSize: 10, color: '#ff3131' },
  retryBtn: {
    background: 'rgba(255,49,49,0.12)',
    border: '1px solid rgba(255,49,49,0.2)',
    color: '#ff3131', fontFamily: "'Share Tech Mono', monospace",
    fontSize: 8, padding: '2px 7px', cursor: 'pointer',
    letterSpacing: '0.1em', textTransform: 'uppercase',
  },
  closeBtn: {
    background: 'none', border: 'none',
    color: '#1e5c1e', cursor: 'pointer',
    fontSize: 14, lineHeight: 1, padding: '0 2px',
    fontFamily: "'Share Tech Mono', monospace",
  },
}

const CSS = `
  @import url('https://fonts.googleapis.com/css2?family=Share+Tech+Mono&display=swap');
  @keyframes fadeUp { from { opacity:0; transform:translateY(3px); } to { opacity:1; transform:translateY(0); } }
  @keyframes blink { 0%,49%{opacity:1} 50%,100%{opacity:0} }
  @keyframes pulse { 0%,100%{opacity:1} 50%{opacity:0.2} }
  @keyframes spin { to { transform:rotate(360deg); } }
  .newsbar-chip {
    display: inline-flex; align-items: center; gap: 6px;
    padding: 0 12px; height: 100%; border: none; outline: none;
    background: transparent; font-family: 'Share Tech Mono', monospace;
    font-size: 10px; color: #00ff41; cursor: pointer; white-space: nowrap;
    border-right: 1px solid #0f2a0f; border-left: 2px solid transparent;
    transition: background 0.1s;
  }
  .newsbar-chip:hover { background: rgba(0,255,65,0.06); }
  .newsbar-chip.high { border-left-color: #ff3131; }
  .newsbar-chip.med  { border-left-color: #ffb800; }
  .newsbar-badge { font-size: 7px; padding: 1px 3px; letter-spacing: 0.05em; }
  .badge-h { color:#ff3131; background:rgba(255,49,49,0.12); border:1px solid rgba(255,49,49,0.2); }
  .badge-m { color:#ffb800; background:rgba(255,184,0,0.10); border:1px solid rgba(255,184,0,0.18); }
  .newsbar-dot { animation: pulse 1.2s infinite; }
  .cur-blink::after { content:'_'; animation: blink .75s step-end infinite; }
  .spin { animation: spin .7s linear infinite; display:inline-block; font-size:9px; }
  .newsbar-chips-wrap::-webkit-scrollbar { display: none; }
`

const EMPTY_MESSAGES = [
  'Market in low-volatility phase',
  'No catalysts detected',
  'Waiting for macro drivers',
  'Calm before volatility',
]

export default function NewsBar({ symbol = null }) {
  const [events, setEvents]     = useState([])
  const [loading, setLoading]   = useState(true)
  const [selected, setSelected] = useState(null)
  const [analysis, setAnalysis] = useState(null)
  const [loadingAI, setLoadingAI] = useState(false)
  const [loadStep, setLoadStep] = useState(0)
  const [error, setError]       = useState(null)
  const [clock, setClock]       = useState('')
  const [emptyIdx, setEmptyIdx] = useState(0)
  const wrapRef = useRef(null)

  // Clock
  useEffect(() => {
    const tick = () => {
      const n = new Date()
      setClock(n.toUTCString().slice(17, 22) + ' UTC')
    }
    tick()
    const id = setInterval(tick, 1000)
    return () => clearInterval(id)
  }, [])

  // Rotate empty state messages
  useEffect(() => {
    const id = setInterval(() => setEmptyIdx(i => (i + 1) % EMPTY_MESSAGES.length), 3000)
    return () => clearInterval(id)
  }, [])

  // ESC to close
  useEffect(() => {
    const onKey = (e) => { if (e.key === 'Escape') setSelected(null) }
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  }, [])

  // Click outside to close
  useEffect(() => {
    const onClick = (e) => {
      if (wrapRef.current && !wrapRef.current.contains(e.target)) setSelected(null)
    }
    document.addEventListener('mousedown', onClick)
    return () => document.removeEventListener('mousedown', onClick)
  }, [])

  // Fetch events
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

    // Animate load lines
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

  // Confidence bar
  const ConfBar = ({ value }) => {
    const pct = Math.round(value * 100)
    const tier = pct >= 70 ? 'g' : pct >= 45 ? 'a' : 'r'
    const tierLbl = pct >= 70 ? 'HIGH' : pct >= 45 ? 'MED' : 'LOW'
    const color = tier === 'g' ? '#00ff41' : tier === 'a' ? '#ffb800' : '#ff3131'
    const filled = Math.ceil((pct / 100) * 10)
    return (
      <div style={styles.confRow}>
        <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: 5 }}>
          <span style={{ fontSize: 7, color: '#1e5c1e', letterSpacing: '0.14em', textTransform: 'uppercase' }}>CONFIDENCE</span>
          <span style={{ fontSize: 11, color }}>{tierLbl} {pct}%</span>
        </div>
        <div style={styles.segsWrap}>
          {[...Array(10)].map((_, i) => (
            <div key={i} style={{
              flex: 1, height: 5,
              background: i < filled ? color : '#0f2a0f',
              transition: `background 0.3s ease ${i * 0.04}s`,
            }} />
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

        {/* ── STRIP ── */}
        <div style={styles.strip}>
          {/* Label */}
          <div style={styles.label}>
            <span className="newsbar-dot" style={styles.labelDot} />
            <span style={styles.labelTxt}>ECO</span>
          </div>

          {/* Chips */}
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
                    <span className={`newsbar-badge ${imp.lbl === 'HIGH' ? 'badge-h' : 'badge-m'}`}>
                      {imp.lbl}
                    </span>
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

          {/* Clock */}
          <div style={styles.clock}>{clock}</div>
        </div>

        {/* ── DROPDOWN MODAL ── */}
        {selected && (
          <div style={styles.modalWrap}>
            {/* Header */}
            <div style={styles.mHeader}>
              <span>
                <span className={`newsbar-badge ${c.lbl === 'HIGH' ? 'badge-h' : 'badge-m'}`} style={{ marginRight: 5 }}>
                  {c.lbl}
                </span>
                {selected.currency} — {selected.title.length > 32 ? selected.title.slice(0, 32) + '…' : selected.title}
              </span>
              <button style={styles.closeBtn} onClick={() => setSelected(null)}>×</button>
            </div>

            <div style={styles.mBody}>
              {/* Loading */}
              {loadingAI && (
                <div style={styles.loadLines}>
                  {LOAD_LINES.slice(0, loadStep + 1).map((line, i) => (
                    <div key={i} style={styles.loadLine}>
                      {'>'} {line} {i === loadStep ? <span className="spin">⟳</span> : null}
                    </div>
                  ))}
                </div>
              )}

              {/* Error */}
              {error && !loadingAI && (
                <div style={styles.errBox}>
                  <span style={styles.errTxt}>Analysis failed. Try again.</span>
                  <button style={styles.retryBtn} onClick={() => handleChip(selected)}>RETRY</button>
                </div>
              )}

              {/* Result */}
              {analysis && !loadingAI && (() => {
                const pct = Math.round(analysis.confidence * 100)
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