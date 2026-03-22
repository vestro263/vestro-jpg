import useBotStore from '../store/botStore'

export default function AccountBar() {
  const { account, connected } = useBotStore()

  const dd = account.balance
    ? Math.abs(((account.balance - account.equity) / account.balance) * 100).toFixed(2)
    : '0.00'

  const Item = ({ label, value, color = '#e5e7eb' }) => (
    <div style={{ display: 'flex', flexDirection: 'column', gap: 1 }}>
      <span style={{ fontSize: 10, color: '#4b5563', textTransform: 'uppercase', letterSpacing: '0.05em' }}>
        {label}
      </span>
      <span style={{ fontSize: 13, fontWeight: 600, color }}>{value}</span>
    </div>
  )

  return (
    <div style={{
      background: '#0b1120',
      borderBottom: '1px solid #1f2937',
      padding: '10px 24px',
      display: 'flex',
      alignItems: 'center',
      gap: 32,
      flexShrink: 0,
    }}>
      {/* Account name + connection dot */}
      <div style={{ display: 'flex', alignItems: 'center', gap: 8, marginRight: 8 }}>
        <span style={{
          width: 7, height: 7, borderRadius: '50%',
          background: connected ? '#4ade80' : '#f87171',
          boxShadow: connected ? '0 0 6px #4ade80' : '0 0 6px #f87171',
          flexShrink: 0,
        }} />
        <span style={{ fontSize: 12, fontWeight: 600, color: '#9ca3af' }}>
          {account.name || '—'}
        </span>
      </div>

      <Item label="Balance"  value={`${account.currency || 'USD'} ${(account.balance || 0).toLocaleString('en', { minimumFractionDigits: 2 })}`} />
      <Item
        label="Equity"
        value={`${account.currency || 'USD'} ${(account.equity || 0).toLocaleString('en', { minimumFractionDigits: 2 })}`}
        color={(account.equity || 0) >= (account.balance || 0) ? '#4ade80' : '#f87171'}
      />
      <Item label="Free Margin" value={`${account.currency || 'USD'} ${(account.margin_free || 0).toLocaleString('en', { minimumFractionDigits: 2 })}`} />
      <Item
        label="Drawdown"
        value={`${dd}%`}
        color={parseFloat(dd) > 3 ? '#f87171' : parseFloat(dd) > 1.5 ? '#fbbf24' : '#4ade80'}
      />
      <Item label="Leverage" value={account.leverage ? `1:${account.leverage}` : '—'} color="#93c5fd" />

      {/* push right: timestamp */}
      <div style={{ marginLeft: 'auto', fontSize: 11, color: '#374151' }}>
        {new Date().toLocaleTimeString()}
      </div>
    </div>
  )
}