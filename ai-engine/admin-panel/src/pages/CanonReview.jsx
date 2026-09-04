import React, { useEffect, useState } from 'react'
import { useStore } from '../store.js'
import SpoilerWall from '../components/SpoilerWall.jsx'

const CONFIDENCE_BADGE = {
  high: 'badge-connected',
  medium: 'badge-running',
  low: 'badge-disconnected',
}

const CanonReview = () => {
  const { canonProposals, fetchCanonProposals, approveCanonProposal, rejectCanonProposal, playModeSessions, campaignSession } = useStore()
  const [drafts, setDrafts] = useState({})

  useEffect(() => {
    fetchCanonProposals()
  }, [])

  const draftFor = (proposal) => drafts[proposal.id] ?? proposal.fact

  const handleApprove = (proposal) => {
    const draft = draftFor(proposal)
    const finalText = draft !== proposal.fact ? draft : null
    approveCanonProposal(proposal.id, finalText)
  }

  const activeCampaign = campaignSession.activeSession?.campaign_name
  const isPlayModeActive = activeCampaign && playModeSessions[activeCampaign]

  const proposalContent = canonProposals.length === 0 ? (
    <div className="empty-state">
      <p>No pending canon proposals</p>
    </div>
  ) : (
    <div style={{ display: 'flex', flexDirection: 'column', gap: '16px' }}>
      {canonProposals.map((proposal) => (
            <div key={proposal.id} className="card">
              <div style={{ display: 'flex', alignItems: 'center', gap: '10px', marginBottom: '10px' }}>
                <span className={`badge ${CONFIDENCE_BADGE[proposal.confidence] || 'badge-running'}`}>
                  {(proposal.confidence || 'unknown').toUpperCase()}
                </span>
                <span style={{ fontSize: '12px', color: 'var(--text-secondary)' }}>{proposal.campaign}</span>
              </div>

              {proposal.contradiction_note && (
                <div style={{
                  background: 'rgba(244, 67, 54, 0.12)',
                  border: '1px solid var(--danger)',
                  borderRadius: '6px',
                  padding: '10px',
                  marginBottom: '12px',
                }}>
                  <strong style={{ color: 'var(--danger)', fontSize: '13px' }}>⚠️ Possible contradiction</strong>
                  <p style={{ fontSize: '13px', margin: '4px 0 0' }}>{proposal.contradiction_note}</p>
                </div>
              )}

              <p style={{ fontSize: '12px', color: 'var(--text-secondary)', marginBottom: '10px' }}>
                {proposal.rationale}
              </p>

              <textarea
                className="input"
                value={draftFor(proposal)}
                onChange={(e) => setDrafts((d) => ({ ...d, [proposal.id]: e.target.value }))}
                rows={3}
                style={{ width: '100%', marginBottom: '10px', resize: 'vertical' }}
              />

              <div style={{ display: 'flex', gap: '8px' }}>
                <button className="btn btn-sm" onClick={() => handleApprove(proposal)}>
                  ✅ Approve
                </button>
                <button className="btn btn-sm" onClick={() => rejectCanonProposal(proposal.id)}>
                  ❌ Reject
                </button>
              </div>
            </div>
          ))}
      </div>
    )

  return (
    <div>
      <div className="section-header">
        <div>
          <h2>Canon Review</h2>
          <p>Review AI-proposed canon facts before they're written to the campaign vault</p>
        </div>
        <button className="btn btn-sm" onClick={fetchCanonProposals}>
          ↻ Refresh
        </button>
      </div>

      {isPlayModeActive ? (
        <SpoilerWall label="pending canon proposals">
          {proposalContent}
        </SpoilerWall>
      ) : (
        proposalContent
      )}
    </div>
  )
}

export default CanonReview
