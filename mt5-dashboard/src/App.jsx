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

    // Always clean the URL
    window.history.replaceState({}, '', '/')

    if (error) {
      // Pull the demo URL if backend sent one
      const derivDemoUrl = params.get('deriv_demo_url')
      if (derivDemoUrl) {
        useBotStore.getState().setDemoUrl(decodeURIComponent(derivDemoUrl))
      }

      useBotStore.getState().setAuthError(
        error === 'google_auth_failed'     ? 'Google sign-in failed. Please try again.' :
        error === 'google_token_failed'    ? 'Google authentication failed. Please try again.' :
        error === 'no_deriv_accounts'      ? 'No Deriv accounts found. Please connect one.' :
        error === 'demo_account_required'  ? 'Vestro requires a Deriv demo account. Please create one and reconnect.' :
        'Something went wrong. Please try again.'
      )
      setAuthChecked(true)
      return
    }

    if (accountsParam) {
      // OAuth callback — backend already saved to DB, just show selector
      try {
        const accounts = JSON.parse(decodeURIComponent(accountsParam))
        if (Array.isArray(accounts) && accounts.length) {
          setDerivAccounts(accounts)
        }
      } catch {}
      setAuthChecked(true)
      return
    }

    if (isLoggedIn) {
      setAuthChecked(true)
      return
    }

    // Check if we have a persisted user_id — verify it's still valid
    const savedUserId = useBotStore.getState().account?.user_id
    if (savedUserId) {
      fetch(`${API}/auth/check/${savedUserId}`)
        .then(r => r.json())
        .then(data => {
          if (data.found && data.accounts?.length) {
            const saved = useBotStore.getState().derivAccounts
            if (saved?.length) {
              setPendingAccounts(saved)
            }
          }
        })
        .catch(() => {})
        .finally(() => setAuthChecked(true))
      return
    }

    setAuthChecked(true)
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