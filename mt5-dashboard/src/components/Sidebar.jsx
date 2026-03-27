import useBotStore from '../store/botStore'

const NAV = [
  { key: 'dashboard', icon: '📊', label: 'Dashboard' },
{ key: 'valuations', icon: '🎯', label: 'Valuations' },

  { key: 'signals',   icon: '📡', label: 'Signals'   },
  { key: 'positions', icon: '📉', label: 'Positions'  },
  { key: 'journal',   icon: '📓', label: 'Journal'    },
  { key: 'stats',     icon: '📈', label: 'Performance'},
]

export default function Sidebar() {
  const { activePage, setActivePage, botRunning, startBot, stopBot } = useBotStore()

  return (
    <div style={{
      width: 200,
      minHeight: '100vh',
      background: '#0b1120',
      borderRight: '1px solid #1f2937',
      display: 'flex',
      flexDirection: 'column',
      padding: '20px 0',
      gap: 4,
    }}>

      {/* Logo */}
      <div style={{
        padding: '0 20px 20px',
        borderBottom: '1px solid #1f2937',
        marginBottom: 8,
      }}>
        <div style={{ fontSize: 18, fontWeight: 700, color: '#38bdf8', letterSpacing: '0.05em' }}>
          ⚡ Vestro
        </div>
        <div style={{ fontSize: 11, color: '#4b5563', marginTop: 2 }}>
          MT5 Dashboard
        </div>
      </div>

      {/* Nav items */}
      {NAV.map(({ key, icon, label }) => {
        const active = activePage === key
        return (
          <button
            key={key}
            onClick={() => setActivePage(key)}
            style={{
              display: 'flex',
              alignItems: 'center',
              gap: 10,
              margin: '0 10px',
              padding: '10px 12px',
              borderRadius: 8,
              border: 'none',
              cursor: 'pointer',
              fontSize: 13,
              fontWeight: active ? 600 : 400,
              color: active ? '#f1f5f9' : '#6b7280',
              background: active ? '#1f2937' : 'transparent',
              transition: 'all 0.15s ease',
              textAlign: 'left',
            }}
            onMouseEnter={e => {
              if (!active) e.currentTarget.style.background = '#111827'
            }}
            onMouseLeave={e => {
              if (!active) e.currentTarget.style.background = 'transparent'
            }}
          >
            <span style={{ fontSize: 15 }}>{icon}</span>
            {label}
            {active && (
              <span style={{
                marginLeft: 'auto',
                width: 4, height: 4,
                borderRadius: '50%',
                background: '#38bdf8',
              }} />
            )}
          </button>
        )
      })}

      {/* Bottom section */}
      <div style={{
        marginTop: 'auto',
        padding: '16px 12px',
        borderTop: '1px solid #1f2937',
        display: 'flex',
        flexDirection: 'column',
        gap: 10,
      }}>

        {/* Outlet (kill switch) button */}
        <button
          onClick={botRunning ? stopBot : startBot}
          style={{
            display: 'flex',
            alignItems: 'center',
            justifyContent: 'center',
            gap: 7,
            width: '100%',
            padding: '9px 12px',
            borderRadius: 8,
            border: `1px solid ${botRunning ? '#7f1d1d' : '#14532d'}`,
            cursor: 'pointer',
            fontSize: 12,
            fontWeight: 600,
            color: botRunning ? '#fca5a5' : '#86efac',
            background: botRunning ? '#1c0a0a' : '#0a1c0e',
            transition: 'all 0.15s ease',
          }}
          onMouseEnter={e => {
            e.currentTarget.style.background = botRunning ? '#2d0f0f' : '#0d2a13'
            e.currentTarget.style.borderColor = botRunning ? '#dc2626' : '#16a34a'
          }}
          onMouseLeave={e => {
            e.currentTarget.style.background = botRunning ? '#1c0a0a' : '#0a1c0e'
            e.currentTarget.style.borderColor = botRunning ? '#7f1d1d' : '#14532d'
          }}
        >
          {/* Power icon */}
          <svg width="13" height="13" viewBox="0 0 24 24" fill="none"
            stroke="currentColor" strokeWidth="2.5" strokeLinecap="round">
            <path d="M18.36 6.64A9 9 0 1 1 5.64 6.64" />
            <line x1="12" y1="2" x2="12" y2="12" />
          </svg>
          {botRunning ? 'Kill Bot' : 'Start Bot'}
        </button>

        {/* Status dot */}
        <div style={{ display: 'flex', alignItems: 'center', gap: 6,
                      paddingLeft: 4, fontSize: 11, color: '#4b5563' }}>
          <span style={{
            width: 6, height: 6,
            borderRadius: '50%',
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