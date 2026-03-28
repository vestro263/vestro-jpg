import { create } from 'zustand'
import axios from 'axios'

// ✅ Base URL (no /api here)
const API = 'https://vestro-jpg.onrender.com'

// ✅ WebSocket URL
const WS_URL = 'wss://vestro-jpg.onrender.com/api/ws'

let reconnectAttempts = 0
const MAX_RECONNECT = 10
const backoffDelay = (n) => Math.min(1000 * 2 ** n, 30000)

const useBotStore = create((set, get) => ({
  connected: false,
  wsError: null,
  ws: null,

  account: {
    balance: 0,
    equity: 0,
    profit: 0,
    margin_free: 0,
    currency: 'USD',
    name: '—',
    leverage: 0
  },

  signals: [],
  positions: [],
  tradeFeed: [],
  journal: [],
  journalLoading: false,
  stats: null,
  statsLoading: false,
  activePage: 'dashboard',
  botRunning: false,

  setActivePage: (page) => set({ activePage: page }),

  // ── BOT CONTROL ─────────────────────────

  startBot: async () => {
    try {
      await axios.post(`${API}/api/bot/start`)
      set({ botRunning: true })
    } catch (err) {
      console.warn('Failed to start bot:', err)
    }
  },

  stopBot: async () => {
    try {
      await axios.post(`${API}/api/bot/stop`)
      set({ botRunning: false })
    } catch (err) {
      console.warn('Failed to stop bot:', err)
    }
  },

  // ── WEBSOCKET ───────────────────────────

  connect: () => {
    const existing = get().ws

    if (
      existing &&
      (existing.readyState === WebSocket.OPEN ||
        existing.readyState === WebSocket.CONNECTING)
    ) {
      return
    }

    if (existing) existing.close()

    const ws = new WebSocket(WS_URL) // ✅ FIXED

    ws.onopen = () => {
      reconnectAttempts = 0
      set({ connected: true, wsError: null })

      get().fetchAccount()
      get().fetchPositions()
    }

    ws.onclose = (e) => {
      set({ connected: false, ws: null })

      if (e.code !== 1000) {
        if (reconnectAttempts >= MAX_RECONNECT) {
          set({
            wsError: `Backend unreachable after ${MAX_RECONNECT} attempts`
          })
          return
        }

        const delay = backoffDelay(reconnectAttempts)
        reconnectAttempts++

        setTimeout(() => get().connect(), delay)
      }
    }

    ws.onerror = () => {}

    ws.onmessage = (e) => {
      let data
      try {
        data = JSON.parse(e.data)
      } catch {
        return
      }

      const state = get()

      if (data.type === 'heartbeat') {
        set({ connected: true, wsError: null })

        if (data.account) set({ account: data.account })
        if (typeof data.bot_running === 'boolean') {
          set({ botRunning: data.bot_running })
        }
        return
      }

      if (data.type === 'signal') {
        const entry = {
          ...data,
          id: Date.now() + Math.random(),
          receivedAt: new Date().toLocaleTimeString()
        }

        set({ signals: [entry, ...state.signals].slice(0, 100) })
      }

      if (data.type === 'tp1_hit' || data.trade) {
        const item = {
          ...data,
          id: Date.now() + Math.random(),
          time: new Date().toLocaleTimeString()
        }

        set({ tradeFeed: [item, ...state.tradeFeed].slice(0, 200) })
        get().fetchPositions()
      }
    }

    set({ ws })
  },

  // ── API CALLS ───────────────────────────

  fetchAccount: async () => {
    try {
      const { data } = await axios.get(`${API}/api/account`) // ✅ FIXED
      set({ account: data })
    } catch {}
  },

  fetchPositions: async () => {
    try {
      const { data } = await axios.get(`${API}/api/positions`) // ✅ FIXED
      set({ positions: Array.isArray(data) ? data : [] })
    } catch {}
  },

  fetchJournal: async (limit = 50) => {
    set({ journalLoading: true })

    try {
      const { data } = await axios.get(`${API}/api/journal?limit=${limit}`) // ✅ FIXED
      set({ journal: Array.isArray(data) ? data : [] })
    } finally {
      set({ journalLoading: false })
    }
  },

  fetchStats: async (days = 30) => {
    set({ statsLoading: true })

    try {
      const { data } = await axios.get(`${API}/api/stats?days=${days}`) // ✅ FIXED
      set({ stats: data })
    } finally {
      set({ statsLoading: false })
    }
  },

  startPolling: () => {
    setInterval(() => {
      get().fetchPositions()
      get().fetchAccount()
    }, 5000)
  }
}))

export default useBotStore