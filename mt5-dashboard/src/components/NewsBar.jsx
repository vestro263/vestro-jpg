/**
 * NewsBar — fetches and displays upcoming high-impact news events.
 * Shown at the top of the Dashboard as a warning strip.
 */

import { useEffect, useState } from 'react'
import axios from 'axios'

const API = 'http://localhost:8000'

const IMPACT_COLORS = {
  1: { bg: '#1c0a0a', border: '#3b0000', text: '#f87171', dot: '#ef4444' },
  2: { bg: '#1c1400', border: '#92400e', text: '#fbbf24', dot: '#f59e0b' },
}

export default function NewsBar({ symbol = null }) {
  const [events, setEvents]   = useState([])
  const [loading, setLoading] = useState(true)

  useEffect(() => {
    const url = symbol
      ? `${API}/news?symbol=${symbol}&hours=12`
      : `${API}/news?hours=6`

    axios.get(url)
      .then(r => setEvents(Array.isArray(r.data) ? r.data.slice(0, 5) : []))
      .catch(() => setEvents([]))
      .finally(() => setLoading(false))
  }, [symbol])

  if (loading || events.length === 0) return null

  return (
    <div style={{
      background: '#111827',
      borderBottom: '1px solid #1f2937',
      padding: '6px 24px',
      display: 'flex',
      alignItems: 'center',
      gap: 16,
      overflowX: 'auto',
      flexShrink: 0,
    }}>
      <span style={{ fontSize: 10, color: '#6b7280', textTransform: 'uppercase',
        letterSpacing: '0.05em', whiteSpace: 'nowrap', flexShrink: 0 }}>
        Upcoming
      </span>
      {events.map((ev, i) => {
        const c = IMPACT_COLORS[ev.tier] || IMPACT_COLORS[2]
        const t = new Date(ev.time)
        const timeStr = t.toLocaleTimeString('en-GB', {
          hour: '2-digit', minute: '2-digit'
        })
        return (
          <div key={i} style={{
            display: 'flex', alignItems: 'center', gap: 6,
            background: c.bg, border: `1px solid ${c.border}`,
            borderRadius: 6, padding: '3px 8px', whiteSpace: 'nowrap', flexShrink: 0,
          }}>
            <span style={{
              width: 6, height: 6, borderRadius: '50%',
              background: c.dot, display: 'inline-block', flexShrink: 0,
            }} />
            <span style={{ fontSize: 11, fontWeight: 600, color: c.text }}>
              {ev.currency}
            </span>
            <span style={{ fontSize: 11, color: c.text }}>
              {ev.title.length > 30 ? ev.title.slice(0, 30) + '…' : ev.title}
            </span>
            <span style={{ fontSize: 10, color: '#6b7280' }}>{timeStr}</span>
          </div>
        )
      })}
    </div>
  )
}