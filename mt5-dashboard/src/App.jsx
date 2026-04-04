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

  // Handle Deriv OAuth callback — runs once on mount
  useEffect(() => {
    const params = new URLSearchParams(window.location.search)
    const account_id = params.get('account_id')
    const balance    = parseFloat(params.get('balance') ?? '0')
    const currency   = params.get('currency') ?? 'USD'
    const error      = params.get('error')

    if (error) {
      useBotStore.getState().setAuthError('Deriv login failed. Please try again.')
      window.history.replaceState({}, '', '/')
    } else if (account_id) {
      login('deriv', account_id, {
        account_id,
        balance,
        currency,
        equity: balance,
        profit: 0,
      })
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

  // Wait until OAuth params are processed before deciding to show Login
  if (!authChecked) return null

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