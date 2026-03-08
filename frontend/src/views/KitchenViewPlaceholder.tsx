import { useParams } from 'react-router-dom'

export default function KitchenViewPlaceholder() {
  const { jobId } = useParams()
  return (
    <div
      style={{
        fontFamily: 'var(--font-family)',
        minHeight: '100vh',
        background: 'var(--bg-canvas)',
        display: 'flex',
        alignItems: 'center',
        justifyContent: 'center',
        color: 'var(--text-tertiary)',
        fontSize: 'var(--font-size-body)',
      }}
    >
      Kitchen View — Task 11 — Job: {jobId}
    </div>
  )
}
