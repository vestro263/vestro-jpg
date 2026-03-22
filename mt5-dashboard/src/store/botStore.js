import { create } from 'zustand'
import axios from 'axios'

const API = 'http://localhost:8000'
const WS  = 'ws://localhost:8000/ws'

const useBotStore = create((set, get) => ({
  connected:      false,
  wsError:        null,
  ws:             null,
  account:        { balance:0, equity:0, profit:0, margin_free:0, currency:'USD', name:'—', leverage:0 },
  signals:        [],
  positions:      [],
  tradeFeed:      [],
  journal:        [],
  journalLoading: false,
  stats:          null,
  statsLoading:   false,
  activePage:     'dashboard',
  setActivePage:  (page) => set({ activePage: page }),

  connect: () => {
    // Don't create a new socket if one is already open or connecting
    const existing = get().ws
    if (existing && (existing.readyState === WebSocket.OPEN ||
                     existing.readyState === WebSocket.CONNECTING)) {
      return
    }
    if (existing) existing.close()

    const ws = new WebSocket(WS)

    ws.onopen = () => {
      set({ connected: true, wsError: null })
      get().fetchAccount()
      get().fetchPositions()
    }

    ws.onclose = (e) => {
      set({ connected: false, ws: null })
      // Only auto-reconnect on abnormal closure (not intentional close)
      if (e.code !== 1000) {
        setTimeout(() => get().connect(), 3000)
      }
    }

    ws.onerror = () => {
      set({ wsError: 'WebSocket error — is the bot running?' })
      // Don't close here — onclose fires automatically after onerror
    }

    ws.onmessage = (e) => {
      let data
      try { data = JSON.parse(e.data) } catch { return }
      const state = get()

      if (data.type === 'heartbeat') {
        // Keep connected flag fresh and update account on every heartbeat
        set({ connected: true, wsError: null })
        if (data.account) set({ account: data.account })
        return
      }

      if (data.type === 'signal') {
        const entry = {
          ...data,
          id:         Date.now() + Math.random(),
          receivedAt: new Date().toLocaleTimeString(),
        }
        set({ signals: [entry, ...state.signals].slice(0, 100) })
        if (data.account) set({ account: { ...state.account, ...data.account } })
      }

      if (data.type === 'tp1_hit' || data.trade) {
        const item = {
          ...data,
          id:   Date.now() + Math.random(),
          time: new Date().toLocaleTimeString(),
        }
        set({ tradeFeed: [item, ...state.tradeFeed].slice(0, 200) })
        get().fetchPositions()
      }

      if (data.type === 'error') {
        console.warn('Bot error:', data.error)
      }
    }

    set({ ws })
  },

  fetchAccount: async () => {
    try {
      const { data } = await axios.get(`${API}/account`)
      if (data && !data.error) set({ account: data })
    } catch {}
  },

  fetchPositions: async () => {
    try {
      const { data } = await axios.get(`${API}/positions`)
      set({ positions: Array.isArray(data) ? data : [] })
    } catch {}
  },

  fetchJournal: async (limit = 50) => {
    set({ journalLoading: true })
    try {
      const { data } = await axios.get(`${API}/journal?limit=${limit}`)
      set({ journal: Array.isArray(data) ? data : [] })
    } catch {
    } finally {
      set({ journalLoading: false })
    }
  },

  fetchStats: async (days = 30) => {
    set({ statsLoading: true })
    try {
      const { data } = await axios.get(`${API}/stats?days=${days}`)
      set({ stats: data })
    } catch {
    } finally {
      set({ statsLoading: false })
    }
  },

  startPolling: () => {
    // Fallback REST polling every 5s (positions + account)
    // WebSocket heartbeat already keeps account fresh — this is a safety net
    setInterval(() => {
      get().fetchPositions()
      get().fetchAccount()
    }, 5000)
  },
}))

export default useBotStore