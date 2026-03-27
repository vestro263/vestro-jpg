import { useEffect } from 'react'
import useBotStore from './store/botStore'
import Sidebar     from './components/Sidebar'
import AccountBar  from './components/AccountBar'
import Dashboard   from './pages/Dashboard'
import Signals     from './pages/Signals'
import Positions   from './pages/Positions'
import Journal     from './pages/Journal'
import Performance from './pages/Performance'
import Valuation   from './pages/Valuation'

const PAGES = {
  dashboard:  Dashboard,
  valuations: Valuation,
  signals:    Signals,
  positions:  Positions,
  journal:    Journal,
  stats:      Performance,
}

export default function App() {
  const { activePage, connect, startPolling, wsError } = useBotStore()
  const Page = PAGES[activePage] || Dashboard

  useEffect(() => {
    connect()
    startPolling()
  }, [])

  return (
    <div style={{ display: 'flex', minHeight: '100vh', background: '#030712' }}>
      <Sidebar />
      <div style={{ flex: 1, display: 'flex', flexDirection: 'column', minWidth: 0, overflow: 'hidden' }}>
        <AccountBar />
        {wsError && (
          <div style={{
            background: '#1c0a0a', borderBottom: '1px solid #3b0000',
            padding: '8px 24px', fontSize: 12, color: '#f87171',
            display: 'flex', alignItems: 'center', gap: 8,
          }}>
            <span style={{ fontWeight: 600 }}>Connection error:</span> {wsError}
            <span style={{ marginLeft: 8, color: '#6b7280' }}>
              Retrying automatically…
            </span>
          </div>
        )}
        <main style={{ flex: 1, overflowY: 'auto' }}>
          <Page />
        </main>
      </div>
    </div>
  )
}