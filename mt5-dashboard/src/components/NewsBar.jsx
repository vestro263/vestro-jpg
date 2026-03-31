import { useEffect, useState } from 'react'
import axios from 'axios'

const API = import.meta.env.VITE_API_URL ?? 'https://vestro-jpg.onrender.com'
const AI_API = 'https://r3bel-production.up.railway.app'

const IMPACT_COLORS = {
  1: { bg: '#1c0a0a', border: '#3b0000', text: '#f87171', dot: '#ef4444' },
  2: { bg: '#1c1400', border: '#92400e', text: '#fbbf24', dot: '#f59e0b' },
}

export default function NewsBar({ symbol = null }) {
  const [events, setEvents] = useState([])
  const [loading, setLoading] = useState(true)

  const [selected, setSelected] = useState(null)
  const [analysis, setAnalysis] = useState(null)
  const [loadingAI, setLoadingAI] = useState(false)

  // 📰 Fetch News Only
  useEffect(() => {
    const fetchNews = async () => {
      setLoading(true)

      try {
        const url = symbol
          ? `${API}/api/news?symbol=${symbol}&hours=12`
          : `${API}/api/news?hours=6`

        const res = await axios.get(url)
        const news = Array.isArray(res.data) ? res.data.slice(0, 5) : []

        setEvents(news)
      } catch (err) {
        console.error('News fetch error:', err)
        setEvents([])
      } finally {
        setLoading(false)
      }
    }

    fetchNews()
  }, [symbol])

  // 🧠 AI Handler (on click)
  const handleClick = async (ev) => {
    setSelected(ev)
    setAnalysis(null)
    setLoadingAI(true)

    try {
      const prompt = `
You are a forex trading assistant.

Return JSON only:
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

      const res = await axios.post(`${AI_API}/chat`, { prompt })

      let data = res.data?.response || res.data

      if (typeof data === 'string') {
        try { data = JSON.parse(data) } catch {}
      }

      setAnalysis(data)

    } catch (err) {
      console.error('AI error:', err)
    } finally {
      setLoadingAI(false)
    }
  }

  if (loading || events.length === 0) return null

  return (
    <>
      {/* 📰 NEWS BAR */}
      <div style={{
        background: '#111827',
        borderBottom: '1px solid #1f2937',
        padding: '6px 24px',
        display: 'flex',
        alignItems: 'center',
        gap: 16,
        overflowX: 'auto',
      }}>
        <span style={{
          fontSize: 10,
          color: '#6b7280',
          textTransform: 'uppercase',
          letterSpacing: '0.05em',
          whiteSpace: 'nowrap'
        }}>
          Upcoming
        </span>

        {events.map((ev, i) => {
          const c = IMPACT_COLORS[ev.tier] || IMPACT_COLORS[2]
          const t = new Date(ev.time)

          const timeStr = t.toLocaleTimeString('en-GB', {
            hour: '2-digit',
            minute: '2-digit'
          })

          return (
            <div
              key={i}
              onClick={() => handleClick(ev)}
              style={{
                cursor: 'pointer',
                display: 'flex',
                alignItems: 'center',
                gap: 6,
                background: c.bg,
                border: `1px solid ${c.border}`,
                borderRadius: 6,
                padding: '3px 8px',
                whiteSpace: 'nowrap',
              }}
            >
              {/* dot */}
              <span style={{
                width: 6,
                height: 6,
                borderRadius: '50%',
                background: c.dot,
              }} />

              {/* currency */}
              <span style={{
                fontSize: 11,
                fontWeight: 600,
                color: c.text
              }}>
                {ev.currency}
              </span>

              {/* title */}
              <span style={{
                fontSize: 11,
                color: c.text
              }}>
                {ev.title.length > 30
                  ? ev.title.slice(0, 30) + '…'
                  : ev.title}
              </span>

              {/* time */}
              <span style={{
                fontSize: 10,
                color: '#6b7280'
              }}>
                {timeStr}
              </span>
            </div>
          )
        })}
      </div>

      {/* 🧠 AI MODAL */}
      {selected && (
        <div
          onClick={() => setSelected(null)}
          style={{
            position: 'fixed',
            top: 0,
            left: 0,
            width: '100%',
            height: '100%',
            background: 'rgba(0,0,0,0.6)',
            display: 'flex',
            alignItems: 'center',
            justifyContent: 'center',
            zIndex: 999
          }}
        >
          <div
            onClick={(e) => e.stopPropagation()}
            style={{
              background: '#0f172a',
              border: '1px solid #1f2937',
              borderRadius: 10,
              padding: 20,
              width: 400,
            }}
          >
            {/* Title */}
            <div style={{
              fontWeight: 700,
              marginBottom: 10
            }}>
              {selected.currency} — {selected.title}
            </div>

            {/* Loading */}
            {loadingAI && (
              <div style={{ color: '#6b7280' }}>
                Analyzing with AI...
              </div>
            )}

            {/* AI Result */}
            {analysis && (
              <>
                <div style={{ marginTop: 10 }}>
                  <strong>Pair:</strong> {analysis.pair}
                </div>

                <div>
                  <strong>Bias:</strong> {analysis.bias}
                </div>

                <div>
                  <strong>Confidence:</strong>{' '}
                  {(analysis.confidence * 100).toFixed(0)}%
                </div>

                <div style={{ marginTop: 10 }}>
                  <strong>Reason:</strong> {analysis.reason}
                </div>
              </>
            )}
          </div>
        </div>
      )}
    </>
  )
}