import { useState } from 'react'
import useBotStore from '../store/botStore'

const DERIV_APP_ID = '132144'
const DERIV_OAUTH_URL = `https://oauth.deriv.com/oauth2/authorize?app_id=132144&l=EN&brand=deriv`

const API = import.meta.env.VITE_API_URL ?? 'https://vestro-jpg.onrender.com'
const BROKERS = [
  { value: 'deriv',     label: 'Deriv' },
  { value: 'welltrade', label: 'WelTrade (MT5)' },
]

export default function Login() {
  const { login, setAuthError, authError } = useBotStore()
  const [broker, setBroker]     = useState('deriv')
  const [loginId, setLoginId]   = useState('')
  const [password, setPassword] = useState('')
  const [loading, setLoading]   = useState(false)

  function handleDerivOAuth() {
    window.location.href = DERIV_OAUTH_URL
  }

  async function handleWellTradeConnect(e) {
    e.preventDefault()
    setLoading(true)
    setAuthError(null)
    try {
      const res = await fetch(`${API}/api/connect`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ broker, login: loginId, password }),
      })
      const raw = await res.text()
      if (!raw) throw new Error(`Empty response — status ${res.status}`)
      const data = JSON.parse(raw)
      if (!res.ok) throw new Error(data.detail || 'Connection failed')
      login(broker, data.account.account_id ?? loginId, data.account)
    } catch (err) {
      setAuthError(err.message)
    } finally {
      setLoading(false)
    }
  }

  return (
    <div style={styles.outer}>
      <div style={styles.card}>

        <div style={styles.brand}>
          <span style={styles.brandDot} />
          <span style={styles.brandName}>Vestro Capital </span>
        </div>
        <p style={styles.sub}>Connect your trading account</p>

        {/* Broker switcher */}
        <div style={styles.field}>
          <label style={styles.label}>Broker</label>
          <div style={styles.segmented}>
            {BROKERS.map(b => (
              <button
                key={b.value}
                type="button"
                onClick={() => { setBroker(b.value); setAuthError(null) }}
                style={{ ...styles.seg, ...(broker === b.value ? styles.segActive : {}) }}
              >
                {b.label}
              </button>
            ))}
          </div>
        </div>

        {/* DERIV — OAuth button only */}
        {broker === 'deriv' && (
          <div style={{ marginTop: 24 }}>
            <p style={styles.oauthNote}>
              You'll be taken to Deriv's secure login page. Once you approve, you'll be connected automatically.
            </p>
            <button onClick={handleDerivOAuth} style={styles.btn}>
              Login with Deriv
            </button>
          </div>
        )}

        {/* WELLTRADE — MT5 login form */}
        {broker === 'welltrade' && (
          <form onSubmit={handleWellTradeConnect} style={styles.form}>
            <div style={styles.field}>
              <label style={styles.label}>MT5 Login</label>
              <input
                style={styles.input}
                value={loginId}
                onChange={e => setLoginId(e.target.value)}
                placeholder="12345678"
                required
              />
            </div>
            <div style={styles.field}>
              <label style={styles.label}>MT5 Password</label>
              <input
                style={styles.input}
                type="password"
                value={password}
                onChange={e => setPassword(e.target.value)}
                placeholder="••••••••"
                required
              />
            </div>
            {authError && <p style={styles.error}>{authError}</p>}
            <button type="submit" style={styles.btn} disabled={loading}>
              {loading ? 'Connecting…' : 'Connect account'}
            </button>
          </form>
        )}

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
  form: {
    display: 'flex',
    flexDirection: 'column',
    gap: 18,
    marginTop: 24,
  },
  field: {
    display: 'flex',
    flexDirection: 'column',
    gap: 6,
  },
  label: {
    color: '#94a3b8',
    fontSize: 13,
    fontWeight: 500,
  },
  input: {
    background: '#1e2d45',
    border: '1px solid #2a3f5f',
    borderRadius: 8,
    color: '#f1f5f9',
    fontSize: 14,
    padding: '10px 14px',
    outline: 'none',
  },
  segmented: {
    display: 'flex',
    background: '#1e2d45',
    borderRadius: 8,
    padding: 3,
    gap: 3,
  },
  seg: {
    flex: 1,
    padding: '8px 0',
    border: 'none',
    borderRadius: 6,
    background: 'transparent',
    color: '#64748b',
    fontSize: 13,
    fontWeight: 500,
    cursor: 'pointer',
    transition: 'all 0.15s',
  },
  segActive: {
    background: '#2563eb',
    color: '#fff',
  },
  oauthNote: {
    color: '#64748b',
    fontSize: 13,
    marginBottom: 16,
    lineHeight: 1.5,
  },
  error: {
    color: '#f87171',
    fontSize: 13,
    margin: 0,
    background: '#1f1217',
    border: '1px solid #7f1d1d',
    borderRadius: 6,
    padding: '8px 12px',
  },
  btn: {
    width: '100%',
    background: '#2563eb',
    color: '#fff',
    border: 'none',
    borderRadius: 8,
    padding: '12px 0',
    fontSize: 15,
    fontWeight: 600,
    cursor: 'pointer',
  },
}