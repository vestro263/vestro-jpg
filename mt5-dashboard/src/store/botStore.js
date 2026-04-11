import { create } from 'zustand'
import { persist } from 'zustand/middleware'
import axios from 'axios'

const API    = 'https://vestro-jpg.onrender.com'
const WS_URL = 'wss://vestro-jpg.onrender.com/api/ws'

let reconnectAttempts = 0
const MAX_RECONNECT = 10
const backoffDelay  = (n) => Math.min(1000 * 2 ** n, 30000)

const useBotStore = create(
  persist(
    (set, get) => ({
      connected: false,
      wsError:   null,
      ws:        null,
      account: {
        balance: 0, equity: 0, profit: 0,
        margin_free: 0, currency: 'USD',
        name: '—', leverage: 0,
        is_virtual: false,
      },
      signals:        [],
      positions:      [],
      tradeFeed:      [],
      journal:        [],
      journalLoading: false,
      stats:          null,
      statsLoading:   false,
      activePage:     'dashboard',
      botRunning:     false,
      setActivePage:  (page) => set({ activePage: page }),

      // ── auth ──────────────────────────────────────────────
      isLoggedIn:      false,
      broker:          null,
      accountId:       null,
      authError:       null,
      derivAccounts:   null,
      pendingAccounts: null,

      setDerivAccounts: (accounts) => set({
        derivAccounts:   accounts,
        pendingAccounts: accounts,
      }),

      setPendingAccounts: (accounts) => set({ pendingAccounts: accounts }),

      // ── login ─────────────────────────────────────────────
      login: (broker, accountId, accountData) => {
        set({
          isLoggedIn:      true,
          broker,
          accountId,
          authError:       null,
          pendingAccounts: null,
          account: {
            ...accountData,
            is_virtual: accountId?.startsWith('VRT') ?? false,
          },
        })
        get().connect()
        get().startPolling()

        // fetch bot status scoped to this account
        fetch(`${API}/api/bot/status`)
          .then(r => r.json())
          .then(d => set({ botRunning: d.running }))
          .catch(() => {})
      },

      // ── logout ────────────────────────────────────────────
      logout: () => {
        const ws = get().ws
        if (ws) ws.close(1000)
        set({
          isLoggedIn:      false,
          broker:          null,
          accountId:       null,
          connected:       false,
          ws:              null,
          pendingAccounts: get().derivAccounts,
          account: {
            balance: 0, equity: 0, profit: 0,
            margin_free: 0, currency: 'USD',
            name: '—', leverage: 0, is_virtual: false,
          },
          positions:  [],
          signals:    [],
          tradeFeed:  [],
          botRunning: false,
          activePage: 'dashboard',
        })
      },

      setAuthError: (err) => set({ authError: err }),

      // ── bot controls ──────────────────────────────────────
      startBot: async () => {
        try {
          const res = await axios.post(`${API}/api/bot/start`)
          if (res.data.status === 'started') set({ botRunning: true })
        } catch (err) {
          console.warn('Failed to start bot:', err)
        }
      },

      stopBot: async () => {
        try {
          const res = await axios.post(`${API}/api/bot/stop`)
          if (res.data.status === 'stopped') set({ botRunning: false })
        } catch (err) {
          console.warn('Failed to stop bot:', err)
        }
      },

      syncBotStatus: async () => {
        try {
          const { data } = await axios.get(`${API}/api/bot/status`)
          set({ botRunning: data.running })
        } catch {}
      },

      // ── websocket ─────────────────────────────────────────
      connect: () => {
        const existing = get().ws
        if (
          existing &&
          (existing.readyState === WebSocket.OPEN ||
           existing.readyState === WebSocket.CONNECTING)
        ) return
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
            if (data.account) {
              set({
                account: {
                  ...data.account,
                  is_virtual: state.accountId?.startsWith('VRT') ?? false,
                }
              })
            }
            if (typeof data.bot_running === 'boolean') {
              set({ botRunning: data.bot_running })
            }
            return
          }

          if (data.type === 'signal') {
            const entry = {
              ...data,
              id:         Date.now() + Math.random(),
              receivedAt: new Date().toLocaleTimeString(),
            }
            set({ signals: [entry, ...state.signals].slice(0, 100) })
            return
          }

          if (data.type === 'contract_update') {
            const update   = { ...data, id: data.contract_id, time: new Date().toLocaleTimeString() }
            const existing = state.tradeFeed.findIndex(t => t.contract_id === data.contract_id)
            if (existing >= 0) {
              const updated = [...state.tradeFeed]
              updated[existing] = { ...updated[existing], ...update }
              set({ tradeFeed: updated })
            } else {
              set({ tradeFeed: [update, ...state.tradeFeed].slice(0, 200) })
            }
            if (data.is_expired || data.is_sold) {
              get().fetchAccount()
            }
            return
          }

          if (data.type === 'tp1_hit' || data.trade) {
            const item = {
              ...data,
              id:   Date.now() + Math.random(),
              time: new Date().toLocaleTimeString(),
            }
            set({ tradeFeed: [item, ...state.tradeFeed].slice(0, 200) })
            get().fetchPositions()
            return
          }
        }

        set({ ws })
      },

      // ── data fetchers ─────────────────────────────────────
      fetchAccount: async () => {
        const { accountId } = get()
        if (!accountId) return
        try {
          const { data } = await axios.get(`${API}/api/account/${accountId}`)
          set({
            account: {
              ...data,
              is_virtual: accountId.startsWith('VRT'),
            }
          })
        } catch {}
      },

      fetchPositions: async () => {
        const { accountId } = get()
        if (!accountId) return
        try {
          const { data } = await axios.get(`${API}/api/positions?account_id=${accountId}`)
          set({ positions: Array.isArray(data) ? data : [] })
        } catch {}
      },

      fetchJournal: async (limit = 50) => {
        const { accountId } = get()
        if (!accountId) return
        set({ journalLoading: true })
        try {
          const { data } = await axios.get(
            `${API}/api/journal?account_id=${accountId}&limit=${limit}`
          )
          set({ journal: Array.isArray(data) ? data : [] })
        } finally {
          set({ journalLoading: false })
        }
      },

      fetchStats: async (days = 30) => {
        const { accountId } = get()
        if (!accountId) return
        set({ statsLoading: true })
        try {
          const { data } = await axios.get(
            `${API}/api/stats?account_id=${accountId}&days=${days}`
          )
          set({ stats: data })
        } finally {
          set({ statsLoading: false })
        }
      },

      // ── polling ───────────────────────────────────────────
      startPolling: () => {
        // clear any existing interval to avoid duplicates on re-login
        if (get()._pollInterval) clearInterval(get()._pollInterval)
        const id = setInterval(() => {
          get().fetchPositions()
          get().fetchAccount()
          get().syncBotStatus()   // keep bot status in sync with file state
        }, 5000)
        set({ _pollInterval: id })
      },
    }),

    {
      name: 'vestro-auth',
      partialize: (s) => ({
        broker:        s.broker,
        accountId:     s.accountId,
        account:       s.account,
        botRunning:    s.botRunning,
        derivAccounts: s.derivAccounts,
      }),
    }
  )
)

export default useBotStore