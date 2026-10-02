import { create } from 'zustand'
import { authApi } from '../api/auth'
import { usePlayerStore } from './playerStore'
import { useFavoritesStore } from './favoritesStore'
import { createLogger } from '../utils/logger'
import { bustCache } from '../api/cache'

const log = createLogger('auth')
const LOGOUT_MARKER = 'tmusic_logout_unconfirmed'
const LOGOUT_WARNING = 'Серверный выход не подтверждён. Сессия может оставаться действующей. Повторите выход.'
function logoutMarker(value) {
  try {
    if (value === undefined) return typeof sessionStorage !== 'undefined' && sessionStorage.getItem(LOGOUT_MARKER) === '1'
    if (value) sessionStorage.setItem(LOGOUT_MARKER, '1')
    else sessionStorage.removeItem(LOGOUT_MARKER)
  } catch { /* Storage is optional; in-memory warning remains authoritative. */ }
  return false
}
const initiallyPendingLogout = logoutMarker()
let epoch = 0
let checkSequence = 0
let mutationTail = Promise.resolve()
function mutateSession(operation) {
  const result = mutationTail.then(operation, operation)
  mutationTail = result.catch(() => {})
  return result
}

export const useAuthStore = create((set, get) => ({
  user: null, isAuthenticated: false, isAuthModalOpen: true, isLoading: false,
  logoutPending: initiallyPendingLogout, logoutBusy: false,
  error: initiallyPendingLogout ? LOGOUT_WARNING : null,
  setAuthModalOpen: (isOpen) => set({ isAuthModalOpen: isOpen }),
  loginWithPassword: async (username, password) => {
    get().clearLocalSession()
    const generation = epoch
    set({ isLoading: true, error: null })
    return mutateSession(async () => {
      if (generation !== epoch) return false
      if (get().logoutPending) {
        set({ error: LOGOUT_WARNING, isLoading: false })
        return false
      }
      try {
        log.info('login attempt', { username })
        await authApi.login(username, password)
        if (generation !== epoch) return false
        const authenticated = await get().checkAuth({ skipMutationWait: true })
        if (authenticated) log.info('login success', { username })
        return authenticated
      } catch (err) {
        if (generation === epoch) {
          log.warn('login failed', { username, reason: err?.message })
          set({ error: err?.message || 'Login failed' })
        }
        return false
      } finally {
        if (generation === epoch) set({ isLoading: false })
      }
    })
  },
  checkAuth: async ({ skipMutationWait = false } = {}) => {
    const generation = epoch
    if (!skipMutationWait) await mutationTail
    if (generation !== epoch || get().logoutPending) return false
    const sequence = ++checkSequence
    try {
      const me = await authApi.getMe()
      if (generation !== epoch || sequence !== checkSequence) return false
      set({ user: me, isAuthenticated: true, isAuthModalOpen: false })
      if (!me.password_change_required) usePlayerStore.getState().hydrate()
      return true
    } catch (err) {
      // UI-07: a transient network failure or 5xx must NOT wipe an active session
      // and kill playback. Only a 401 means the credentials are no longer valid.
      if (err?.code === 'authentication_required') {
        if (generation === epoch && sequence === checkSequence) get().clearLocalSession()
        return false
      }
      // network_error / server_error: preserve the session and surface a
      // recoverable notice instead of silently destroying the player.
      if (generation === epoch && sequence === checkSequence) {
        usePlayerStore.getState().pushNotice({
          kind: 'warn',
          text: 'Нет связи с сервером. Воспроизведение продолжается, если доступно офлайн.',
        })
      }
      return false
    }
  },
  clearLocalSession: () => {
    epoch += 1
    checkSequence += 1
    usePlayerStore.getState().reset()
    useFavoritesStore.getState().reset()
    bustCache('')
    set({ user: null, isAuthenticated: false, isAuthModalOpen: true, isLoading: false, error: null })
  },
  logout: async () => {
    if (get().logoutBusy) return { ok: false, code: 'logout_in_progress' }
    get().clearLocalSession() // Invalidate pending replies immediately.
    logoutMarker(true)
    set({ logoutBusy: true })
    return mutateSession(async () => {
      try {
        await authApi.logout()
        logoutMarker(false)
        set({ logoutPending: false, error: null })
        return { ok: true }
      } catch (error) {
        log.warn('Server logout was not confirmed', { code: error?.code || 'logout_failed' })
        set({ logoutPending: true, error: LOGOUT_WARNING, isAuthModalOpen: true })
        return { ok: false, code: 'logout_unconfirmed', message: LOGOUT_WARNING }
      } finally {
        set({ logoutBusy: false })
      }
    })
  },
}))
