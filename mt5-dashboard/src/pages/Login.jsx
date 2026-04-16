import { useState } from 'react'
import useBotStore from '../store/botStore'

const API = import.meta.env.VITE_API_URL ?? 'https://vestro-jpg.onrender.com'

export default function Login() {
  const { authError } = useBotStore()

  const [loading, setLoading] = useState(false)

  function handleGoogleLogin() {
    setLoading(true)
    window.location.href = `${API}/auth/google`
  }

  return (
    <div style={styles.outer}>
      <div style={styles.card}>

        <div style={styles.brand}>
          <span style={styles.brandDot} />
          <span style={styles.brandName}>Vestro Capital</span>
        </div>

        <p style={styles.sub}>Sign in to access your trading dashboard</p>

        {authError && <div style={styles.error}>{authError}</div>}

        <button
          onClick={handleGoogleLogin}
          disabled={loading}
          style={{ ...styles.googleBtn, opacity: loading ? 0.7 : 1 }}
        >
          <GoogleIcon />
          {loading ? 'Redirecting…' : 'Continue with Google'}
        </button>

        <p style={styles.note}>
          After signing in, you'll connect your Deriv account to start trading.
        </p>

        <div style={styles.divider} />
        <p style={styles.fine}>
          Your Deriv credentials are encrypted and never stored in plain text.
        </p>

      </div>
    </div>
  )
}

function GoogleIcon() {
  return (
    <svg width="18" height="18" viewBox="0 0 18 18" style={{ flexShrink: 0 }}>
      <path fill="#4285F4" d="M17.64 9.2c0-.637-.057-1.251-.164-1.84H9v3.481h4.844c-.209 1.125-.843 2.078-1.796 2.717v2.258h2.908c1.702-1.567 2.684-3.874 2.684-6.615z"/>
      <path fill="#34A853" d="M9 18c2.43 0 4.467-.806 5.956-2.184l-2.908-2.258c-.806.54-1.837.86-3.048.86-2.344 0-4.328-1.584-5.036-3.711H.957v2.332C2.438 15.983 5.482 18 9 18z"/>
      <path fill="#FBBC05" d="M3.964 10.707c-.18-.54-.282-1.117-.282-1.707s.102-1.167.282-1.707V4.961H.957C.347 6.175 0 7.55 0 9s.348 2.825.957 4.039l3.007-2.332z"/>
      <path fill="#EA4335" d="M9 3.58c1.321 0 2.508.454 3.44 1.345l2.582-2.58C13.463.891 11.426 0 9 0 5.482 0 2.438 2.017.957 4.961L3.964 7.293C4.672 5.166 6.656 3.58 9 3.58z"/>
    </svg>
  )
}

const styles = {
  outer:     { minHeight: '100dvh', background: '#030712', display: 'flex', alignItems: 'center', justifyContent: 'center', padding: '24px 16px' },
  card:      { background: '#0f1623', border: '1px solid #1e2d45', borderRadius: 16, padding: '40px 36px', width: '100%', maxWidth: 400, display: 'flex', flexDirection: 'column' },
  brand:     { display: 'flex', alignItems: 'center', gap: 10, marginBottom: 8 },
  brandDot:  { width: 10, height: 10, borderRadius: '50%', background: '#3b82f6', display: 'inline-block' },
  brandName: { color: '#f1f5f9', fontSize: 22, fontWeight: 600, letterSpacing: '-0.5px' },
  sub:       { color: '#64748b', fontSize: 14, margin: '0 0 28px', lineHeight: 1.5 },
  error:     { color: '#f87171', fontSize: 13, background: '#1f1217', border: '1px solid #7f1d1d', borderRadius: 6, padding: '10px 14px', marginBottom: 16, lineHeight: 1.5 },
  googleBtn: { width: '100%', display: 'flex', alignItems: 'center', justifyContent: 'center', gap: 10, background: '#fff', color: '#1e293b', border: 'none', borderRadius: 8, padding: '12px 0', fontSize: 15, fontWeight: 600, cursor: 'pointer' },
  note:      { color: '#475569', fontSize: 13, textAlign: 'center', margin: '16px 0 0', lineHeight: 1.5 },
  divider:   { height: 1, background: '#1e2d45', margin: '24px 0 16px' },
  fine:      { color: '#334155', fontSize: 11, textAlign: 'center', lineHeight: 1.6, margin: 0 },
}