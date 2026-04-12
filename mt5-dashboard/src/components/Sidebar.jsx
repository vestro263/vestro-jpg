import { useState, useEffect } from 'react'
import useBotStore from '../store/botStore'

const NAV = [
  {
    key: 'dashboard',
    label: 'Dashboard',
    icon: `<svg width="18" height="18" viewBox="0 0 22 22" fill="none" stroke="currentColor" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round">
      <rect x="2" y="2" width="8" height="8" rx="1.5"/>
      <rect x="12" y="2" width="8" height="8" rx="1.5"/>
      <rect x="2" y="12" width="8" height="8" rx="1.5"/>
      <rect x="12" y="12" width="8" height="8" rx="1.5"/>
    </svg>`,
  },
  {
    key: 'valuations',
    label: 'Valuations',
    icon: `<svg width="18" height="18" viewBox="0 0 22 22" fill="none" stroke="currentColor" stroke-width="1.5" stroke-linecap="round">
      <circle cx="11" cy="11" r="8.5"/>
      <circle cx="11" cy="11" r="3.5"/>
      <line x1="11" y1="2.5" x2="11" y2="6"/>
      <line x1="11" y1="16" x2="11" y2="19.5"/>
      <line x1="2.5" y1="11" x2="6" y2="11"/>
      <line x1="16" y1="11" x2="19.5" y2="11"/>
    </svg>`,
  },
  {
    key: 'signals',
    label: 'Signals',
    icon: `<svg width="18" height="18" viewBox="0 0 22 22" fill="none" stroke="currentColor" stroke-width="1.5" stroke-linejoin="round">
      <path d="M11 3.5C11 3.5 5 8 5 13a6 6 0 0012 0c0-5-6-9.5-6-9.5z"/>
      <line x1="11" y1="13" x2="11" y2="9" stroke-linecap="round"/>
      <circle cx="11" cy="14.5" r="1" fill="currentColor" stroke="none"/>
    </svg>`,
  },
  {
    key: 'positions',
    label: 'Positions',
    icon: `<svg width="18" height="18" viewBox="0 0 22 22" fill="none" stroke="currentColor" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round">
      <polyline points="2,16 7,10 11,13 16,7 20,9"/>
      <line x1="2" y1="19" x2="20" y2="19"/>
    </svg>`,
  },
  {
    key: 'journal',
    label: 'Journal',
    icon: `<svg width="18" height="18" viewBox="0 0 22 22" fill="none" stroke="currentColor" stroke-width="1.5" stroke-linecap="round">
      <rect x="3.5" y="2.5" width="15" height="17" rx="2"/>
      <line x1="7" y1="8" x2="15" y2="8"/>
      <line x1="7" y1="12" x2="15" y2="12"/>
      <line x1="7" y1="16" x2="11" y2="16"/>
    </svg>`,
  },
  {
    key: 'stats',
    label: 'Performance',
    icon: `<svg width="18" height="18" viewBox="0 0 22 22" fill="none" stroke="currentColor" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round">
      <polyline points="2,15 7,9 11,12 16,5 20,7"/>
      <polyline points="16,5 20,5 20,9"/>
      <line x1="2" y1="19" x2="20" y2="19"/>
    </svg>`,
  },
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

// ── Desktop sidebar ─────────────────────────────────────────────────────────
function DesktopSidebar({ activePage, setActivePage, botRunning, startBot, stopBot, account, broker, logout }) {
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
          Vestro Capital
        </div>
        <div style={{ fontSize: 11, color: '#4b5563', marginTop: 2 }}>MT5 Dashboard</div>
      </div>

      {/* ── NEW: account pill ── */}
      <div style={{
        margin: '0 10px 8px',
        padding: '10px 12px',
        background: '#111827',
        borderRadius: 8,
        border: '1px solid #1f2937',
      }}>
        <div style={{ display: 'flex', alignItems: 'center', gap: 6, marginBottom: 4 }}>
          <span style={{
            width: 6, height: 6, borderRadius: '50%', flexShrink: 0,
            background: '#4ade80', boxShadow: '0 0 6px #4ade80',
          }} />
          <span style={{ fontSize: 11, color: '#4ade80', fontWeight: 600 }}>
            {broker?.toUpperCase() ?? 'CONNECTED'}
          </span>
        </div>
        <div style={{ fontSize: 13, color: '#f1f5f9', fontWeight: 600, letterSpacing: '-0.2px' }}>
          {account?.currency} {Number(account?.balance ?? 0).toFixed(2)}
        </div>
        <div style={{ fontSize: 11, color: '#4b5563', marginTop: 1 }}>
          {account?.name ?? '—'}
        </div>
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

      {/* Bottom: bot toggle + logout */}
      <div style={{ marginTop: 'auto', padding: '16px 12px', borderTop: '1px solid #1f2937', display: 'flex', flexDirection: 'column', gap: 8 }}>

        {/* Bot kill switch — unchanged */}
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
            e.currentTarget.style.background  = botRunning ? '#2d0f0f' : '#0d2a13'
            e.currentTarget.style.borderColor = botRunning ? '#dc2626' : '#16a34a'
          }}
          onMouseLeave={e => {
            e.currentTarget.style.background  = botRunning ? '#1c0a0a' : '#0a1c0e'
            e.currentTarget.style.borderColor = botRunning ? '#7f1d1d' : '#14532d'
          }}
        >
          <svg width="13" height="13" viewBox="0 0 24 24" fill="none"
            stroke="currentColor" strokeWidth="2.5" strokeLinecap="round">
            <path d="M18.36 6.64A9 9 0 1 1 5.64 6.64" />
            <line x1="12" y1="2" x2="12" y2="12" />
          </svg>
          {botRunning ? 'Kill Bot' : 'Start Bot'}
        </button>

        {/* Status dot */}
        <div style={{ display: 'flex', alignItems: 'center', gap: 6, paddingLeft: 4, fontSize: 11, color: '#4b5563' }}>
          <span style={{
            width: 6, height: 6, borderRadius: '50%',
            background: botRunning ? '#4ade80' : '#4b5563',
            boxShadow: botRunning ? '0 0 6px #4ade80' : 'none',
            transition: 'all 0.3s',
          }} />
          {botRunning ? 'Bot running' : 'Bot stopped'}
        </div>

        {/* ── NEW: logout button ── */}
        <button
          onClick={logout}
          style={{
            display: 'flex', alignItems: 'center', justifyContent: 'center', gap: 6,
            width: '100%', padding: '8px 12px', borderRadius: 8,
            border: '1px solid #1f2937',
            cursor: 'pointer', fontSize: 12, fontWeight: 500,
            color: '#4b5563', background: 'transparent',
            transition: 'all 0.15s', marginTop: 2,
          }}
          onMouseEnter={e => {
            e.currentTarget.style.background  = '#111827'
            e.currentTarget.style.color       = '#f87171'
            e.currentTarget.style.borderColor = '#7f1d1d'
          }}
          onMouseLeave={e => {
            e.currentTarget.style.background  = 'transparent'
            e.currentTarget.style.color       = '#4b5563'
            e.currentTarget.style.borderColor = '#1f2937'
          }}
        >
          <svg width="12" height="12" viewBox="0 0 24 24" fill="none"
            stroke="currentColor" strokeWidth="2.5" strokeLinecap="round" strokeLinejoin="round">
            <path d="M9 21H5a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2h4"/>
            <polyline points="16 17 21 12 16 7"/>
            <line x1="21" y1="12" x2="9" y2="12"/>
          </svg>
          Disconnect
        </button>

      </div>
    </div>
  )
}

// ── Mobile top bar + bottom tab bar ─────────────────────────────────────────
function MobileNav({ activePage, setActivePage, botRunning, startBot, stopBot, account, broker, logout }) {
  const [showMenu, setShowMenu] = useState(false)
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

        {/* ── NEW: balance chip ── */}
        <div style={{
          fontSize: 12, fontWeight: 600, color: '#f1f5f9',
          background: '#111827', border: '1px solid #1f2937',
          borderRadius: 6, padding: '4px 8px', letterSpacing: '-0.2px',
        }}>
          {account?.currency} {Number(account?.balance ?? 0).toFixed(2)}
        </div>

        {/* Bot toggle */}
        <button
          onClick={botRunning ? stopBot : startBot}
          style={{
            display: 'inline-flex', alignItems: 'center', gap: 5,
            padding: '6px 12px', borderRadius: 7,
            border: `1px solid ${botRunning ? '#7f1d1d' : '#14532d'}`,
            background: botRunning ? '#1c0a0a' : '#0a1c0e',
            color: botRunning ? '#fca5a5' : '#86efac',
            fontSize: 12, fontWeight: 600, cursor: 'pointer', minHeight: 36,
          }}
        >
          <svg width="11" height="11" viewBox="0 0 24 24" fill="none"
            stroke="currentColor" strokeWidth="2.5" strokeLinecap="round">
            <path d="M18.36 6.64A9 9 0 1 1 5.64 6.64" />
            <line x1="12" y1="2" x2="12" y2="12" />
          </svg>
          {botRunning ? 'Stop' : 'Start'}
        </button>

        {/* ── NEW: logout icon button ── */}
        <button
          onClick={logout}
          title="Disconnect"
          style={{
            display: 'inline-flex', alignItems: 'center', justifyContent: 'center',
            width: 34, height: 34, borderRadius: 7, flexShrink: 0,
            border: '1px solid #1f2937', background: 'transparent',
            color: '#4b5563', cursor: 'pointer',
          }}
          onTouchStart={e => e.currentTarget.style.color = '#f87171'}
          onTouchEnd={e => e.currentTarget.style.color = '#4b5563'}
        >
          <svg width="14" height="14" viewBox="0 0 24 24" fill="none"
            stroke="currentColor" strokeWidth="2.5" strokeLinecap="round" strokeLinejoin="round">
            <path d="M9 21H5a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2h4"/>
            <polyline points="16 17 21 12 16 7"/>
            <line x1="21" y1="12" x2="9" y2="12"/>
          </svg>
        </button>

        {/* Status dot */}
        <span style={{
          width: 7, height: 7, borderRadius: '50%', flexShrink: 0,
          background: botRunning ? '#4ade80' : '#4b5563',
          boxShadow: botRunning ? '0 0 6px #4ade80' : 'none',
        }} />
      </div>

      {/* Bottom tab bar — unchanged */}
      <div style={{
        position: 'fixed', bottom: 0, left: 0, right: 0, zIndex: 50,
        background: '#0b1120', borderTop: '1px solid #1f2937',
        display: 'flex',
        paddingBottom: 'env(safe-area-inset-bottom, 0px)',
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
              minHeight: 52, position: 'relative',
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

// ── Export ───────────────────────────────────────────────────────────────────
export default function Sidebar() {
  const {
    activePage, setActivePage,
    botRunning, startBot, stopBot,
    account, broker, logout,          // ← add these three
  } = useBotStore()

  const isMobile = useIsMobile()

  if (isMobile) {
    return (
      <MobileNav
        activePage={activePage}
        setActivePage={setActivePage}
        botRunning={botRunning}
        startBot={startBot}
        stopBot={stopBot}
        account={account}             // ← pass down
        broker={broker}
        logout={logout}
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
      account={account}               // ← pass down
      broker={broker}
      logout={logout}
    />
  )
}