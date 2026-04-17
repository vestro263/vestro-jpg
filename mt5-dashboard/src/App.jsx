import { useState, useEffect } from 'react'
import Sidebar from './components/Sidebar'
import Dashboard from './pages/Dashboard'
import Signals from './pages/Signals'
import Positions from './pages/Positions'
import Journal from './pages/Journal'
import Performance from './pages/Performance'
import Valuation from './pages/Valuation'
import useBotStore from './store/botStore'
import Login from './pages/Login'
import AccountSelector from './pages/AccountSelector'

const API = import.meta.env.VITE_API_URL ?? 'https://vestro-jpg.onrender.com'

function useIsMobile(bp = 768) {
  const [m, setM] = useState(() => window.innerWidth < bp)
  useEffect(() => {
    const h = () => setM(window.innerWidth < bp)
    window.addEventListener('resize', h)
    return () => window.removeEventListener('resize', h)
  }, [bp])
  return m
}

const PAGES = {
  dashboard:  Dashboard,
  valuations: Valuation,
  signals:    Signals,
  positions:  Positions,
  journal:    Journal,
  stats:      Performance,
}

export default function App() {
  const { isLoggedIn, activePage, pendingAccounts, setPendingAccounts } = useBotStore()
  const isMobile    = useIsMobile()
  const [authChecked, setAuthChecked] = useState(false)

  useEffect(() => {
    const params        = new URLSearchParams(window.location.search)
    const accountsParam = params.get('accounts')
    const error         = params.get('error')

    window.history.replaceState({}, '', '/')

    // ── OAuth redirect with accounts → show selector ──────
    if (accountsParam) {
      try {
        const accounts = JSON.parse(decodeURIComponent(accountsParam))
        if (Array.isArray(accounts) && accounts.length) {
          useBotStore.getState().setPendingAccounts(accounts)
        }
      } catch (e) {
        console.warn('[App] Failed to parse accounts:', e)
      }
      setAuthChecked(true)
      return
    }

    if (error) {
      console.warn('[OAuth Error]', error)
      setAuthChecked(true)
      return
    }

    // ── Already logged in (persisted session) → reconnect ─
    if (isLoggedIn) {
      const state = useBotStore.getState()
      if (!state.connected) {
        state.connect()
        state.startPolling()
      }
      setAuthChecked(true)
      return
    }

    // ── Try session restore via saved userId ──────────────
    const savedUserId = useBotStore.getState().userId
    if (savedUserId) {
      fetch(`${API}/auth/check/${savedUserId}`)
        .then(r => r.json())
        .then(data => {
          if (data.found && data.accounts?.length) {
            // Enrich with email/user_id for the selector
            const enriched = data.accounts.map(a => ({
              ...a,
              user_id: data.user_id,
              email:   data.email,
            }))
            useBotStore.getState().setPendingAccounts(enriched)
          }
        })
        .catch(() => {})
        .finally(() => setAuthChecked(true))
      return
    }

    setAuthChecked(true)
  }, [])

  // Connect WS when isLoggedIn flips true
  useEffect(() => {
    if (isLoggedIn && !useBotStore.getState().connected) {
      useBotStore.getState().connect()
      useBotStore.getState().startPolling()
    }
  }, [isLoggedIn])

  if (!authChecked) return null

  // Show selector — after OAuth or session restore
  if (pendingAccounts?.length) {
    return (
      <AccountSelector
        accounts={pendingAccounts}
        onSelect={(account) => {
          useBotStore.getState().login(account)
          setPendingAccounts(null)
        }}
      />
    )
  }

  if (!isLoggedIn) return <Login />

  const Page = PAGES[activePage] ?? Dashboard

  if (isMobile) {
    return (
      <div style={{ display: 'flex', flexDirection: 'column', minHeight: '100dvh', background: '#030712' }}>
        <Sidebar />
        <main style={{ flex: 1, overflowY: 'auto', WebkitOverflowScrolling: 'touch', paddingBottom: 'calc(52px + env(safe-area-inset-bottom, 0px))' }}>
          <Page />
        </main>
      </div>
    )
  }

  return (
    <div style={{ display: 'flex', minHeight: '100vh', background: '#030712' }}>
      <Sidebar />
      <main style={{ flex: 1, overflowY: 'auto' }}>
        <Page />
      </main>
    </div>
  )
}