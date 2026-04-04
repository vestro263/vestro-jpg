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
  const { accountId, activePage, login } = useBotStore()
  const isLoggedIn = !!accountId
  const isMobile = useIsMobile()
  const [authChecked, setAuthChecked] = useState(false)
  const [pendingAccounts, setPendingAccounts] = useState(null)

  useEffect(() => {
    const params = new URLSearchParams(window.location.search)
    const accountsParam = params.get('accounts')
    const error = params.get('error')

    if (error) {
      useBotStore.getState().setAuthError('Deriv login failed. Please try again.')
      window.history.replaceState({}, '', '/')
    } else if (accountsParam) {
      try {
        const accounts = JSON.parse(decodeURIComponent(accountsParam))
        if (accounts.length === 1) {
          // Only one account — log in directly
          const acc = accounts[0]
          login('deriv', acc.account_id, {
            account_id: acc.account_id,
            balance: acc.balance,
            currency: acc.currency,
            equity: acc.balance,
            profit: 0,
          })
        } else {
          // Multiple accounts — show selector
          setPendingAccounts(accounts)
        }
      } catch {}
      window.history.replaceState({}, '', '/')
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

  // Show account selector if multiple accounts
  if (pendingAccounts) {
    return <AccountSelector accounts={pendingAccounts} />
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