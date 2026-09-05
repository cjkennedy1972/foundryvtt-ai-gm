import React, { useEffect, useState } from 'react'
import { useStore } from '../store.js'

// Between-session turns (CKP-102). Deliberately NOT a spoiler surface: this
// page shows what the player wrote and whether it resolved, never what came
// of it. The operator is also a player, and the outcome is theirs to hear
// when the GM narrates it at the next session start.

const STOPPED_REASONS = {
  empty_action: 'Give both a character and what they do.',
  no_session: 'This campaign has no session history yet — play a session first.',
  llm_error: 'The turn could not be resolved. Nothing was recorded.',
  no_outcome: 'The turn produced nothing to narrate. Nothing was recorded.',
}

const Downtime = () => {
  const { pendingDowntime, fetchPendingDowntime, submitDowntimeTurn, campaignSession } = useStore()
  const campaign = campaignSession.activeSession?.campaign_name || ''

  const [player, setPlayer] = useState('')
  const [action, setAction] = useState('')
  const [busy, setBusy] = useState(false)
  const [notice, setNotice] = useState(null)

  useEffect(() => {
    fetchPendingDowntime(campaign)
  }, [campaign])

  const handleSubmit = async (e) => {
    e.preventDefault()
    setBusy(true)
    setNotice(null)
    const res = await submitDowntimeTurn(player.trim(), action.trim(), campaign)
    setBusy(false)

    if (res?.ok && res.data?.resolved) {
      setAction('')
      setNotice({ kind: 'ok', text: `Recorded for ${res.data.player}. You will hear how it went when the next session opens.` })
      return
    }
    const reason = res?.data?.stopped_reason
    setNotice({ kind: 'error', text: STOPPED_REASONS[reason] || res?.error || 'The turn could not be resolved.' })
  }

  return (
    <div className="page">
      <h2>🕰️ Downtime</h2>
      <p style={{ color: 'var(--text-secondary)', fontSize: '13px', marginTop: '-4px' }}>
        What a character does between sessions. Resolved without a live session — the
        GM narrates the result when you next sit down.
      </p>

      <form className="card" onSubmit={handleSubmit} style={{ display: 'flex', flexDirection: 'column', gap: '12px' }}>
        <label style={{ fontSize: '13px' }}>
          Character
          <input
            type="text"
            value={player}
            onChange={(e) => setPlayer(e.target.value)}
            placeholder="Ranger"
            style={{ width: '100%', marginTop: '4px' }}
          />
        </label>

        <label style={{ fontSize: '13px' }}>
          What they do
          <textarea
            value={action}
            onChange={(e) => setAction(e.target.value)}
            rows={3}
            placeholder="My ranger spends the week tracking the cult."
            style={{ width: '100%', marginTop: '4px' }}
          />
        </label>

        <button type="submit" className="btn" disabled={busy || !player.trim() || !action.trim()}>
          {busy ? 'Resolving…' : 'Submit downtime turn'}
        </button>

        {notice && (
          <p style={{ fontSize: '13px', margin: 0, color: notice.kind === 'ok' ? 'var(--text-primary)' : 'var(--danger)' }}>
            {notice.text}
          </p>
        )}
      </form>

      <h3 style={{ marginTop: '20px' }}>Waiting for the next session</h3>
      {pendingDowntime.length === 0 ? (
        <div className="empty-state">
          <p>Nothing pending</p>
        </div>
      ) : (
        <div style={{ display: 'flex', flexDirection: 'column', gap: '10px' }}>
          {pendingDowntime.map((turn, i) => (
            <div key={i} className="card">
              <strong style={{ fontSize: '13px' }}>{turn.player}</strong>
              <p style={{ fontSize: '13px', margin: '4px 0 0', color: 'var(--text-secondary)' }}>{turn.action}</p>
            </div>
          ))}
        </div>
      )}
    </div>
  )
}

export default Downtime
