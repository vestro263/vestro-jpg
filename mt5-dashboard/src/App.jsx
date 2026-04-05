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

const API = 'https://vestro-jpg.onrender.com'

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
    accountId, activePage,
    pendingAccounts, setPendingAccounts, setDerivAccounts,
  } = useBotStore()
  const isLoggedIn  = !!accountId
  const isMobile    = useIsMobile()
  const [authChecked, setAuthChecked] = useState(false)

  useEffect(() => {
    const params        = new URLSearchParams(window.location.search)
    const accountsParam = params.get('accounts')
    const error         = params.get('error')

    if (error) {
      useBotStore.getState().setAuthError('Deriv login failed. Please try again.')
      window.history.replaceState({}, '', '/')
      setAuthChecked(true)

    } else if (accountsParam) {
      // Fresh OAuth callback — overwrite saved list and show selector
      try {
        const accounts = JSON.parse(decodeURIComponent(accountsParam))
        setDerivAccounts(accounts)
      } catch {}
      window.history.replaceState({}, '', '/')
      setAuthChecked(true)

    } else if (!isLoggedIn) {
      // Try persisted list first, fall back to fetching from backend
      const saved = useBotStore.getState().derivAccounts
      if (saved?.length) {
        setPendingAccounts(saved)
        setAuthChecked(true)
      } else {
        fetch(`${API}/api/accounts`)
          .then(r => r.json())
          .then(accounts => {
            if (accounts?.length) setDerivAccounts(accounts)
          })
          .catch(() => {})
          .finally(() => setAuthChecked(true))
      }

    } else {
      setAuthChecked(true)
    }
  }, [])

  useEffect(() => {
    if (isLoggedIn && !useBotStore.getState().connected) {
      useBotStore.getState().connect()
      useBotStore.getState().startPolling()
    }
  }, [isLoggedIn])

  if (!authChecked) return null

  if (pendingAccounts?.length) {
    return (
      <AccountSelector
        accounts={pendingAccounts}
        onSelect={() => setPendingAccounts(null)}
      />
    )
  }

  if (!isLoggedIn) return <Login />

  const Page = PAGES[activePage] ?? Dashboard

  if (isMobile) {
    return (
      <div style={{
        display: 'flex',
        flexDirection: 'column',
        minHeight: '100dvh',
        background: '#030712',
      }}>
        <Sidebar />
        <main style={{
          flex: 1,
          overflowY: 'auto',
          WebkitOverflowScrolling: 'touch',
          paddingBottom: 'calc(52px + env(safe-area-inset-bottom, 0px))',
        }}>
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