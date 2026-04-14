import { useState } from 'react'
import useBotStore from '../store/botStore'

const API = import.meta.env.VITE_API_URL ?? 'https://vestro-jpg.onrender.com'
const FRONTEND_URL = import.meta.env.VITE_FRONTEND_URL ?? 'https://vestro-ui.onrender.com'

export default function Login() {
  const { authError, demoUrl, userId } = useBotStore()
  const [loading, setLoading] = useState(false)

  const [showModal, setShowModal] = useState(false)
  const [mt5Id, setMt5Id]         = useState('')
  const [linking, setLinking]     = useState(false)
  const [linkError, setLinkError] = useState('')

  const isDemoRequired = authError?.includes('create one')

  function handleGoogleLogin() {
    setLoading(true)
    window.location.href = `${API}/auth/google`
  }

  function handleCreateDemo() {
    window.open(demoUrl || 'https://hub.deriv.com/tradershub/home', '_blank')
  }

  function openModal() {
    setMt5Id('')
    setLinkError('')
    setShowModal(true)
  }

  async function handleLinkAccount() {
    const trimmed = mt5Id.trim()
    if (!trimmed) return
    setLinking(true)
    setLinkError('')
    try {
      const res = await fetch(`${API}/auth/link-demo-account`, {
        method:  'POST',
        headers: { 'Content-Type': 'application/json' },
        body:    JSON.stringify({ mt5_login_id: trimmed }),
      })
      const data = await res.json()
      if (!res.ok) throw new Error(data.detail || 'Linking failed')
      window.location.href = `${FRONTEND_URL}?user_id=${userId}&active_account=${data.active}`
    } catch (err) {
      setLinkError(err.message)
    } finally {
      setLinking(false)
    }
  }

  return (
    <div style={styles.outer}>
      <div style={styles.card}>

        <div style={styles.brand}>
          <span style={styles.brandDot} />
          <span style={styles.brandName}>Vestro Capital</span>
        </div>

        <p style={styles.sub}>Sign in to access your trading dashboard</p>

        {isDemoRequired ? (
          <div style={styles.demoBox}>
            <div style={styles.demoIcon}>!</div>
            <p style={styles.demoTitle}>Demo account required</p>
            <p style={styles.demoText}>
              Vestro only works with Deriv demo accounts. Create a free demo account on Deriv, then come back and sign in.
            </p>
            <button onClick={handleCreateDemo} style={styles.derivBtn}>
              Create Deriv demo account →
            </button>
            <button onClick={openModal} style={styles.retryBtn}>
              I already created one — link my account
            </button>
          </div>
        ) : (
          <>
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
          </>
        )}

        <div style={styles.divider} />
        <p style={styles.fine}>
          Your Deriv credentials are encrypted and never stored in plain text.
        </p>

      </div>

      {/* MT5 link modal */}
      {showModal && (
        <div style={styles.overlay} onClick={() => setShowModal(false)}>
          <div style={styles.modal} onClick={e => e.stopPropagation()}>

            <div style={styles.modalHeader}>
              <p style={styles.modalTitle}>Link your demo account</p>
              <button onClick={() => setShowModal(false)} style={styles.closeBtn}>×</button>
            </div>

            <p style={styles.modalSub}>
              Open Deriv, tap your account name → <strong style={{ color: '#94a3b8' }}>Account details</strong>.
              Copy the <strong style={{ color: '#94a3b8' }}>Login ID</strong> shown there.
            </p>

            {/* Mini visual hint */}
            <div style={styles.hint}>
              <div style={styles.hintRow}>
                <span style={styles.hintLabel}>Login ID</span>
                <span style={styles.hintValue}>7070770</span>
              </div>
              <div style={styles.hintRow}>
                <span style={styles.hintLabel}>Server</span>
                <span style={styles.hintValueMuted}>Deriv-Demo</span>
              </div>
            </div>

            <input
              style={styles.input}
              placeholder="Enter Login ID, e.g. 7070770"
              value={mt5Id}
              onChange={e => setMt5Id(e.target.value.replace(/\D/g, ''))}
              onKeyDown={e => e.key === 'Enter' && handleLinkAccount()}
              inputMode="numeric"
              autoFocus
            />

            {linkError && <p style={styles.linkError}>{linkError}</p>}

            <div style={styles.modalBtns}>
              <button onClick={() => setShowModal(false)} style={styles.cancelBtn}>
                Cancel
              </button>
              <button
                onClick={handleLinkAccount}
                disabled={linking || !mt5Id.trim()}
                style={{ ...styles.linkBtn, opacity: (linking || !mt5Id.trim()) ? 0.6 : 1 }}
              >
                {linking ? 'Linking…' : 'Link account'}
              </button>
            </div>

          </div>
        </div>
      )}

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
  outer:      { minHeight: '100dvh', background: '#030712', display: 'flex', alignItems: 'center', justifyContent: 'center', padding: '24px 16px' },
  card:       { background: '#0f1623', border: '1px solid #1e2d45', borderRadius: 16, padding: '40px 36px', width: '100%', maxWidth: 400, display: 'flex', flexDirection: 'column' },
  brand:      { display: 'flex', alignItems: 'center', gap: 10, marginBottom: 8 },
  brandDot:   { width: 10, height: 10, borderRadius: '50%', background: '#3b82f6', display: 'inline-block' },
  brandName:  { color: '#f1f5f9', fontSize: 22, fontWeight: 600, letterSpacing: '-0.5px' },
  sub:        { color: '#64748b', fontSize: 14, margin: '0 0 28px', lineHeight: 1.5 },
  error:      { color: '#f87171', fontSize: 13, background: '#1f1217', border: '1px solid #7f1d1d', borderRadius: 6, padding: '10px 14px', marginBottom: 16, lineHeight: 1.5 },
  googleBtn:  { width: '100%', display: 'flex', alignItems: 'center', justifyContent: 'center', gap: 10, background: '#fff', color: '#1e293b', border: 'none', borderRadius: 8, padding: '12px 0', fontSize: 15, fontWeight: 600, cursor: 'pointer' },
  note:       { color: '#475569', fontSize: 13, textAlign: 'center', margin: '16px 0 0', lineHeight: 1.5 },
  divider:    { height: 1, background: '#1e2d45', margin: '24px 0 16px' },
  fine:       { color: '#334155', fontSize: 11, textAlign: 'center', lineHeight: 1.6, margin: 0 },
  demoBox:    { background: '#0d1f35', border: '1px solid #1e3a5f', borderRadius: 10, padding: '20px 18px', marginBottom: 4, display: 'flex', flexDirection: 'column', gap: 10 },
  demoIcon:   { width: 28, height: 28, borderRadius: '50%', background: '#1e3a5f', color: '#60a5fa', fontSize: 16, fontWeight: 700, display: 'flex', alignItems: 'center', justifyContent: 'center' },
  demoTitle:  { color: '#f1f5f9', fontSize: 15, fontWeight: 600, margin: 0 },
  demoText:   { color: '#64748b', fontSize: 13, margin: 0, lineHeight: 1.6 },
  derivBtn:   { width: '100%', background: '#1d4ed8', color: '#fff', border: 'none', borderRadius: 8, padding: '11px 0', fontSize: 14, fontWeight: 600, cursor: 'pointer', marginTop: 4 },
  retryBtn:   { width: '100%', background: 'transparent', color: '#475569', border: '1px solid #1e2d45', borderRadius: 8, padding: '10px 0', fontSize: 13, cursor: 'pointer' },

  // Modal
  overlay:    { position: 'fixed', inset: 0, background: 'rgba(0,0,0,0.65)', display: 'flex', alignItems: 'center', justifyContent: 'center', zIndex: 50, padding: '16px' },
  modal:      { background: '#0f1623', border: '1px solid #1e2d45', borderRadius: 14, padding: '24px 22px', width: '100%', maxWidth: 360, display: 'flex', flexDirection: 'column', gap: 14 },
  modalHeader:{ display: 'flex', justifyContent: 'space-between', alignItems: 'center' },
  modalTitle: { color: '#f1f5f9', fontSize: 15, fontWeight: 600, margin: 0 },
  closeBtn:   { background: 'none', border: 'none', color: '#475569', fontSize: 20, cursor: 'pointer', padding: 0, lineHeight: 1 },
  modalSub:   { color: '#64748b', fontSize: 13, margin: 0, lineHeight: 1.6 },

  // Mini hint card
  hint:       { background: '#0d1f35', border: '1px solid #1e3a5f', borderRadius: 8, padding: '10px 14px', display: 'flex', flexDirection: 'column', gap: 6 },
  hintRow:    { display: 'flex', justifyContent: 'space-between', alignItems: 'center' },
  hintLabel:  { color: '#475569', fontSize: 12 },
  hintValue:  { color: '#93c5fd', fontSize: 13, fontFamily: 'monospace', fontWeight: 600 },
  hintValueMuted: { color: '#334155', fontSize: 13, fontFamily: 'monospace' },

  input:      { background: '#0d1f35', border: '1px solid #1e3a5f', borderRadius: 8, padding: '11px 13px', color: '#f1f5f9', fontSize: 15, fontFamily: 'monospace', outline: 'none', letterSpacing: '0.05em' },
  linkError:  { color: '#f87171', fontSize: 12, margin: 0, lineHeight: 1.5 },
  modalBtns:  { display: 'flex', gap: 10, marginTop: 2 },
  cancelBtn:  { flex: 1, background: 'transparent', color: '#475569', border: '1px solid #1e2d45', borderRadius: 8, padding: '10px 0', fontSize: 13, cursor: 'pointer' },
  linkBtn:    { flex: 2, background: '#1d4ed8', color: '#fff', border: 'none', borderRadius: 8, padding: '10px 0', fontSize: 14, fontWeight: 600, cursor: 'pointer' },
}