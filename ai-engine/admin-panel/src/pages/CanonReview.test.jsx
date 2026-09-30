import { screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import { renderWithStore, resetStore } from '../test/store-harness.jsx'
import CanonReview from './CanonReview.jsx'

const PROPOSAL = {
  id: 7,
  campaign: 'Greenrest',
  fact: 'The mill burned down in the spring.',
  rationale: 'The party watched it burn.',
  confidence: 'high',
}

let fetchCanonProposals
let approveCanonProposal
let rejectCanonProposal

function seed(extra = {}) {
  return renderWithStore(<CanonReview />, {
    fetchCanonProposals,
    approveCanonProposal,
    rejectCanonProposal,
    canonProposals: [],
    playModeSessions: {},
    campaignSession: { campaigns: [], selectedCampaign: null, activeSession: null, loading: false, error: null },
    ...extra,
  })
}

const inPlayMode = (campaign) => ({
  playModeSessions: { [campaign]: true },
  campaignSession: {
    campaigns: [], selectedCampaign: null, loading: false, error: null,
    activeSession: { session_id: 's1', campaign_name: campaign },
  },
})

beforeEach(() => {
  fetchCanonProposals = vi.fn(async () => {})
  approveCanonProposal = vi.fn(async () => ({ ok: true }))
  rejectCanonProposal = vi.fn(async () => ({ ok: true }))
})

afterEach(resetStore)

describe('CanonReview', () => {
  it('fetches proposals on mount', () => {
    seed()

    expect(fetchCanonProposals).toHaveBeenCalledTimes(1)
  })

  it('refetches on demand', async () => {
    const user = userEvent.setup()
    seed()

    await user.click(screen.getByRole('button', { name: /refresh/i }))

    expect(fetchCanonProposals).toHaveBeenCalledTimes(2)
  })

  it('shows an empty state with no proposals', () => {
    seed()

    expect(screen.getByText(/no pending canon proposals/i)).toBeInTheDocument()
  })

  it('renders a proposal with its confidence, campaign and rationale', () => {
    seed({ canonProposals: [PROPOSAL] })

    expect(screen.getByText('HIGH')).toBeInTheDocument()
    expect(screen.getByText('Greenrest')).toBeInTheDocument()
    expect(screen.getByText(PROPOSAL.rationale)).toBeInTheDocument()
    expect(screen.getByRole('textbox')).toHaveValue(PROPOSAL.fact)
  })

  it('labels a proposal with no confidence as unknown', () => {
    seed({ canonProposals: [{ ...PROPOSAL, confidence: undefined }] })

    expect(screen.getByText('UNKNOWN')).toBeInTheDocument()
  })

  it('flags a possible contradiction when the proposal carries one', () => {
    seed({ canonProposals: [{ ...PROPOSAL, contradiction_note: 'The mill is intact in Locations.md' }] })

    expect(screen.getByText(/possible contradiction/i)).toBeInTheDocument()
    expect(screen.getByText(/the mill is intact/i)).toBeInTheDocument()
  })

  it('shows no contradiction banner when there is none', () => {
    seed({ canonProposals: [PROPOSAL] })

    expect(screen.queryByText(/possible contradiction/i)).not.toBeInTheDocument()
  })

  it('approves an unedited fact with null, so the server keeps its own text', async () => {
    const user = userEvent.setup()
    seed({ canonProposals: [PROPOSAL] })

    await user.click(screen.getByRole('button', { name: /approve/i }))

    expect(approveCanonProposal).toHaveBeenCalledWith(7, null)
  })

  it('approves an edited fact with the operator\'s text', async () => {
    const user = userEvent.setup()
    seed({ canonProposals: [PROPOSAL] })

    const box = screen.getByRole('textbox')
    await user.clear(box)
    await user.type(box, 'The mill burned down in the autumn.')
    await user.click(screen.getByRole('button', { name: /approve/i }))

    expect(approveCanonProposal).toHaveBeenCalledWith(7, 'The mill burned down in the autumn.')
  })

  it('sends null again if an edit is reverted to the original', async () => {
    const user = userEvent.setup()
    seed({ canonProposals: [PROPOSAL] })

    const box = screen.getByRole('textbox')
    await user.type(box, ' extra')
    await user.clear(box)
    await user.type(box, PROPOSAL.fact)
    await user.click(screen.getByRole('button', { name: /approve/i }))

    expect(approveCanonProposal).toHaveBeenCalledWith(7, null)
  })

  it('rejects by id', async () => {
    const user = userEvent.setup()
    seed({ canonProposals: [PROPOSAL] })

    await user.click(screen.getByRole('button', { name: /reject/i }))

    expect(rejectCanonProposal).toHaveBeenCalledWith(7)
  })

  it('keeps each proposal\'s draft separate', async () => {
    const user = userEvent.setup()
    const second = { ...PROPOSAL, id: 8, fact: 'The bridge is out.' }
    seed({ canonProposals: [PROPOSAL, second] })

    const boxes = screen.getAllByRole('textbox')
    await user.clear(boxes[0])
    await user.type(boxes[0], 'edited first')

    expect(boxes[1]).toHaveValue('The bridge is out.')

    await user.click(screen.getAllByRole('button', { name: /approve/i })[1])
    expect(approveCanonProposal).toHaveBeenCalledWith(8, null)
  })

  describe('play mode', () => {
    it('hides proposals behind the spoiler wall for the active campaign', () => {
      seed({ canonProposals: [PROPOSAL], ...inPlayMode('Greenrest') })

      // Canon proposals are unannounced plot: in play mode they must not be
      // in the DOM until the operator asks for them.
      expect(screen.queryByText(PROPOSAL.rationale)).not.toBeInTheDocument()
      expect(screen.getByText(/play mode active/i)).toBeInTheDocument()
      expect(screen.getByText(/pending canon proposals/)).toBeInTheDocument()
    })

    it('reveals them once the operator accepts', async () => {
      const user = userEvent.setup()
      seed({ canonProposals: [PROPOSAL], ...inPlayMode('Greenrest') })

      await user.click(screen.getByRole('button', { name: /i understand, show me/i }))

      expect(screen.getByText(PROPOSAL.rationale)).toBeInTheDocument()
    })

    it('does not wall off a campaign that is not in play mode', () => {
      seed({
        canonProposals: [PROPOSAL],
        playModeSessions: { Blackmoor: true },
        campaignSession: {
          campaigns: [], selectedCampaign: null, loading: false, error: null,
          activeSession: { session_id: 's1', campaign_name: 'Greenrest' },
        },
      })

      expect(screen.getByText(PROPOSAL.rationale)).toBeInTheDocument()
    })

    it('does not wall off when play mode is off for the active campaign', () => {
      seed({
        canonProposals: [PROPOSAL],
        playModeSessions: { Greenrest: false },
        campaignSession: {
          campaigns: [], selectedCampaign: null, loading: false, error: null,
          activeSession: { session_id: 's1', campaign_name: 'Greenrest' },
        },
      })

      expect(screen.getByText(PROPOSAL.rationale)).toBeInTheDocument()
    })

    it('does not wall off when no session is active', () => {
      seed({ canonProposals: [PROPOSAL], playModeSessions: { Greenrest: true } })

      expect(screen.getByText(PROPOSAL.rationale)).toBeInTheDocument()
    })

    it('still offers refresh while walled off', () => {
      seed({ canonProposals: [PROPOSAL], ...inPlayMode('Greenrest') })

      expect(screen.getByRole('button', { name: /refresh/i })).toBeInTheDocument()
    })
  })
})
