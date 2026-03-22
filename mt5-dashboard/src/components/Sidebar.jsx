import useBotStore from '../store/botStore'

const NAV = [
  { key: 'dashboard', icon: '📊', label: 'Dashboard' },
  { key: 'signals',   icon: '📡', label: 'Signals'   },
  { key: 'positions', icon: '📉', label: 'Positions'  },
  { key: 'journal',   icon: '📓', label: 'Journal'    },
  { key: 'stats',     icon: '📈', label: 'Performance'},
]

export default function Sidebar() {
  const { activePage, setActivePage } = useBotStore()

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

      {/* Bottom status */}
      <div style={{
        marginTop: 'auto',
        padding: '16px 20px',
        borderTop: '1px solid #1f2937',
        fontSize: 11,
        color: '#4b5563',
      }}>
        <div style={{ display: 'flex', alignItems: 'center', gap: 6 }}>
          <span style={{
            width: 6, height: 6,
            borderRadius: '50%',
            background: '#4ade80',
            boxShadow: '0 0 6px #4ade80',
          }} />
          Bot running
        </div>
      </div>

    </div>
  )
}