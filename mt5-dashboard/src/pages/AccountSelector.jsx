import useBotStore from '../store/botStore'

const API = import.meta.env.VITE_API_URL ?? 'https://vestro-jpg.onrender.com'

export default function AccountSelector({ accounts, onSelect }) {
  async function handleSelect(acc) {
    // Persist active account to backend
    if (acc.user_id) {
      try {
        await fetch(`${API}/auth/set-active-account`, {
          method:  'POST',
          headers: { 'Content-Type': 'application/json' },
          body:    JSON.stringify({
            deriv_account: acc.account_id,
            user_id:       acc.user_id,
          }),
        })
      } catch (e) {
        console.warn('[AccountSelector] set-active-account failed:', e)
      }
    }

    // login(broker, accountId, accountData) — matches botStore signature exactly
    useBotStore.getState().login(
      acc.broker ?? 'deriv',
      acc.account_id,
      {
        balance:    acc.balance    ?? 0,
        equity:     acc.balance    ?? 0,   // Deriv doesn't send equity separately
        profit:     0,
        margin_free: 0,
        currency:   acc.currency   ?? 'USD',
        name:       acc.name       ?? '—',
        leverage:   0,
        is_virtual: acc.is_demo    ?? false,
        is_demo:    acc.is_demo    ?? false,
        email:      acc.email      ?? '',
        account_id: acc.account_id,
      }
    )

    onSelect(acc)
  }

  // Filter: only show USD accounts with a balance, or demo accounts
  // Show demo first, then real — sorted by balance descending within each group
  const demo = accounts
    .filter(a => a.is_demo || a.type === 'demo')
    .sort((a, b) => (b.balance ?? 0) - (a.balance ?? 0))

  const real = accounts
    .filter(a => !a.is_demo && a.type !== 'demo')
    .sort((a, b) => (b.balance ?? 0) - (a.balance ?? 0))

  const sorted = [...demo, ...real]

  return (
    <div style={styles.outer}>
      <div style={styles.card}>

        <div style={styles.brand}>
          <span style={styles.brandDot} />
          <span style={styles.brandName}>Vestro</span>
        </div>

        {accounts[0]?.email && (
          <p style={styles.email}>{accounts[0].email}</p>
        )}

        <p style={styles.sub}>Select an account to trade with</p>

        <div style={styles.list}>
          {sorted.map(acc => (
            <button
              key={acc.account_id}
              style={styles.item}
              onClick={() => handleSelect(acc)}
            >
              <div style={styles.itemLeft}>
                <span style={{
                  ...styles.badge,
                  background: acc.is_demo ? '#1e3a5f' : '#14532d',
                  color:      acc.is_demo ? '#60a5fa' : '#4ade80',
                }}>
                  {acc.is_demo ? 'DEMO' : 'REAL'}
                </span>
                <span style={styles.accountId}>{acc.account_id}</span>
              </div>
              <div style={styles.itemRight}>
                <span style={styles.balance}>
                  {Number(acc.balance ?? 0).toLocaleString(undefined, {
                    minimumFractionDigits: 2,
                    maximumFractionDigits: 2,
                  })} {acc.currency || 'USD'}
                </span>
                <span style={styles.arrow}>→</span>
              </div>
            </button>
          ))}
        </div>

        <button
          style={styles.addAccount}
          onClick={() => { window.location.href = `${API}/auth/google` }}
        >
          + Connect another account
        </button>

      </div>
    </div>
  )
}

const styles = {
  outer: {
    minHeight: '100dvh', background: '#030712',
    display: 'flex', alignItems: 'center', justifyContent: 'center',
    padding: '24px 16px',
  },
  card: {
    background: '#0f1623', border: '1px solid #1e2d45',
    borderRadius: 16, padding: '40px 36px',
    width: '100%', maxWidth: 420,
  },
  brand:     { display: 'flex', alignItems: 'center', gap: 10, marginBottom: 4 },
  brandDot:  { width: 10, height: 10, borderRadius: '50%', background: '#3b82f6', display: 'inline-block' },
  brandName: { color: '#f1f5f9', fontSize: 22, fontWeight: 600, letterSpacing: '-0.5px' },
  email:     { color: '#3b82f6', fontSize: 13, margin: '0 0 4px', fontFamily: 'monospace' },
  sub:       { color: '#64748b', fontSize: 14, margin: '0 0 24px' },
  list:      { display: 'flex', flexDirection: 'column', gap: 10 },
  item: {
    display: 'flex', alignItems: 'center', justifyContent: 'space-between',
    background: '#1e2d45', border: '1px solid #2a3f5f',
    borderRadius: 10, padding: '14px 16px',
    cursor: 'pointer', width: '100%',
    transition: 'border-color 0.15s',
  },
  itemLeft:  { display: 'flex', alignItems: 'center', gap: 10 },
  badge:     { fontSize: 10, fontWeight: 700, padding: '3px 8px', borderRadius: 4, letterSpacing: '0.5px' },
  accountId: { color: '#f1f5f9', fontSize: 14, fontWeight: 500 },
  itemRight: { display: 'flex', alignItems: 'center', gap: 10 },
  balance:   { color: '#94a3b8', fontSize: 13, fontFamily: 'monospace' },
  arrow:     { color: '#3b82f6', fontSize: 16 },
  addAccount: {
    width: '100%', marginTop: 16,
    background: 'transparent', border: '1px dashed #2a3f5f',
    borderRadius: 10, color: '#475569', fontSize: 13,
    padding: '12px 0', cursor: 'pointer',
  },
}