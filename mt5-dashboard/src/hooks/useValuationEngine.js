/**
 * useValuationEngine.js
 * Drop into mt5-dashboard/src/hooks/
 *
 * Connects the Vestro frontend to the Python valuation engine backend.
 * - REST: fetches firms + signals on mount
 * - WebSocket: receives live score updates, patches firms state in real-time
 *
 * Usage:
 *   import { useValuationEngine } from '../hooks/useValuationEngine'
 *   const { firms, signals, loading, error, wsStatus, fetchFirmDetail } = useValuationEngine()
 */
import { useState, useEffect, useRef, useCallback } from 'react'

const API_BASE = import.meta.env.VITE_API_URL || 'http://localhost:8000'
const WS_BASE  = API_BASE.replace(/^https/, 'wss').replace(/^http/, 'ws')

export function useValuationEngine() {
  const [firms,    setFirms]    = useState([])
  const [signals,  setSignals]  = useState([])
  const [loading,  setLoading]  = useState(true)
  const [error,    setError]    = useState(null)
  const [wsStatus, setWsStatus] = useState('connecting') // connecting | connected | reconnecting | error

  const wsRef   = useRef(null)
  const pingRef = useRef(null)

  // ── REST helpers ──────────────────────────────────────────────────────

  const fetchFirms = useCallback(async (params = {}) => {
    const qs  = new URLSearchParams(params).toString()
    const res = await fetch(`${API_BASE}/api/firms${qs ? '?' + qs : ''}`)
    if (!res.ok) throw new Error(`firms ${res.status}`)
    return res.json()
  }, [])

  const fetchSignals = useCallback(async (firmId = null, limit = 50) => {
    const params = firmId ? `?firm_id=${firmId}&limit=${limit}` : `?limit=${limit}`
    const res    = await fetch(`${API_BASE}/api/signals${params}`)
    if (!res.ok) throw new Error(`signals ${res.status}`)
    return res.json()
  }, [])

  const fetchFirmDetail = useCallback(async (firmId) => {
    const res = await fetch(`${API_BASE}/api/firms/${firmId}`)
    if (!res.ok) throw new Error(`firm ${res.status}`)
    return res.json()
  }, [])

  // ── Initial load ──────────────────────────────────────────────────────

  useEffect(() => {
    let cancelled = false
    ;(async () => {
      try {
        setLoading(true)
        const [f, s] = await Promise.all([fetchFirms(), fetchSignals()])
        if (!cancelled) { setFirms(f); setSignals(s); setError(null) }
      } catch (e) {
        if (!cancelled) setError(e.message)
      } finally {
        if (!cancelled) setLoading(false)
      }
    })()
    return () => { cancelled = true }
  }, [fetchFirms, fetchSignals])

  // ── WebSocket ─────────────────────────────────────────────────────────

  useEffect(() => {
    let reconnectTimer = null

    const connect = () => {
      const ws = new WebSocket(`${WS_BASE}/ws/stream`)
      wsRef.current = ws

      ws.onopen = () => {
        setWsStatus('connected')
        pingRef.current = setInterval(() => {
          if (ws.readyState === WebSocket.OPEN) ws.send('ping')
        }, 25_000)
      }

      ws.onmessage = ({ data }) => {
        try {
          const msg = JSON.parse(data)
          if (msg === 'pong' || msg?.type === 'ping') return

          // Snapshot on connect — array of top scores
          if (Array.isArray(msg)) {
            setFirms(prev => _applyUpdates(prev, msg))
            return
          }

          // Live score update
          if (msg.type === 'score_update') {
            setFirms(prev => _applyUpdates(prev, [msg]))
          }
        } catch { /* ignore parse errors */ }
      }

      ws.onclose = () => {
        setWsStatus('reconnecting')
        clearInterval(pingRef.current)
        reconnectTimer = setTimeout(connect, 4_000)
      }

      ws.onerror = () => {
        setWsStatus('error')
        ws.close()
      }
    }

    connect()
    return () => {
      clearTimeout(reconnectTimer)
      clearInterval(pingRef.current)
      wsRef.current?.close()
    }
  }, [])

  return {
    firms,
    signals,
    loading,
    error,
    wsStatus,
    fetchFirmDetail,
    fetchSignals,
    refetch: () => fetchFirms().then(setFirms),
  }
}

// ── Helpers ───────────────────────────────────────────────────────────────

function _applyUpdates(firms, updates) {
  const map = new Map(firms.map(f => [f.id, f]))
  updates.forEach(u => {
    const existing = map.get(u.firm_id)
    if (existing) {
      map.set(u.firm_id, {
        ...existing,
        score: {
          rise_prob:  u.rise_prob,
          fall_prob:  u.fall_prob,
          conviction: u.conviction,
          top_driver: u.top_driver,
        },
      })
    }
  })
  return Array.from(map.values())
}