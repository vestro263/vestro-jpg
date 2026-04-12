import { useState, useEffect } from 'react'
import useBotStore from '../store/botStore'

// ── Icons ────────────────────────────────────────────────────────────────────

const I = (props) => (
  <svg width="18" height="18" viewBox="0 0 22 22" fill="none"
    stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round"
    {...props}
  />
)

const Icons = {
  Dashboard: () => (
    <I>
      <rect x="2"  y="2"  width="8" height="8" rx="1.5" />
      <rect x="12" y="2"  width="8" height="8" rx="1.5" />
      <rect x="2"  y="12" width="8" height="8" rx="1.5" />
      <rect x="12" y="12" width="8" height="8" rx="1.5" />
    </I>
  ),
  Valuations: () => (
    <I>
      <circle cx="11" cy="11" r="8.5" />
      <circle cx="11" cy="11" r="3.5" />
      <line x1="11" y1="2.5" x2="11" y2="6"    />
      <line x1="11" y1="16"  x2="11" y2="19.5" />
      <line x1="2.5" y1="11" x2="6"   y2="11"  />
      <line x1="16"  y1="11" x2="19.5" y2="11" />
    </I>
  ),
  Signals: () => (
    <I>
      <path d="M11 3.5C11 3.5 5 8 5 13a6 6 0 0012 0c0-5-6-9.5-6-9.5z" />
      <line x1="11" y1="13" x2="11" y2="9" />
      <circle cx="11" cy="14.5" r="1" fill="currentColor" stroke="none" />
    </I>
  ),
  Positions: () => (
    <I>
      <polyline points="2,16 7,10 11,13 16,7 20,9" />
      <line x1="2" y1="19" x2="20" y2="19" />
    </I>
  ),
  Journal: () => (
    <I>
      <rect x="3.5" y="2.5" width="15" height="17" rx="2" />
      <line x1="7" y1="8"  x2="15" y2="8"  />
      <line x1="7" y1="12" x2="15" y2="12" />
      <line x1="7" y1="16" x2="11" y2="16" />
    </I>
  ),
  Performance: () => (
    <I>
      <polyline points="2,15 7,9 11,12 16,5 20,7" />
      <polyline points="16,5 20,5 20,9" />
      <line x1="2" y1="19" x2="20" y2="19" />
    </I>
  ),
  Power: () => (
    <svg width="13" height="13" viewBox="0 0 24 24" fill="none"
      stroke="currentColor" strokeWidth="2.5" strokeLinecap="round">
      <path d="M18.36 6.64A9 9 0 1 1 5.64 6.64" />
      <line x1="12" y1="2" x2="12" y2="12" />
    </svg>
  ),
  Logout: () => (
    <svg width="13" height="13" viewBox="0 0 24 24" fill="none"
      stroke="currentColor" strokeWidth="2.5" strokeLinecap="round" strokeLinejoin="round">
      <path d="M9 21H5a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2h4" />
      <polyline points="16 17 21 12 16 7" />
      <line x1="21" y1="12" x2="9" y2="12" />
    </svg>
  ),
}

// ── Nav config ────────────────────────────────────────────────────────────────

const NAV = [
  { key: 'dashboard',  label: 'Dashboard',   Icon: Icons.Dashboard   },
  { key: 'valuations', label: 'Valuations',  Icon: Icons.Valuations  },
  { key: 'signals',    label: 'Signals',     Icon: Icons.Signals     },
  { key: 'positions',  label: 'Positions',   Icon: Icons.Positions   },
  { key: 'journal',    label: 'Journal',     Icon: Icons.Journal     },
  { key: 'stats',      label: 'Performance', Icon: Icons.Performance },
]

// ── Styles ───────────────────────────────────────────────────────────────────

const css = {
  // sidebar shell
  sidebar: {
    width: 200, minHeight: '100vh', flexShrink: 0,
    background: '#0b1120', borderRight: '1px solid #1f2937',
    display: 'flex', flexDirection: 'column',
    padding: '20px 0', gap: 4,
  },
  // logo block
  logoWrap: { padding: '0 20px 20px', borderBottom: '1px solid #1f2937', marginBottom: 8 },
  logoText: { fontSize: 18, fontWeight: 700, color: '#38bdf8', letterSpacing: '0.05em' },
  logoSub:  { fontSize: 11, color: '#4b5563', marginTop: 2 },
  // account pill
  pill: {
    margin: '0 10px 8px', padding: '10px 12px',
    background: '#111827', borderRadius: 8, border: '1px solid #1f2937',
  },
  pillHeader:  { display: 'flex', alignItems: 'center', gap: 6, marginBottom: 4 },
  pillDot:     { width: 6, height: 6, borderRadius: '50%', background: '#4ade80', boxShadow: '0 0 6px #4ade80' },
  pillBroker:  { fontSize: 11, color: '#4ade80', fontWeight: 600 },
  pillBalance: { fontSize: 13, color: '#f1f5f9', fontWeight: 600, letterSpacing: '-0.2px' },
  pillName:    { fontSize: 11, color: '#4b5563', marginTop: 1 },
  // nav item base
  navItem: (active) => ({
    display: 'flex', alignItems: 'center', gap: 10,
    margin: '0 10px', padding: '10px 12px', width: 'calc(100% - 20px)',
    borderRadius: 8, border: 'none', cursor: 'pointer', textAlign: 'left',
    fontSize: 13, fontWeight: active ? 600 : 400,
    color: active ? '#f1f5f9' : '#6b7280',
    background: active ? '#1f2937' : 'transparent',
    transition: 'all 0.15s',
  }),
  activeDot: {
    marginLeft: 'auto', width: 4, height: 4,
    borderRadius: '50%', background: '#38bdf8',
  },
  // footer
  footer: { marginTop: 'auto', padding: '16px 12px', borderTop: '1px solid #1f2937', display: 'flex', flexDirection: 'column', gap: 8 },
  botBtn: (running) => ({
    display: 'flex', alignItems: 'center', justifyContent: 'center', gap: 7,
    width: '100%', padding: '9px 12px', borderRadius: 8,
    border: `1px solid ${running ? '#7f1d1d' : '#14532d'}`,
    cursor: 'pointer', fontSize: 12, fontWeight: 600,
    color: running ? '#fca5a5' : '#86efac',
    background: running ? '#1c0a0a' : '#0a1c0e',
    transition: 'all 0.15s',
  }),
  statusRow: { display: 'flex', alignItems: 'center', gap: 6, paddingLeft: 4, fontSize: 11, color: '#4b5563' },
  statusDot: (running) => ({
    width: 6, height: 6, borderRadius: '50%', transition: 'all 0.3s',
    background: running ? '#4ade80' : '#4b5563',
    boxShadow: running ? '0 0 6px #4ade80' : 'none',
  }),
  logoutBtn: {
    display: 'flex', alignItems: 'center', justifyContent: 'center', gap: 6,
    width: '100%', padding: '8px 12px', borderRadius: 8,
    border: '1px solid #1f2937', cursor: 'pointer',
    fontSize: 12, fontWeight: 500, color: '#4b5563', background: 'transparent',
    transition: 'all 0.15s',
  },
  // mobile
  topBar: {
    position: 'sticky', top: 0, zIndex: 50,
    background: '#0b1120', borderBottom: '1px solid #1f2937',
    display: 'flex', alignItems: 'center', padding: '10px 14px', gap: 10,
  },
  balanceChip: {
    fontSize: 12, fontWeight: 600, color: '#f1f5f9',
    background: '#111827', border: '1px solid #1f2937',
    borderRadius: 6, padding: '4px 8px', letterSpacing: '-0.2px',
  },
  mobileBot: (running) => ({
    display: 'inline-flex', alignItems: 'center', gap: 5,
    padding: '6px 12px', borderRadius: 7, minHeight: 36,
    border: `1px solid ${running ? '#7f1d1d' : '#14532d'}`,
    background: running ? '#1c0a0a' : '#0a1c0e',
    color: running ? '#fca5a5' : '#86efac',
    fontSize: 12, fontWeight: 600, cursor: 'pointer',
  }),
  mobileLogout: {
    display: 'inline-flex', alignItems: 'center', justifyContent: 'center',
    width: 34, height: 34, borderRadius: 7, flexShrink: 0,
    border: '1px solid #1f2937', background: 'transparent',
    color: '#4b5563', cursor: 'pointer',
  },
  bottomBar: {
    position: 'fixed', bottom: 0, left: 0, right: 0, zIndex: 50,
    background: '#0b1120', borderTop: '1px solid #1f2937',
    display: 'flex',
    paddingBottom: 'env(safe-area-inset-bottom, 0px)',
  },
  tabBtn: (active) => ({
    flex: 1, display: 'flex', flexDirection: 'column',
    alignItems: 'center', justifyContent: 'center',
    gap: 2, padding: '8px 4px', border: 'none',
    background: 'transparent', cursor: 'pointer',
    color: active ? '#38bdf8' : '#4b5563',
    fontSize: 9, fontWeight: active ? 600 : 400,
    transition: 'color 0.15s', WebkitTapHighlightColor: 'transparent',
    minHeight: 52, position: 'relative',
  }),
  tabIndicator: {
    position: 'absolute', top: 0, width: 24, height: 2,
    background: '#38bdf8', borderRadius: 1,
  },
}

// ── Hooks ─────────────────────────────────────────────────────────────────────

function useIsMobile(bp = 768) {
  const [m, setM] = useState(() => window.innerWidth < bp)
  useEffect(() => {
    const h = () => setM(window.innerWidth < bp)
    window.addEventListener('resize', h)
    return () => window.removeEventListener('resize', h)
  }, [bp])
  return m
}

// ── Shared sub-components ─────────────────────────────────────────────────────

function BotButton({ running, onStart, onStop, style, hoverStyles }) {
  const [hovered, setHovered] = useState(false)
  return (
    <button
      onClick={running ? onStop : onStart}
      onMouseEnter={() => setHovered(true)}
      onMouseLeave={() => setHovered(false)}
      style={{
        ...style(running),
        ...(hovered ? hoverStyles(running) : {}),
      }}
    >
      <Icons.Power />
      {running ? 'Kill Bot' : 'Start Bot'}
    </button>
  )
}

// ── Desktop sidebar ───────────────────────────────────────────────────────────

function DesktopSidebar() {
  const { activePage, setActivePage, botRunning, startBot, stopBot, account, broker, logout } = useBotStore()
  const [hoveredNav, setHoveredNav] = useState(null)
  const [logoutHovered, setLogoutHovered] = useState(false)

  return (
    <div style={css.sidebar}>

      <div style={css.logoWrap}>
        <div style={css.logoText}>Vestro Capital</div>

      </div>

      <div style={css.pill}>
        <div style={css.pillHeader}>
          <span style={css.pillDot} />
          <span style={css.pillBroker}>{broker?.toUpperCase() ?? 'CONNECTED'}</span>
        </div>
        <div style={css.pillBalance}>
          {account?.currency} {Number(account?.balance ?? 0).toFixed(2)}
        </div>
        <div style={css.pillName}>{account?.name ?? '—'}</div>
      </div>

      {NAV.map(({ key, label, Icon }) => {
        const active = activePage === key
        const hovered = hoveredNav === key
        return (
          <button
            key={key}
            onClick={() => setActivePage(key)}
            onMouseEnter={() => setHoveredNav(key)}
            onMouseLeave={() => setHoveredNav(null)}
            style={{
              ...css.navItem(active),
              ...(hovered && !active ? { background: '#111827' } : {}),
            }}
          >
            <Icon />
            {label}
            {active && <span style={css.activeDot} />}
          </button>
        )
      })}

      <div style={css.footer}>
        <BotButton
          running={botRunning}
          onStart={startBot}
          onStop={stopBot}
          style={css.botBtn}
          hoverStyles={(r) => ({
            background:   r ? '#2d0f0f' : '#0d2a13',
            borderColor:  r ? '#dc2626' : '#16a34a',
          })}
        />

        <div style={css.statusRow}>
          <span style={css.statusDot(botRunning)} />
          {botRunning ? 'Bot running' : 'Bot stopped'}
        </div>

        <button
          onClick={logout}
          onMouseEnter={() => setLogoutHovered(true)}
          onMouseLeave={() => setLogoutHovered(false)}
          style={{
            ...css.logoutBtn,
            ...(logoutHovered ? { background: '#111827', color: '#f87171', borderColor: '#7f1d1d' } : {}),
          }}
        >
          <Icons.Logout />
          Disconnect
        </button>
      </div>
    </div>
  )
}

// ── Mobile nav ────────────────────────────────────────────────────────────────

function MobileNav() {
  const { activePage, setActivePage, botRunning, startBot, stopBot, account, logout } = useBotStore()
  const tabs = NAV.slice(0, 5)

  return (
    <>
      <div style={css.topBar}>
        <div style={{ fontSize: 16, fontWeight: 700, color: '#38bdf8', letterSpacing: '0.04em', flex: 1 }}>
          Vestro
        </div>

        <div style={css.balanceChip}>
          {account?.currency} {Number(account?.balance ?? 0).toFixed(2)}
        </div>

        <button
          onClick={botRunning ? stopBot : startBot}
          style={css.mobileBot(botRunning)}
        >
          <Icons.Power />
          {botRunning ? 'Stop' : 'Start'}
        </button>

        <button onClick={logout} title="Disconnect" style={css.mobileLogout}>
          <Icons.Logout />
        </button>

        <span style={css.statusDot(botRunning)} />
      </div>

      <div style={css.bottomBar}>
        {tabs.map(({ key, label, Icon }) => {
          const active = activePage === key
          return (
            <button key={key} onClick={() => setActivePage(key)} style={css.tabBtn(active)}>
              <Icon />
              {label}
              {active && <span style={css.tabIndicator} />}
            </button>
          )
        })}
      </div>
    </>
  )
}

// ── Export ────────────────────────────────────────────────────────────────────

export default function Sidebar() {
  const isMobile = useIsMobile()
  return isMobile ? <MobileNav /> : <DesktopSidebar />
}