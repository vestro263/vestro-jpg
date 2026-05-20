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

  // ── DEBUG — remove once selector issue is resolved ────────
  console.log('[DEBUG] App render:', {
    pendingAccounts,
    pendingAccountsIsArray: Array.isArray(pendingAccounts),
    pendingAccountsLength:  Array.isArray(pendingAccounts) ? pendingAccounts.length : 'n/a',
    isLoggedIn,
    authChecked,
    accountsParam: new URLSearchParams(window.location.search).get('accounts'),
    href: window.location.href,
    localStorageVestroAuth: (() => {
      try { return JSON.parse(localStorage.getItem('vestro-auth')) } catch { return null }
    })(),
  })
  // ── END DEBUG ─────────────────────────────────────────────

  useEffect(() => {
    const params        = new URLSearchParams(window.location.search)
    const accountsParam = params.get('accounts')
    const error         = params.get('error')

    // ── DEBUG — log URL state at effect time ──────────────
    console.log('[DEBUG] useEffect fired:', {
      accountsParam,
      error,
      fullSearch: window.location.search,
      isLoggedIn,
      savedUserId: useBotStore.getState().userId,
    })
    // ── END DEBUG ─────────────────────────────────────────

    window.history.replaceState({}, '', '/')

    // ── OAuth redirect with accounts → show selector ──────
    if (accountsParam) {
      try {
        const accounts = JSON.parse(decodeURIComponent(accountsParam))
        console.log('[DEBUG] parsed accounts from URL:', accounts)
        if (Array.isArray(accounts)) {
          if (accounts[0]?.user_id) {
            useBotStore.getState().setUserId(accounts[0].user_id)
          }
          useBotStore.getState().setPendingAccounts(accounts)
          console.log('[DEBUG] setPendingAccounts called with:', accounts)
        }
      } catch (e) {
        console.warn('[App] Failed to parse accounts:', e)
        useBotStore.getState().setPendingAccounts([])
      }
      setAuthChecked(true)
      return
    }

    if (error) {
      console.warn('[OAuth Error]', error)
      if (error === 'no_deriv_accounts') {
        const userId = params.get('user_id')
        if (userId) {
          useBotStore.getState().setUserId(userId)
        }
        useBotStore.getState().setPendingAccounts([])
        console.log('[DEBUG] error=no_deriv_accounts → setPendingAccounts([])')
      } else {
        useBotStore.getState().setAuthError(
          'Authentication failed. Please try again.'
        )
      }
      setAuthChecked(true)
      return
    }

    // ── Already logged in (persisted session) → reconnect ─
    if (isLoggedIn) {
      console.log('[DEBUG] already isLoggedIn → skipping selector, going to dashboard')
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
      console.log('[DEBUG] attempting session restore for userId:', savedUserId)
      fetch(`${API}/auth/check/${savedUserId}`)
        .then(r => r.json())
        .then(data => {
          console.log('[DEBUG] auth/check response:', data)
          if (data.found) {
            const enriched = (data.accounts ?? []).map(a => ({
              ...a,
              user_id: data.user_id,
              email:   data.email,
              name:    data.name,
            }))
            console.log('[DEBUG] session restore → setPendingAccounts:', enriched)
            useBotStore.getState().setPendingAccounts(enriched)
          }
        })
        .catch((e) => { console.warn('[DEBUG] auth/check failed:', e) })
        .finally(() => setAuthChecked(true))
      return
    }

    console.log('[DEBUG] no accounts, no session → showing Login')
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

  // Show selector whenever pendingAccounts is an array (even empty)
  if (Array.isArray(pendingAccounts)) {
    console.log('[DEBUG] rendering AccountSelector with', pendingAccounts.length, 'accounts')
    return (
      <AccountSelector
        accounts={pendingAccounts}
        onSelect={(account) => {
          useBotStore.getState().setUserId(account.user_id)
          setPendingAccounts(null)
        }}
      />
    )
  }

  if (!isLoggedIn) {
    console.log('[DEBUG] rendering Login page')
    return <Login />
  }

  console.log('[DEBUG] rendering Dashboard, activePage:', activePage)
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