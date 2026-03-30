// src/store/botStore.js
import { create } from 'zustand'
import { persist } from 'zustand/middleware'  // ← add this import
import axios from 'axios'

const API = 'https://vestro-jpg.onrender.com'
const WS_URL = 'wss://vestro-jpg.onrender.com/api/ws'

let reconnectAttempts = 0
const MAX_RECONNECT = 10
const backoffDelay = (n) => Math.min(1000 * 2 ** n, 30000)

const useBotStore = create(
  persist(                                    // ← wrap with persist
    (set, get) => ({
      // ── ALL YOUR EXISTING STATE (unchanged) ─────────
      connected: false,
      wsError: null,
      ws: null,
      account: {
        balance: 0, equity: 0, profit: 0,
        margin_free: 0, currency: 'USD',
        name: '—', leverage: 0
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

      // ── NEW: auth state ──────────────────────────────
      isLoggedIn: false,
      broker: null,          // 'deriv' | 'welltrade'
      accountId: null,       // 'CR123456' or MT5 login
      authError: null,

      login: (broker, accountId, accountData) => {
        // after /api/connect succeeds — seed account + connect WS
        set({
          isLoggedIn: true,
          broker,
          accountId,
          authError: null,
          account: {
            ...get().account,
            ...accountData,
          },
        })
        get().connect()       // start WS immediately after login
        get().startPolling()  // start 5s polling
      },

      logout: () => {
        const ws = get().ws
        if (ws) ws.close(1000)
        set({
          isLoggedIn: false,
          broker: null,
          accountId: null,
          connected: false,
          ws: null,
          account: { balance: 0, equity: 0, profit: 0, margin_free: 0, currency: 'USD', name: '—', leverage: 0 },
          positions: [],
          signals: [],
          tradeFeed: [],
          botRunning: false,
          activePage: 'dashboard',
        })
      },

      setAuthError: (err) => set({ authError: err }),

      // ── ALL YOUR EXISTING METHODS (unchanged) ────────
      startBot: async () => {
        try {
          await axios.post(`${API}/api/bot/start`)
          set({ botRunning: true })
        } catch (err) { console.warn('Failed to start bot:', err) }
      },

      stopBot: async () => {
        try {
          await axios.post(`${API}/api/bot/stop`)
          set({ botRunning: false })
        } catch (err) { console.warn('Failed to stop bot:', err) }
      },

      connect: () => {
        const existing = get().ws
        if (existing && (existing.readyState === WebSocket.OPEN || existing.readyState === WebSocket.CONNECTING)) return
        if (existing) existing.close()

        const ws = new WebSocket(WS_URL)

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
              set({ wsError: `Backend unreachable after ${MAX_RECONNECT} attempts` })
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
          try { data = JSON.parse(e.data) } catch { return }
          const state = get()

          if (data.type === 'heartbeat') {
            set({ connected: true, wsError: null })
            if (data.account) set({ account: data.account })
            if (typeof data.bot_running === 'boolean') set({ botRunning: data.bot_running })
            return
          }
          if (data.type === 'signal') {
            const entry = { ...data, id: Date.now() + Math.random(), receivedAt: new Date().toLocaleTimeString() }
            set({ signals: [entry, ...state.signals].slice(0, 100) })
          }
          if (data.type === 'tp1_hit' || data.trade) {
            const item = { ...data, id: Date.now() + Math.random(), time: new Date().toLocaleTimeString() }
            set({ tradeFeed: [item, ...state.tradeFeed].slice(0, 200) })
            get().fetchPositions()
          }
        }

        set({ ws })
      },

      fetchAccount: async () => {
        try {
          const { data } = await axios.get(`${API}/api/account`)
          set({ account: data })
        } catch {}
      },

      fetchPositions: async () => {
        try {
          const { data } = await axios.get(`${API}/api/positions`)
          set({ positions: Array.isArray(data) ? data : [] })
        } catch {}
      },

      fetchJournal: async (limit = 50) => {
        set({ journalLoading: true })
        try {
          const { data } = await axios.get(`${API}/api/journal?limit=${limit}`)
          set({ journal: Array.isArray(data) ? data : [] })
        } finally { set({ journalLoading: false }) }
      },

      fetchStats: async (days = 30) => {
        set({ statsLoading: true })
        try {
          const { data } = await axios.get(`${API}/api/stats?days=${days}`)
          set({ stats: data })
        } finally { set({ statsLoading: false }) }
      },

      startPolling: () => {
        setInterval(() => {
          get().fetchPositions()
          get().fetchAccount()
        }, 5000)
      },
    }),
    {
      name: 'vestro-auth',                     // localStorage key
      partialize: (s) => ({                    // only persist auth — not WS/signals
        isLoggedIn: s.isLoggedIn,
        broker: s.broker,
        accountId: s.accountId,
      }),
    }
  )
)

export default useBotStore