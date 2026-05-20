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

// ─────────────────────────────────────────────────────────────────────────────
// Read OAuth params SYNCHRONOUSLY at module load time — before any React state
// or Zustand persist hydration runs. This guarantees the URL is read before
// isLoggedIn from localStorage can interfere.
// ─────────────────────────────────────────────────────────────────────────────
const _initialParams = new URLSearchParams(window.location.search)
const _accountsParam = _initialParams.get('accounts')   // null if not present
const _errorParam    = _initialParams.get('error')      // null if not present
const _urlUserId     = _initialParams.get('user_id') || ''
const _hasOAuthReturn = _accountsParam !== null || _errorParam !== null

// Clear URL immediately so refreshing doesn't re-trigger
if (_hasOAuthReturn) {
  window.history.replaceState({}, '', '/')
}

export default function App() {
  const { isLoggedIn, activePage, pendingAccounts, setPendingAccounts } = useBotStore()
  const isMobile = useIsMobile()
  const [authChecked, setAuthChecked] = useState(false)

  useEffect(() => {
    // ── Case 1: OAuth redirect returned ──────────────────────────────────────
    if (_hasOAuthReturn) {
      if (_accountsParam !== null) {
        let accounts = []
        try {
          const parsed = JSON.parse(decodeURIComponent(_accountsParam))
          if (Array.isArray(parsed)) accounts = parsed
        } catch (e) {
          console.warn('[App] Failed to parse accounts param:', e)
        }

        const resolvedUserId = _urlUserId || accounts[0]?.user_id || ''
        if (resolvedUserId) {
          useBotStore.getState().setUserId(resolvedUserId)
        }

        const enriched = accounts.map(a => ({
          ...a,
          user_id: a.user_id || resolvedUserId,
        }))

        useBotStore.getState().setPendingAccounts(enriched)
      } else {
        // error param — show empty selector
        if (_urlUserId) useBotStore.getState().setUserId(_urlUserId)
        useBotStore.getState().setPendingAccounts([])
      }

      setAuthChecked(true)
      return
    }

    // ── Case 2: Already logged in (persisted session) → reconnect ────────────
    if (isLoggedIn) {
      const state = useBotStore.getState()
      if (!state.connected) {
        state.connect()
        state.startPolling()
      }
      setAuthChecked(true)
      return
    }

    // ── Case 3: Try session restore via saved userId ──────────────────────────
    const savedUserId = useBotStore.getState().userId
    if (savedUserId) {
      fetch(`${API}/auth/check/${savedUserId}`)
        .then(r => r.json())
        .then(data => {
          if (data.found) {
            const enriched = (data.accounts ?? []).map(a => ({
              ...a,
              user_id: data.user_id,
              email:   data.email,
              name:    data.name,
            }))
            useBotStore.getState().setPendingAccounts(enriched)
          }
        })
        .catch(e => console.warn('[App] auth/check failed:', e))
        .finally(() => setAuthChecked(true))
      return
    }

    // ── Case 4: No session, no redirect → show Login ──────────────────────────
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

  // Show selector whenever pendingAccounts is an array (even empty = zero accounts)
  if (Array.isArray(pendingAccounts)) {
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