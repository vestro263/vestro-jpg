// src/pages/AuthCallback.jsx
import { useEffect } from 'react'
import { useNavigate } from 'react-router-dom'
import useBotStore from '../store/botStore'

export default function AuthCallback() {
  const { login } = useBotStore()
  const navigate = useNavigate()

  useEffect(() => {
    const params = new URLSearchParams(window.location.search)
    const account_id = params.get('account_id')
    const balance    = parseFloat(params.get('balance') ?? '0')
    const currency   = params.get('currency') ?? 'USD'

    if (account_id) {
      login('deriv', account_id, { account_id, balance, currency, equity: balance, profit: 0 })
      navigate('/dashboard')
    } else {
      navigate('/login?error=oauth_failed')
    }
  }, [])

  return (
    <div style={{ minHeight: '100dvh', background: '#030712', display: 'flex', alignItems: 'center', justifyContent: 'center' }}>
      <p style={{ color: '#64748b', fontSize: 14 }}>Connecting your account…</p>
    </div>
  )
}