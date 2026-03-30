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
  const { activePage, isLoggedIn } = useBotStore()
  const isMobile = useIsMobile()
  const Page = PAGES[activePage] ?? Dashboard

  if (!isLoggedIn) return <Login />
      useEffect(() => {
      if (isLoggedIn && !useBotStore.getState().connected) {
        useBotStore.getState().connect()
        useBotStore.getState().startPolling()
      }
    }, [isLoggedIn])

  if (isMobile) {
    return (
      // Full-screen column: top bar | scrollable content | bottom tabs
      <div style={{
        display: 'flex',
        flexDirection: 'column',
        minHeight: '100dvh',          // dvh respects mobile browser chrome
        background: '#030712',
      }}>
        {/* Sidebar renders ONLY the top bar + bottom tab bar on mobile */}
        <Sidebar />

        {/* Scrollable page content — padded so it clears the bottom tab bar */}
        <main style={{
          flex: 1,
          overflowY: 'auto',
          WebkitOverflowScrolling: 'touch',
          // 52px tab bar + safe-area bottom inset
          paddingBottom: 'calc(52px + env(safe-area-inset-bottom, 0px))',
        }}>
          <Page />
        </main>
      </div>
    )
  }

  // Desktop: side-by-side
  return (
    <div style={{ display: 'flex', minHeight: '100vh', background: '#030712' }}>
      <Sidebar />
      <main style={{ flex: 1, overflowY: 'auto' }}>
        <Page />
      </main>
    </div>
  )
}

