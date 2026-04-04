// src/pages/AccountSelector.jsx
import useBotStore from '../store/botStore'

export default function AccountSelector({ accounts }) {
  const { login } = useBotStore()

  function handleSelect(acc) {
    login('deriv', acc.account_id, {
      account_id: acc.account_id,
      balance:    acc.balance,
      currency:   acc.currency,
      equity:     acc.balance,
      profit:     0,
    })
  }

  return (
    <div style={styles.outer}>
      <div style={styles.card}>
        <div style={styles.brand}>
          <span style={styles.brandDot} />
          <span style={styles.brandName}>Vestro</span>
        </div>
        <p style={styles.sub}>Select an account to trade with</p>

        <div style={styles.list}>
          {accounts.map(acc => (
            <button
              key={acc.account_id}
              style={styles.item}
              onClick={() => handleSelect(acc)}
            >
              <div style={styles.itemLeft}>
                <span style={{
                  ...styles.badge,
                  background: acc.type === 'demo' ? '#1e3a5f' : '#14532d',
                  color:      acc.type === 'demo' ? '#60a5fa' : '#4ade80',
                }}>
                  {acc.type === 'demo' ? 'DEMO' : 'REAL'}
                </span>
                <span style={styles.accountId}>{acc.account_id}</span>
              </div>
              <div style={styles.itemRight}>
                <span style={styles.balance}>
                  {acc.balance.toLocaleString()} {acc.currency}
                </span>
                <span style={styles.arrow}>→</span>
              </div>
            </button>
          ))}
        </div>
      </div>
    </div>
  )
}

const styles = {
  outer: {
    minHeight: '100dvh',
    background: '#030712',
    display: 'flex',
    alignItems: 'center',
    justifyContent: 'center',
    padding: '24px 16px',
  },
  card: {
    background: '#0f1623',
    border: '1px solid #1e2d45',
    borderRadius: 16,
    padding: '40px 36px',
    width: '100%',
    maxWidth: 420,
  },
  brand: {
    display: 'flex',
    alignItems: 'center',
    gap: 10,
    marginBottom: 6,
  },
  brandDot: {
    width: 10,
    height: 10,
    borderRadius: '50%',
    background: '#3b82f6',
    display: 'inline-block',
  },
  brandName: {
    color: '#f1f5f9',
    fontSize: 22,
    fontWeight: 600,
    letterSpacing: '-0.5px',
  },
  sub: {
    color: '#64748b',
    fontSize: 14,
    margin: '0 0 28px',
  },
  list: {
    display: 'flex',
    flexDirection: 'column',
    gap: 12,
  },
  item: {
    display: 'flex',
    alignItems: 'center',
    justifyContent: 'space-between',
    background: '#1e2d45',
    border: '1px solid #2a3f5f',
    borderRadius: 10,
    padding: '14px 16px',
    cursor: 'pointer',
    transition: 'border-color 0.15s',
  },
  itemLeft: {
    display: 'flex',
    alignItems: 'center',
    gap: 10,
  },
  badge: {
    fontSize: 10,
    fontWeight: 700,
    padding: '3px 8px',
    borderRadius: 4,
    letterSpacing: '0.5px',
  },
  accountId: {
    color: '#f1f5f9',
    fontSize: 14,
    fontWeight: 500,
  },
  itemRight: {
    display: 'flex',
    alignItems: 'center',
    gap: 10,
  },
  balance: {
    color: '#94a3b8',
    fontSize: 13,
    fontFamily: 'monospace',
  },
  arrow: {
    color: '#3b82f6',
    fontSize: 16,
  },
}