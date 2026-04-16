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
  dashboard: Dashboard,
  valuations: Valuation,
  signals: Signals,
  positions: Positions,
  journal: Journal,
  stats: Performance,
}

export default function App() {
  const {
    isLoggedIn,
    activePage,
    pendingAccounts,
    setPendingAccounts,
  } = useBotStore()

  const isMobile = useIsMobile()
  const [authChecked, setAuthChecked] = useState(false)

  // 🔐 Handle OAuth + session restore
  useEffect(() => {
    const params = new URLSearchParams(window.location.search)
    const accountsParam = params.get('accounts')
    const error = params.get('error')

    if (accountsParam) {
      try {
        const accounts = JSON.parse(decodeURIComponent(accountsParam))

        if (Array.isArray(accounts) && accounts.length) {
          // Store accounts → show selector
          useBotStore.getState().setPendingAccounts(accounts)
        }
      } catch (e) {
        console.warn('[App] Failed to parse accounts:', e)
      }

      // Clean URL AFTER parsing
      window.history.replaceState({}, '', '/')

      setAuthChecked(true)
      return
    }

    if (error) {
      console.warn('[OAuth Error]', error)
      window.history.replaceState({}, '', '/')
      setAuthChecked(true)
      return
    }

    // Already logged in (persisted session)
    if (isLoggedIn) {
      setAuthChecked(true)
      return
    }

    // Try restoring saved account
    const savedAccount = localStorage.getItem('account')

    if (savedAccount) {
      try {
        const parsed = JSON.parse(savedAccount)

        // Restore session silently
        useBotStore.getState().login(parsed)
      } catch (e) {
        console.warn('[Restore] Failed:', e)
      }
    }

    setAuthChecked(true)
  }, []) // run once

  // 🔌 Connect WebSocket after login
  useEffect(() => {
    if (isLoggedIn && !useBotStore.getState().connected) {
      useBotStore.getState().connect()
      useBotStore.getState().startPolling()
    }
  }, [isLoggedIn])

  // ⛔ Prevent flicker
  if (!authChecked) return null

  // 🟡 Show account selector (after OAuth)
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

  // 🔴 Not logged in
  if (!isLoggedIn) return <Login />

  // 🟢 Logged in → render app
  const Page = PAGES[activePage] ?? Dashboard

  if (isMobile) {
    return (
      <div
        style={{
          display: 'flex',
          flexDirection: 'column',
          minHeight: '100dvh',
          background: '#030712',
        }}
      >
        <Sidebar />
        <main
          style={{
            flex: 1,
            overflowY: 'auto',
            WebkitOverflowScrolling: 'touch',
            paddingBottom: 'calc(52px + env(safe-area-inset-bottom, 0px))',
          }}
        >
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