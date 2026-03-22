export default function StatCard({ label, value, sub, color = '#f1f5f9' }) {
  return (
    <div style={{
      background: '#1f2937', borderRadius: 10,
      padding: '12px 14px', minWidth: 0,
    }}>
      <div style={{
        fontSize: 10, color: '#6b7280', textTransform: 'uppercase',
        letterSpacing: '0.05em', fontWeight: 500,
      }}>
        {label}
      </div>
      <div style={{
        fontSize: 22, fontWeight: 600, color,
        marginTop: 4, lineHeight: 1.2,
      }}>
        {value}
      </div>
      {sub && (
        <div style={{ fontSize: 11, color: '#6b7280', marginTop: 3 }}>
          {sub}
        </div>
      )}
    </div>
  )
}