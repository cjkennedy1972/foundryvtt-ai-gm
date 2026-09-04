import React, { useState } from 'react'

const SpoilerWall = ({ children, label = 'spoiler content' }) => {
  const [revealed, setRevealed] = useState(false)

  if (revealed) {
    return children
  }

  return (
    <div style={{
      padding: '24px',
      textAlign: 'center',
      backgroundColor: 'var(--bg-tertiary)',
      borderRadius: '8px',
      border: '2px dashed var(--bg-active)',
    }}>
      <h3 style={{ fontSize: '16px', marginBottom: '8px', marginTop: 0 }}>
        ⚠️ Play Mode Active
      </h3>
      <p style={{
        fontSize: '13px',
        color: 'var(--text-secondary)',
        marginBottom: '16px',
        lineHeight: '1.5'
      }}>
        This panel contains {label} that will spoil your campaign. Revealing it now prevents accidents, but won't stop you if you're determined — the database is yours.
      </p>
      <button
        type="button"
        onClick={() => setRevealed(true)}
        className="btn btn-sm"
        style={{
          backgroundColor: 'rgba(244, 67, 54, 0.15)',
          borderColor: 'var(--danger)',
          color: 'var(--danger)',
        }}
      >
        I understand, show me
      </button>
    </div>
  )
}

export default SpoilerWall
