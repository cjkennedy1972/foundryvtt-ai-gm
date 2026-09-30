/**
 * Renders a page against the real zustand store with a chosen slice of state.
 *
 * The pages read the store through `useStore()`, so a test either mocks the
 * module or seeds the real store. Seeding is preferable: it exercises the
 * selectors and the action wiring the page actually depends on, so a page that
 * reads a field nobody sets still fails. Actions are replaced with spies,
 * since a page test should not reach the network.
 */
import { render } from '@testing-library/react'

import { useStore } from '../store.js'

const pristine = { ...useStore.getState() }

/** Restore the store between tests. Call from an afterEach. */
export function resetStore() {
  useStore.setState(pristine, true)
}

/** Seed state and/or replace actions, then render. */
export function renderWithStore(ui, overrides = {}) {
  useStore.setState({ ...pristine, ...overrides }, true)
  return render(ui)
}

export { useStore, pristine }
