import { act, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import { renderWithStore, resetStore, useStore } from '../test/store-harness.jsx'
import GMChat from './GMChat.jsx'

let sendDirectGMMessage
let scrollIntoView

const PROMPT = /ask the gm anything/i

function seed(extra = {}) {
  return renderWithStore(<GMChat />, { sendDirectGMMessage, ...extra })
}

function inCombat(combat) {
  return { gameState: { mode: 'combat', combat } }
}

function inPlayMode(name) {
  return {
    campaignSession: {
      campaigns: [], selectedCampaign: null, loading: false, error: null,
      activeSession: { session_id: 's1', campaign_name: name },
    },
    playModeSessions: { [name]: true },
  }
}

beforeEach(() => {
  sendDirectGMMessage = vi.fn(async () => {})
  // jsdom does not implement scrollIntoView, and the page no longer needs it
  // to: tests run without one unless they install a spy to assert on.
  scrollIntoView = vi.fn()
})

afterEach(() => {
  delete Element.prototype.scrollIntoView
  resetStore()
})

describe('GMChat', () => {
  it('invites a first message when the log is empty', () => {
    seed()

    expect(screen.getByText(/start a conversation with the ai gm/i)).toBeInTheDocument()
  })

  it('renders the log and sides each message by its role', () => {
    seed({
      gmChatMessages: [
        { role: 'user', content: 'Who runs the mill?' },
        { role: 'assistant', content: 'A miller named Thaddeus.' },
      ],
    })

    expect(screen.getByText('Who runs the mill?').closest('.chat-message')).toHaveClass('chat-message-user')
    expect(screen.getByText('A miller named Thaddeus.').closest('.chat-message')).toHaveClass('chat-message-assistant')
    expect(screen.queryByText(/start a conversation/i)).not.toBeInTheDocument()
  })

  it('scrolls the log to the newest message', () => {
    Element.prototype.scrollIntoView = scrollIntoView
    seed({ gmChatMessages: [{ role: 'user', content: 'Who runs the mill?' }] })

    expect(scrollIntoView).toHaveBeenCalledWith({ behavior: 'smooth' })
  })

  it('renders and updates where scrollIntoView does not exist', () => {
    // jsdom is such an environment, as are some embedded webviews. An
    // auto-scroll convenience must not take the chat panel down with it.
    expect(Element.prototype.scrollIntoView).toBeUndefined()

    seed({ gmChatMessages: [{ role: 'user', content: 'Who runs the mill?' }] })
    act(() => {
      useStore.setState({ gmChatMessages: [{ role: 'user', content: 'Who runs the mill?' }, { role: 'assistant', content: 'A miller.' }] })
    })

    expect(screen.getByText('A miller.')).toBeInTheDocument()
  })

  it('keeps Send disabled until there is something to send', async () => {
    const user = userEvent.setup()
    seed()
    const send = screen.getByRole('button', { name: 'Send' })

    expect(send).toBeDisabled()

    await user.type(screen.getByPlaceholderText(PROMPT), 'Who runs the mill?')
    expect(send).toBeEnabled()
  })

  it('treats a whitespace-only message as nothing to send', async () => {
    const user = userEvent.setup()
    seed()

    await user.type(screen.getByPlaceholderText(PROMPT), '   ')

    expect(screen.getByRole('button', { name: 'Send' })).toBeDisabled()
  })

  it('sends the message as typed and clears the box', async () => {
    const user = userEvent.setup()
    seed()

    await user.type(screen.getByPlaceholderText(PROMPT), 'Who runs the mill?')
    await user.click(screen.getByRole('button', { name: 'Send' }))

    expect(sendDirectGMMessage).toHaveBeenCalledWith('Who runs the mill?')
    expect(screen.getByPlaceholderText(PROMPT)).toHaveValue('')
  })

  it('sends on Enter', async () => {
    const user = userEvent.setup()
    seed()

    await user.type(screen.getByPlaceholderText(PROMPT), 'Who runs the mill?{Enter}')

    expect(sendDirectGMMessage).toHaveBeenCalledWith('Who runs the mill?')
  })

  it('takes a newline on Shift+Enter instead of sending', async () => {
    const user = userEvent.setup()
    seed()
    const box = screen.getByPlaceholderText(PROMPT)

    await user.type(box, 'first{Shift>}{Enter}{/Shift}second')

    expect(sendDirectGMMessage).not.toHaveBeenCalled()
    expect(box).toHaveValue('first\nsecond')
  })

  it('does not send an empty message on Enter', async () => {
    const user = userEvent.setup()
    seed()

    await user.type(screen.getByPlaceholderText(PROMPT), '{Enter}')

    expect(sendDirectGMMessage).not.toHaveBeenCalled()
  })

  it('locks the box and shows progress while the GM is answering', async () => {
    let release
    sendDirectGMMessage = vi.fn(() => new Promise((r) => { release = r }))
    const user = userEvent.setup()
    seed()

    await user.type(screen.getByPlaceholderText(PROMPT), 'Who runs the mill?')
    await user.click(screen.getByRole('button', { name: 'Send' }))

    expect(screen.getByRole('button', { name: '...' })).toBeDisabled()
    expect(screen.getByPlaceholderText(PROMPT)).toBeDisabled()

    release()
    expect(await screen.findByRole('button', { name: 'Send' })).toBeInTheDocument()
  })

  it('keeps the message in the box when sending fails, so it is not lost', async () => {
    sendDirectGMMessage = vi.fn(async () => { throw new Error('engine offline') })
    vi.spyOn(console, 'error').mockImplementation(() => {})
    const user = userEvent.setup()
    seed()

    await user.type(screen.getByPlaceholderText(PROMPT), 'Who runs the mill?')
    await user.click(screen.getByRole('button', { name: 'Send' }))

    expect(screen.getByPlaceholderText(PROMPT)).toHaveValue('Who runs the mill?')
    // And the box is usable again rather than stuck disabled.
    expect(screen.getByPlaceholderText(PROMPT)).toBeEnabled()
  })

  it('shows no combat banner outside combat', () => {
    seed({ gameState: { mode: 'exploration' } })

    expect(screen.queryByText(/combat active/i)).not.toBeInTheDocument()
  })

  it('shows the combat banner and round in combat', () => {
    seed(inCombat({ round: 3 }))

    expect(screen.getByText(/combat active/i)).toBeInTheDocument()
    expect(screen.getByText('Round 3')).toBeInTheDocument()
  })

  it('shows the banner without a round when combat details are missing', () => {
    seed(inCombat(undefined))

    expect(screen.getByText(/combat active/i)).toBeInTheDocument()
    expect(screen.queryByText(/^Round/)).not.toBeInTheDocument()
  })

  it('credits the combat automation modules that are loaded', () => {
    seed({ ...inCombat({ round: 1 }), engineStatus: { modules: { 'midi-qol': {}, dae: {} } } })

    expect(screen.getByText(/MIDI QOL/)).toBeInTheDocument()
    expect(screen.getByText(/DAE/)).toBeInTheDocument()
  })

  it('credits only the module that is loaded', () => {
    seed({ ...inCombat({ round: 1 }), engineStatus: { modules: { 'midi-qol': {} } } })

    expect(screen.getByText(/MIDI QOL/)).toBeInTheDocument()
    expect(screen.queryByText(/DAE/)).not.toBeInTheDocument()
  })

  it('credits no modules when none are loaded', () => {
    seed(inCombat({ round: 1 }))

    expect(screen.queryByText(/MIDI QOL/)).not.toBeInTheDocument()
    expect(screen.queryByText(/DAE/)).not.toBeInTheDocument()
  })

  it('hides the chat behind the spoiler wall while play mode is on', () => {
    seed({ ...inPlayMode('Greenrest'), gmChatMessages: [{ role: 'assistant', content: 'A miller named Thaddeus.' }] })

    expect(screen.getByText(/play mode active/i)).toBeInTheDocument()
    expect(screen.queryByText('A miller named Thaddeus.')).not.toBeInTheDocument()
    expect(screen.queryByPlaceholderText(PROMPT)).not.toBeInTheDocument()
  })

  it('reveals the chat once the operator accepts the spoiler', async () => {
    const user = userEvent.setup()
    seed({ ...inPlayMode('Greenrest'), gmChatMessages: [{ role: 'assistant', content: 'A miller named Thaddeus.' }] })

    await user.click(screen.getByRole('button', { name: /show me/i }))

    expect(screen.getByText('A miller named Thaddeus.')).toBeInTheDocument()
  })

  // The chat is rendered twice — inside SpoilerWall and outside — so the
  // revealed copy's own handlers need driving as well.
  it('sends from the revealed copy, on Enter and on the button', async () => {
    const user = userEvent.setup()
    seed(inPlayMode('Greenrest'))
    await user.click(screen.getByRole('button', { name: /show me/i }))

    await user.type(screen.getByPlaceholderText(PROMPT), 'Who runs the mill?{Enter}')
    expect(sendDirectGMMessage).toHaveBeenCalledWith('Who runs the mill?')

    await user.type(screen.getByPlaceholderText(PROMPT), 'And the inn?')
    await user.click(screen.getByRole('button', { name: 'Send' }))
    expect(sendDirectGMMessage).toHaveBeenLastCalledWith('And the inn?')
  })

  it('takes a newline on Shift+Enter in the revealed copy too', async () => {
    const user = userEvent.setup()
    seed(inPlayMode('Greenrest'))
    await user.click(screen.getByRole('button', { name: /show me/i }))
    const box = screen.getByPlaceholderText(PROMPT)

    await user.type(box, 'first{Shift>}{Enter}{/Shift}second')

    expect(sendDirectGMMessage).not.toHaveBeenCalled()
    expect(box).toHaveValue('first\nsecond')
  })

  it('invites a first message in the revealed copy when the log is empty', async () => {
    const user = userEvent.setup()
    seed(inPlayMode('Greenrest'))

    await user.click(screen.getByRole('button', { name: /show me/i }))

    expect(screen.getByText(/start a conversation with the ai gm/i)).toBeInTheDocument()
  })

  it('does not wall the chat when play mode is on for another campaign', () => {
    seed({ ...inPlayMode('Greenrest'), playModeSessions: { Blackmoor: true } })

    expect(screen.queryByText(/play mode active/i)).not.toBeInTheDocument()
    expect(screen.getByPlaceholderText(PROMPT)).toBeInTheDocument()
  })
})
