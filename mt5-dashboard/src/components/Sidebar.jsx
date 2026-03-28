import { useState, useEffect } from 'react'
import useBotStore from '../store/botStore'

const NAV = [
  { key: 'dashboard', icon: '📊', label: 'Dashboard'   },
  { key: 'valuations',icon: '🎯', label: 'Valuations'  },
  { key: 'signals',   icon: '📡', label: 'Signals'     },
  { key: 'positions', icon: '📉', label: 'Positions'   },
  { key: 'journal',   icon: '📓', label: 'Journal'     },
  { key: 'stats',     icon: '📈', label: 'Performance' },
]

function useIsMobile(bp = 768) {
  const [m, setM] = useState(() => window.innerWidth < bp)
  useEffect(() => {
    const h = () => setM(window.innerWidth < bp)
    window.addEventListener('resize', h)
    return () => window.removeEventListener('resize', h)
  }, [bp])
  return m
}

// ── Desktop sidebar ────────────────────────────────────────────────────────────
function DesktopSidebar({ activePage, setActivePage, botRunning, startBot, stopBot }) {
  return (
    <div style={{
      width: 200, minHeight: '100vh',
      background: '#0b1120', borderRight: '1px solid #1f2937',
      display: 'flex', flexDirection: 'column',
      padding: '20px 0', gap: 4, flexShrink: 0,
    }}>
      {/* Logo */}
      <div style={{ padding: '0 20px 20px', borderBottom: '1px solid #1f2937', marginBottom: 8 }}>
        <div style={{ fontSize: 18, fontWeight: 700, color: '#38bdf8', letterSpacing: '0.05em' }}>
          ⚡ Vestro
        </div>
        <div style={{ fontSize: 11, color: '#4b5563', marginTop: 2 }}>MT5 Dashboard</div>
      </div>

      {/* Nav */}
      {NAV.map(({ key, icon, label }) => {
        const active = activePage === key
        return (
          <button key={key} onClick={() => setActivePage(key)} style={{
            display: 'flex', alignItems: 'center', gap: 10,
            margin: '0 10px', padding: '10px 12px',
            borderRadius: 8, border: 'none', cursor: 'pointer',
            fontSize: 13, fontWeight: active ? 600 : 400,
            color: active ? '#f1f5f9' : '#6b7280',
            background: active ? '#1f2937' : 'transparent',
            transition: 'all 0.15s', textAlign: 'left',
          }}
            onMouseEnter={e => { if (!active) e.currentTarget.style.background = '#111827' }}
            onMouseLeave={e => { if (!active) e.currentTarget.style.background = 'transparent' }}
          >
            <span style={{ fontSize: 15 }}>{icon}</span>
            {label}
            {active && (
              <span style={{ marginLeft: 'auto', width: 4, height: 4, borderRadius: '50%', background: '#38bdf8' }} />
            )}
          </button>
        )
      })}

      {/* Kill switch */}
      <div style={{ marginTop: 'auto', padding: '16px 12px', borderTop: '1px solid #1f2937', display: 'flex', flexDirection: 'column', gap: 10 }}>
        <button
          onClick={botRunning ? stopBot : startBot}
          style={{
            display: 'flex', alignItems: 'center', justifyContent: 'center', gap: 7,
            width: '100%', padding: '9px 12px', borderRadius: 8,
            border: `1px solid ${botRunning ? '#7f1d1d' : '#14532d'}`,
            cursor: 'pointer', fontSize: 12, fontWeight: 600,
            color: botRunning ? '#fca5a5' : '#86efac',
            background: botRunning ? '#1c0a0a' : '#0a1c0e',
            transition: 'all 0.15s',
          }}
          onMouseEnter={e => {
            e.currentTarget.style.background   = botRunning ? '#2d0f0f' : '#0d2a13'
            e.currentTarget.style.borderColor  = botRunning ? '#dc2626' : '#16a34a'
          }}
          onMouseLeave={e => {
            e.currentTarget.style.background   = botRunning ? '#1c0a0a' : '#0a1c0e'
            e.currentTarget.style.borderColor  = botRunning ? '#7f1d1d' : '#14532d'
          }}
        >
          <svg width="13" height="13" viewBox="0 0 24 24" fill="none"
            stroke="currentColor" strokeWidth="2.5" strokeLinecap="round">
            <path d="M18.36 6.64A9 9 0 1 1 5.64 6.64" />
            <line x1="12" y1="2" x2="12" y2="12" />
          </svg>
          {botRunning ? 'Kill Bot' : 'Start Bot'}
        </button>

        <div style={{ display: 'flex', alignItems: 'center', gap: 6, paddingLeft: 4, fontSize: 11, color: '#4b5563' }}>
          <span style={{
            width: 6, height: 6, borderRadius: '50%',
            background: botRunning ? '#4ade80' : '#4b5563',
            boxShadow: botRunning ? '0 0 6px #4ade80' : 'none',
            transition: 'all 0.3s',
          }} />
          {botRunning ? 'Bot running' : 'Bot stopped'}
        </div>
      </div>
    </div>
  )
}

// ── Mobile top bar + bottom tab bar ───────────────────────────────────────────
function MobileNav({ activePage, setActivePage, botRunning, startBot, stopBot }) {
  // Show 5 tabs; rest hidden (could add a "More" tab later)
  const tabs = NAV.slice(0, 5)

  return (
    <>
      {/* Top bar */}
      <div style={{
        position: 'sticky', top: 0, zIndex: 50,
        background: '#0b1120', borderBottom: '1px solid #1f2937',
        display: 'flex', alignItems: 'center',
        padding: '10px 14px', gap: 10,
      }}>
        <div style={{ fontSize: 16, fontWeight: 700, color: '#38bdf8', letterSpacing: '0.04em', flex: 1 }}>
          ⚡ Vestro
        </div>

        {/* Inline bot toggle */}
        <button
          onClick={botRunning ? stopBot : startBot}
          style={{
            display: 'inline-flex', alignItems: 'center', gap: 5,
            padding: '6px 12px', borderRadius: 7,
            border: `1px solid ${botRunning ? '#7f1d1d' : '#14532d'}`,
            background: botRunning ? '#1c0a0a' : '#0a1c0e',
            color: botRunning ? '#fca5a5' : '#86efac',
            fontSize: 12, fontWeight: 600, cursor: 'pointer',
            minHeight: 36,
          }}
        >
          <svg width="11" height="11" viewBox="0 0 24 24" fill="none"
            stroke="currentColor" strokeWidth="2.5" strokeLinecap="round">
            <path d="M18.36 6.64A9 9 0 1 1 5.64 6.64" />
            <line x1="12" y1="2" x2="12" y2="12" />
          </svg>
          {botRunning ? 'Stop' : 'Start'}
        </button>

        {/* Status dot */}
        <span style={{
          width: 7, height: 7, borderRadius: '50%', flexShrink: 0,
          background: botRunning ? '#4ade80' : '#4b5563',
          boxShadow: botRunning ? '0 0 6px #4ade80' : 'none',
        }} />
      </div>

      {/* Bottom tab bar */}
      <div style={{
        position: 'fixed', bottom: 0, left: 0, right: 0, zIndex: 50,
        background: '#0b1120', borderTop: '1px solid #1f2937',
        display: 'flex',
        paddingBottom: 'env(safe-area-inset-bottom, 0px)', // iPhone safe area
      }}>
        {tabs.map(({ key, icon, label }) => {
          const active = activePage === key
          return (
            <button key={key} onClick={() => setActivePage(key)} style={{
              flex: 1, display: 'flex', flexDirection: 'column',
              alignItems: 'center', justifyContent: 'center',
              gap: 2, padding: '8px 4px', border: 'none',
              background: 'transparent', cursor: 'pointer',
              color: active ? '#38bdf8' : '#4b5563',
              fontSize: 9, fontWeight: active ? 600 : 400,
              transition: 'color 0.15s',
              WebkitTapHighlightColor: 'transparent',
              minHeight: 52,
            }}>
              <span style={{ fontSize: 18, lineHeight: 1 }}>{icon}</span>
              {label}
              {active && (
                <span style={{ position: 'absolute', top: 0, width: 24, height: 2, background: '#38bdf8', borderRadius: 1 }} />
              )}
            </button>
          )
        })}
      </div>
    </>
  )
}

// ── Export ─────────────────────────────────────────────────────────────────────
export default function Sidebar() {
  const { activePage, setActivePage, botRunning, startBot, stopBot } = useBotStore()
  const isMobile = useIsMobile()

  if (isMobile) {
    return (
      <MobileNav
        activePage={activePage}
        setActivePage={setActivePage}
        botRunning={botRunning}
        startBot={startBot}
        stopBot={stopBot}
      />
    )
  }

  return (
    <DesktopSidebar
      activePage={activePage}
      setActivePage={setActivePage}
      botRunning={botRunning}
      startBot={startBot}
      stopBot={stopBot}
    />
  )
}