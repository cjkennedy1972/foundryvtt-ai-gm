import { render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { describe, expect, it } from 'vitest'

import SpoilerWall from './SpoilerWall.jsx'

const HIDDEN = 'the lich is the mayor'

describe('SpoilerWall', () => {
  it('does not render its children until revealed', () => {
    render(<SpoilerWall><p>{HIDDEN}</p></SpoilerWall>)

    // The load-bearing assertion: the spoiler must not be in the DOM at all,
    // not merely visually covered. Anything rendered-but-hidden is one
    // devtools inspection away from being read at the table.
    expect(screen.queryByText(HIDDEN)).not.toBeInTheDocument()
  })

  it('explains why it is in the way', () => {
    render(<SpoilerWall><p>{HIDDEN}</p></SpoilerWall>)

    expect(screen.getByRole('heading', { name: /play mode active/i })).toBeInTheDocument()
  })

  it('names the kind of content it is hiding', () => {
    render(<SpoilerWall label="every villain's true identity"><p>{HIDDEN}</p></SpoilerWall>)

    expect(screen.getByText(/every villain's true identity/)).toBeInTheDocument()
  })

  it('falls back to a generic label', () => {
    render(<SpoilerWall><p>{HIDDEN}</p></SpoilerWall>)

    expect(screen.getByText(/spoiler content/)).toBeInTheDocument()
  })

  it('reveals the children when the operator accepts', async () => {
    const user = userEvent.setup()
    render(<SpoilerWall><p>{HIDDEN}</p></SpoilerWall>)

    await user.click(screen.getByRole('button', { name: /i understand, show me/i }))

    expect(screen.getByText(HIDDEN)).toBeInTheDocument()
  })

  it('drops the wall entirely once revealed, rather than leaving it around', async () => {
    const user = userEvent.setup()
    render(<SpoilerWall><p>{HIDDEN}</p></SpoilerWall>)

    await user.click(screen.getByRole('button', { name: /i understand, show me/i }))

    expect(screen.queryByRole('heading', { name: /play mode active/i })).not.toBeInTheDocument()
    expect(screen.queryByRole('button', { name: /i understand/i })).not.toBeInTheDocument()
  })

  it('stays revealed across a re-render', async () => {
    const user = userEvent.setup()
    const { rerender } = render(<SpoilerWall><p>{HIDDEN}</p></SpoilerWall>)
    await user.click(screen.getByRole('button', { name: /i understand, show me/i }))

    rerender(<SpoilerWall><p>{HIDDEN}</p></SpoilerWall>)

    expect(screen.getByText(HIDDEN)).toBeInTheDocument()
  })

  it('renders multiple children once revealed', async () => {
    const user = userEvent.setup()
    render(
      <SpoilerWall>
        <p>first secret</p>
        <p>second secret</p>
      </SpoilerWall>,
    )

    await user.click(screen.getByRole('button', { name: /i understand, show me/i }))

    expect(screen.getByText('first secret')).toBeInTheDocument()
    expect(screen.getByText('second secret')).toBeInTheDocument()
  })
})
