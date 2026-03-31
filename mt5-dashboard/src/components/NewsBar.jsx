import { useEffect, useState, useRef } from 'react'
import axios from 'axios'

const API = import.meta.env.VITE_API_URL ?? 'https://vestro-jpg.onrender.com'
const AI_API = 'https://r3bel-production.up.railway.app'

const IMPACT = {
  1: { bg: 'rgba(239,68,68,0.08)', border: 'rgba(239,68,68,0.25)', text: '#f87171', dot: '#ef4444', label: 'HIGH' },
  2: { bg: 'rgba(245,158,11,0.08)', border: 'rgba(245,158,11,0.25)', text: '#fbbf24', dot: '#f59e0b', label: 'MED' },
}

const BIAS_COLORS = {
  buy:  { bg: 'rgba(34,197,94,0.12)',  border: 'rgba(34,197,94,0.3)',  text: '#4ade80' },
  sell: { bg: 'rgba(239,68,68,0.12)', border: 'rgba(239,68,68,0.3)', text: '#f87171' },
}

const ConfidenceBar = ({ value }) => {
  const pct = Math.round(value * 100)

  const tier =
    pct >= 70 ? {
      label: 'HIGH',
      color: '#4ade80',
      glow: '#4ade8066',
      trackBg: 'rgba(74,222,128,0.08)',
      labelBg: 'rgba(74,222,128,0.12)',
      labelBorder: 'rgba(74,222,128,0.25)',
      segments: 3,
    } : pct >= 45 ? {
      label: 'MEDIUM',
      color: '#fbbf24',
      glow: '#fbbf2466',
      trackBg: 'rgba(251,191,36,0.08)',
      labelBg: 'rgba(251,191,36,0.12)',
      labelBorder: 'rgba(251,191,36,0.25)',
      segments: 2,
    } : {
      label: 'LOW',
      color: '#f87171',
      glow: '#f8717166',
      trackBg: 'rgba(248,113,113,0.08)',
      labelBg: 'rgba(248,113,113,0.12)',
      labelBorder: 'rgba(248,113,113,0.25)',
      segments: 1,
    }

  return (
    <div style={{
      marginTop: 14,
      background: tier.trackBg,
      border: `1px solid ${tier.labelBorder}`,
      borderRadius: 8,
      padding: '10px 14px',
    }}>
      {/* Header row */}
      <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: 10 }}>
        <div style={{ display: 'flex', alignItems: 'center', gap: 6 }}>
          <span style={{
            fontSize: 10, color: '#4b5563',
            textTransform: 'uppercase', letterSpacing: '0.08em'
          }}>
            Confidence
          </span>
          <span style={{
            fontSize: 9, fontWeight: 700,
            color: tier.color,
            background: tier.labelBg,
            border: `1px solid ${tier.labelBorder}`,
            padding: '1px 6px', borderRadius: 4,
            letterSpacing: '0.07em',
          }}>
            {tier.label}
          </span>
        </div>
        <span style={{ fontSize: 15, fontWeight: 800, color: tier.color }}>
          {pct}%
        </span>
      </div>

      {/* Segmented bar — 5 blocks */}
      <div style={{ display: 'flex', gap: 4 }}>
        {[1, 2, 3, 4, 5].map(i => {
          const filled = i <= Math.ceil((pct / 100) * 5)

          return (
            <div key={i} style={{
              flex: 1, height: 6, borderRadius: 3,
              background: filled ? tier.color : 'rgba(255,255,255,0.06)',
              boxShadow: filled ? `0 0 6px ${tier.glow}` : 'none',
              transition: `background 0.4s ease ${i * 0.06}s, box-shadow 0.4s ease ${i * 0.06}s`,
            }} />
          )
        })}
      </div>

      {/* Segment labels */}
      <div style={{ display: 'flex', justifyContent: 'space-between', marginTop: 5 }}>
        {['0', '20', '40', '60', '80', '100'].map(l => (
          <span key={l} style={{ fontSize: 8.5, color: '#374151' }}>{l}</span>
        ))}
      </div>
    </div>
  )
}


const Spinner = () => (
  <svg width="18" height="18" viewBox="0 0 24 24" style={{ animation: 'spin 0.8s linear infinite' }}>
    <circle cx="12" cy="12" r="10" fill="none" stroke="rgba(255,255,255,0.12)" strokeWidth="3" />
    <path d="M12 2a10 10 0 0 1 10 10" fill="none" stroke="#60a5fa" strokeWidth="3" strokeLinecap="round" />
  </svg>
)

export default function NewsBar({ symbol = null }) {
  const [events, setEvents]     = useState([])
  const [loading, setLoading]   = useState(true)
  const [selected, setSelected] = useState(null)
  const [analysis, setAnalysis] = useState(null)
  const [loadingAI, setLoadingAI] = useState(false)
  const [error, setError]       = useState(null)
  const modalRef = useRef(null)

  // Close modal on Escape
  useEffect(() => {
    const onKey = (e) => { if (e.key === 'Escape') setSelected(null) }
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  }, [])

  useEffect(() => {
    const fetchNews = async () => {
      setLoading(true)
      try {
        const url = symbol
          ? `${API}/api/news?symbol=${symbol}&hours=12`
          : `${API}/api/news?hours=6`
        const res = await axios.get(url)
        setEvents(Array.isArray(res.data) ? res.data.slice(0, 6) : [])
      } catch {
        setEvents([])
      } finally {
        setLoading(false)
      }
    }
    fetchNews()
  }, [symbol])

  const handleClick = async (ev) => {
    setSelected(ev)
    setAnalysis(null)
    setError(null)
    setLoadingAI(true)

    try {
      const message = `
You are a forex trading assistant.

Return JSON only — no markdown, no explanation:
{
  "pair": "EURUSD",
  "bias": "buy or sell",
  "confidence": 0.0-1.0,
  "reason": "short explanation"
}

News:
Currency: ${ev.currency}
Event: ${ev.title}
Impact: ${ev.tier}
`
      const res = await axios.post(`${AI_API}/chat`, {
        message,
        session_id: `${ev.currency}_${Date.now()}`
      })

      let data = res.data?.response || res.data
      if (typeof data === 'string') {
        try { data = JSON.parse(data.replace(/```json|```/g, '').trim()) } catch {}
      }

      if (!data?.pair) throw new Error('Unexpected response format')
      setAnalysis(data)
    } catch (err) {
      setError('Analysis failed. Please try again.')
    } finally {
      setLoadingAI(false)
    }
  }

  if (loading || events.length === 0) return null

  const biasMeta = analysis ? (BIAS_COLORS[analysis.bias?.toLowerCase()] ?? BIAS_COLORS.buy) : null

  return (
    <>
      <style>{`
        @keyframes spin { to { transform: rotate(360deg); } }
        @keyframes fadeUp {
          from { opacity: 0; transform: translateY(6px) scale(0.98); }
          to   { opacity: 1; transform: translateY(0) scale(1); }
        }
        @keyframes shimmer {
          0%   { background-position: -200% center; }
          100% { background-position:  200% center; }
        }
        .news-chip:hover {
          filter: brightness(1.2);
          transform: translateY(-1px);
        }
        .news-chip { transition: transform 0.15s ease, filter 0.15s ease; }
        .close-btn:hover { background: rgba(255,255,255,0.1) !important; }
        .retry-btn:hover { opacity: 0.8; }
      `}</style>

      {/* ── NEWS BAR ── */}
      <div style={{
        background: '#0d1117',
        borderBottom: '1px solid rgba(255,255,255,0.06)',
        padding: '7px 20px',
        display: 'flex',
        alignItems: 'center',
        gap: 10,
        overflowX: 'auto',
        scrollbarWidth: 'none',
      }}>
        {/* Label */}
        <div style={{
          display: 'flex',
          alignItems: 'center',
          gap: 5,
          flexShrink: 0,
          paddingRight: 8,
          borderRight: '1px solid rgba(255,255,255,0.07)',
        }}>
          <span style={{
            width: 5, height: 5, borderRadius: '50%',
            background: '#ef4444',
            boxShadow: '0 0 6px #ef4444',
            animation: 'spin 2s linear infinite',
            flexShrink: 0,
          }} />
          <span style={{
            fontSize: 9.5,
            color: '#4b5563',
            textTransform: 'uppercase',
            letterSpacing: '0.1em',
            fontWeight: 600,
          }}>
            Events
          </span>
        </div>

        {events.map((ev, i) => {
          const c = IMPACT[ev.tier] || IMPACT[2]
          const timeStr = new Date(ev.time).toLocaleTimeString('en-GB', { hour: '2-digit', minute: '2-digit' })

          return (
            <button
              key={i}
              className="news-chip"
              onClick={() => handleClick(ev)}
              style={{
                cursor: 'pointer',
                display: 'flex',
                alignItems: 'center',
                gap: 7,
                background: c.bg,
                border: `1px solid ${c.border}`,
                borderRadius: 6,
                padding: '4px 10px',
                whiteSpace: 'nowrap',
                outline: 'none',
                fontFamily: 'inherit',
              }}
            >
              <span style={{
                fontSize: 8.5,
                fontWeight: 700,
                color: c.dot,
                letterSpacing: '0.06em',
                background: `${c.dot}22`,
                padding: '1px 4px',
                borderRadius: 3,
              }}>
                {c.label}
              </span>
              <span style={{ fontSize: 11, fontWeight: 700, color: c.text }}>
                {ev.currency}
              </span>
              <span style={{ fontSize: 11, color: 'rgba(255,255,255,0.55)' }}>
                {ev.title.length > 28 ? ev.title.slice(0, 28) + '…' : ev.title}
              </span>
              <span style={{ fontSize: 10, color: '#4b5563', marginLeft: 2 }}>
                {timeStr}
              </span>
            </button>
          )
        })}
      </div>

      {/* ── AI MODAL ── */}
      {selected && (
        <div
          onClick={() => setSelected(null)}
          style={{
            position: 'fixed', inset: 0,
            background: 'rgba(0,0,0,0.65)',
            backdropFilter: 'blur(4px)',
            display: 'flex',
            alignItems: 'center',
            justifyContent: 'center',
            zIndex: 999,
          }}
        >
          <div
            ref={modalRef}
            onClick={(e) => e.stopPropagation()}
            style={{
              background: '#0d1117',
              border: '1px solid rgba(255,255,255,0.08)',
              borderRadius: 12,
              padding: '20px 22px',
              width: 380,
              boxShadow: '0 24px 60px rgba(0,0,0,0.6)',
              animation: 'fadeUp 0.2s ease',
            }}
          >
            {/* Header */}
            <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'flex-start', marginBottom: 16 }}>
              <div>
                <div style={{ fontSize: 13, fontWeight: 700, color: '#f1f5f9', lineHeight: 1.3 }}>
                  {selected.title}
                </div>
                <div style={{ display: 'flex', alignItems: 'center', gap: 6, marginTop: 5 }}>
                  {(() => {
                    const c = IMPACT[selected.tier] || IMPACT[2]
                    return (
                      <>
                        <span style={{
                          fontSize: 9, fontWeight: 700, color: c.dot,
                          background: `${c.dot}22`, padding: '1px 5px', borderRadius: 3,
                          letterSpacing: '0.07em',
                        }}>{c.label} IMPACT</span>
                        <span style={{ fontSize: 11, color: '#6b7280' }}>{selected.currency}</span>
                      </>
                    )
                  })()}
                </div>
              </div>
              <button
                className="close-btn"
                onClick={() => setSelected(null)}
                style={{
                  background: 'transparent',
                  border: 'none',
                  cursor: 'pointer',
                  color: '#6b7280',
                  fontSize: 18,
                  lineHeight: 1,
                  padding: '2px 6px',
                  borderRadius: 6,
                  transition: 'background 0.15s',
                }}
              >×</button>
            </div>

            <div style={{ height: '1px', background: 'rgba(255,255,255,0.06)', marginBottom: 16 }} />

            {/* Loading state */}
            {loadingAI && (
              <div style={{ display: 'flex', alignItems: 'center', gap: 10, color: '#6b7280', padding: '8px 0' }}>
                <Spinner />
                <span style={{ fontSize: 12 }}>Analyzing market impact…</span>
              </div>
            )}

            {/* Error state */}
            {error && !loadingAI && (
              <div style={{
                background: 'rgba(239,68,68,0.08)',
                border: '1px solid rgba(239,68,68,0.2)',
                borderRadius: 8, padding: '10px 14px',
                display: 'flex', justifyContent: 'space-between', alignItems: 'center',
              }}>
                <span style={{ fontSize: 12, color: '#f87171' }}>{error}</span>
                <button
                  className="retry-btn"
                  onClick={() => handleClick(selected)}
                  style={{
                    background: 'rgba(239,68,68,0.15)',
                    border: '1px solid rgba(239,68,68,0.25)',
                    borderRadius: 5, padding: '3px 10px',
                    color: '#f87171', fontSize: 11, cursor: 'pointer',
                    transition: 'opacity 0.15s',
                  }}
                >Retry</button>
              </div>
            )}

            {/* Analysis result */}
            {analysis && !loadingAI && (
              <div style={{ animation: 'fadeUp 0.25s ease' }}>
                {/* Pair + Bias */}
                <div style={{ display: 'flex', gap: 10, marginBottom: 14 }}>
                  <div style={{
                    flex: 1,
                    background: 'rgba(255,255,255,0.04)',
                    border: '1px solid rgba(255,255,255,0.07)',
                    borderRadius: 8, padding: '10px 14px',
                  }}>
                    <div style={{ fontSize: 10, color: '#4b5563', textTransform: 'uppercase', letterSpacing: '0.08em', marginBottom: 4 }}>Pair</div>
                    <div style={{ fontSize: 18, fontWeight: 800, color: '#f1f5f9', letterSpacing: '0.02em' }}>{analysis.pair}</div>
                  </div>
                  <div style={{
                    flex: 1,
                    background: biasMeta.bg,
                    border: `1px solid ${biasMeta.border}`,
                    borderRadius: 8, padding: '10px 14px',
                  }}>
                    <div style={{ fontSize: 10, color: '#4b5563', textTransform: 'uppercase', letterSpacing: '0.08em', marginBottom: 4 }}>Bias</div>
                    <div style={{ fontSize: 18, fontWeight: 800, color: biasMeta.text, textTransform: 'uppercase' }}>{analysis.bias}</div>
                  </div>
                </div>

                {/* Confidence bar */}
                <ConfidenceBar value={analysis.confidence} />

                {/* Reason */}
                <div style={{
                  marginTop: 14,
                  background: 'rgba(255,255,255,0.03)',
                  border: '1px solid rgba(255,255,255,0.06)',
                  borderRadius: 8, padding: '10px 14px',
                }}>
                  <div style={{ fontSize: 10, color: '#4b5563', textTransform: 'uppercase', letterSpacing: '0.08em', marginBottom: 6 }}>Analysis</div>
                  <div style={{ fontSize: 12, color: '#94a3b8', lineHeight: 1.6 }}>{analysis.reason}</div>
                </div>
              </div>
            )}
          </div>
        </div>
      )}
    </>
  )
}