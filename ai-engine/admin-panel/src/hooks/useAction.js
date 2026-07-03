import { useState, useCallback } from 'react'

/**
 * Loading/result/error state machine for a single async action, e.g. a
 * "Teardown campaign" or "Extend arc" button. Collapses the repeated
 * `{loading, result, error}` + setState-before/setState-after shape that
 * used to be copy-pasted per action (CampaignList.jsx had four copies).
 *
 * `fn` must resolve to `{ok: true, data}` or `{ok: false, error}` — the
 * shape every store action already returns.
 *
 * Usage:
 *   const [teardownState, runTeardown, resetTeardown] = useAction()
 *   const handleTeardown = () => runTeardown(() => teardownCampaign(selected))
 *   ...
 *   {teardownState.loading && <Spinner />}
 *   {teardownState.error && <Alert message={teardownState.error} />}
 */
export function useAction(initialExtra = {}) {
  const [state, setState] = useState({ loading: false, result: null, error: '', ...initialExtra })

  const run = useCallback(async (fn, { fallbackError = 'Action failed' } = {}) => {
    setState((s) => ({ ...s, loading: true, error: '', result: null }))
    const result = await fn()
    setState((s) => ({
      ...s,
      loading: false,
      result: result.ok ? result.data : null,
      error: result.ok ? '' : (result.error || fallbackError),
    }))
    return result
  }, [])

  const reset = useCallback((extra = {}) => {
    setState({ loading: false, result: null, error: '', ...initialExtra, ...extra })
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [])

  const patch = useCallback((extra) => {
    setState((s) => ({ ...s, ...extra }))
  }, [])

  return [state, run, reset, patch]
}
