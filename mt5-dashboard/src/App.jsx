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
    isLoggedIn,           // ← read directly from store, NEVER derive as !!accountId
    activePage,
    pendingAccounts,
    setPendingAccounts,
  } = useBotStore()

  const isMobile    = useIsMobile()
  const [authChecked, setAuthChecked] = useState(false)

  useEffect(() => {
    const params        = new URLSearchParams(window.location.search)
    const accountsParam = params.get('accounts')
    const error         = params.get('error')

    // Always clean the URL immediately
    window.history.replaceState({}, '', '/')

    if (error) {
      const derivDemoUrl = params.get('deriv_demo_url')
      if (derivDemoUrl) {
        useBotStore.getState().setDemoUrl(decodeURIComponent(derivDemoUrl))
      }

      useBotStore.getState().setAuthError(
        error === 'google_auth_failed'    ? 'Google sign-in failed. Please try again.' :
        error === 'google_token_failed'   ? 'Google authentication failed. Please try again.' :
        error === 'no_deriv_accounts'     ? 'No Deriv accounts found. Please connect one.' :
        error === 'demo_account_required' ? 'Vestro requires a Deriv demo account. Please create one and reconnect.' :
        'Something went wrong. Please try again.'
      )
      setAuthChecked(true)
      return
    }

    if (accountsParam) {
      // Fresh OAuth callback — parse accounts and show selector
      try {
        const accounts = JSON.parse(decodeURIComponent(accountsParam))
        if (Array.isArray(accounts) && accounts.length) {
          // Always use setPendingAccounts here — NOT setDerivAccounts.
          // setDerivAccounts also sets pendingAccounts but additionally
          // persists derivAccounts; for an OAuth redirect we only need
          // the selector to appear, so we set pending only and let login()
          // persist what matters.
          useBotStore.getState().setPendingAccounts(accounts)
        }
      } catch (e) {
        console.warn('[App] failed to parse accounts param:', e)
      }
      setAuthChecked(true)
      return
    }

    if (isLoggedIn) {
      // Already authenticated from persisted store — go straight to dashboard
      setAuthChecked(true)
      return
    }

    // Not logged in and no OAuth callback — check if we have a saved userId
    // that's still valid, so we can skip the Login page and show the selector.
    const savedUserId = useBotStore.getState().userId
    if (savedUserId) {
      fetch(`${API}/auth/check/${savedUserId}`)
        .then(r => r.json())
        .then(data => {
          if (data.found && data.accounts?.length) {
            const saved = useBotStore.getState().derivAccounts
            if (saved?.length) {
              useBotStore.getState().setPendingAccounts(saved)
            }
          }
        })
        .catch(() => {})
        .finally(() => setAuthChecked(true))
      return
    }

    setAuthChecked(true)
  }, []) // eslint-disable-line react-hooks/exhaustive-deps
  // ↑ intentionally empty deps — this runs once on mount to handle the
  //   OAuth redirect and any persisted-session restore. Re-running it on
  //   isLoggedIn changes would cause loops.

  useEffect(() => {
    if (isLoggedIn && !useBotStore.getState().connected) {
      useBotStore.getState().connect()
      useBotStore.getState().startPolling()
    }
  }, [isLoggedIn])

  // Render nothing until we've resolved the auth state (avoids flicker)
  if (!authChecked) return null

  // OAuth just returned accounts — show selector before anything else
  if (pendingAccounts?.length) {
    return (
      <AccountSelector
        accounts={pendingAccounts}
        onSelect={() => setPendingAccounts(null)}
      />
    )
  }

  // Not authenticated — show login
  if (!isLoggedIn) return <Login />

  // Authenticated — render the dashboard
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
