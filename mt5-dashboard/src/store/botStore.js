import { create } from 'zustand'
import axios from 'axios'

const API = 'https://vestro-jpg.onrender.com'
const ws = new WebSocket("wss://vestro-jpg.onrender.com/api/ws");

let reconnectAttempts = 0
const MAX_RECONNECT = 10
const backoffDelay  = (n) => Math.min(1000 * 2 ** n, 30000) // 1s, 2s, 4s … capped at 30s

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
  botRunning:     false,

  setActivePage: (page) => set({ activePage: page }),

  startBot: async () => {
    try {
      await axios.post(`${API}/bot/start`)
      set({ botRunning: true })
    } catch (err) {
      console.warn('Failed to start bot:', err)
    }
  },

  stopBot: async () => {
    try {
      await axios.post(`${API}/bot/stop`)
      set({ botRunning: false })
    } catch (err) {
      console.warn('Failed to stop bot:', err)
    }
  },

  connect: () => {
    const existing = get().ws
    if (existing && (existing.readyState === WebSocket.OPEN ||
                     existing.readyState === WebSocket.CONNECTING)) {
      return
    }
    if (existing) existing.close()

    const ws = new WebSocket(WS)

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
          set({ wsError: `Backend unreachable after ${MAX_RECONNECT} attempts — start your Python server then refresh.` })
          return
        }
        const delay = backoffDelay(reconnectAttempts)
        reconnectAttempts++
        setTimeout(() => get().connect(), delay)
      }
    }

    ws.onerror = () => {
      // onclose fires automatically after onerror — wsError is set there
    }

    ws.onmessage = (e) => {
      let data
      try { data = JSON.parse(e.data) } catch { return }
      const state = get()

      if (data.type === 'heartbeat') {
        set({ connected: true, wsError: null })
        if (data.account) set({ account: data.account })
        if (typeof data.bot_running === 'boolean') set({ botRunning: data.bot_running })
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
    setInterval(() => {
      get().fetchPositions()
      get().fetchAccount()
    }, 5000)
  },
}))

export default useBotStore