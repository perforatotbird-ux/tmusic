import { create } from 'zustand'
import { authApi } from '../api/auth'
import { createLogger } from '../utils/logger'

const log = createLogger('auth')

export const useAuthStore = create((set, get) => ({
  user: null,
  isAuthenticated: false,
  isAuthModalOpen: true,
  isLoading: false,
  error: null,

  setAuthModalOpen: (isOpen) => {
    log.debug('auth modal', { open: isOpen })
    set({ isAuthModalOpen: isOpen })
  },

  loginWithPassword: async (username, password) => {
    set({ isLoading: true, error: null })
    log.info('login attempt', { username })
    try {
      await authApi.login(username, password)
      log.info('login success', { username })
      set({
        isAuthenticated: true,
        isAuthModalOpen: false,
        isLoading: false,
        error: null,
      })
      await get().checkAuth()
      return true
    } catch (err) {
      // Never log the password — only outcome.
      log.warn('login failed', { username, reason: err?.message })
      set({ isLoading: false, error: err.message || 'Login failed' })
      return false
    }
  },

  checkAuth: async () => {
    try {
      const me = await authApi.getMe()
      log.debug('session valid', { username: me?.username, role: me?.role })
      set({ user: me, isAuthenticated: true, isAuthModalOpen: false })
    } catch {
      log.debug('no active session')
      set({ user: null, isAuthenticated: false, isAuthModalOpen: true })
    }
  },

  logout: async () => {
    log.info('logout', { username: get().user?.username })
    try {
      await authApi.logout()
    } catch {
      // Ignore network errors — still clear local session state
    }
    set({ user: null, isAuthenticated: false, isAuthModalOpen: true })
  },
}))
