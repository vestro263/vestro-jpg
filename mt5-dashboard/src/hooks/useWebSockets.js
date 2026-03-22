/**
 * useWebSocket — low-level WebSocket hook with exponential back-off reconnect.
 * Used internally by botStore; can also be used standalone in components.
 */

import { useEffect, useRef, useCallback } from 'react'

const DEFAULT_URL     = 'ws://localhost:8000/ws'
const MAX_RETRY_DELAY = 30_000   // 30 seconds max
const INIT_DELAY      = 1_000    // 1 second initial

export function useWebSocket(url = DEFAULT_URL, onMessage) {
  const wsRef        = useRef(null)
  const retryDelay   = useRef(INIT_DELAY)
  const retryTimer   = useRef(null)
  const mountedRef   = useRef(true)

  const connect = useCallback(() => {
    if (!mountedRef.current) return
    if (wsRef.current?.readyState === WebSocket.OPEN) return

    const ws = new WebSocket(url)
    wsRef.current = ws

    ws.onopen = () => {
      retryDelay.current = INIT_DELAY  // reset on success
    }

    ws.onmessage = (e) => {
      try {
        const data = JSON.parse(e.data)
        onMessage?.(data)
      } catch {
        // ignore malformed messages
      }
    }

    ws.onclose = () => {
      if (!mountedRef.current) return
      retryTimer.current = setTimeout(() => {
        retryDelay.current = Math.min(retryDelay.current * 2, MAX_RETRY_DELAY)
        connect()
      }, retryDelay.current)
    }

    ws.onerror = () => {
      ws.close()
    }
  }, [url, onMessage])

  useEffect(() => {
    mountedRef.current = true
    connect()
    return () => {
      mountedRef.current = false
      clearTimeout(retryTimer.current)
      wsRef.current?.close()
    }
  }, [connect])

  const send = useCallback((data) => {
    if (wsRef.current?.readyState === WebSocket.OPEN) {
      wsRef.current.send(typeof data === 'string' ? data : JSON.stringify(data))
    }
  }, [])

  return { send }
}