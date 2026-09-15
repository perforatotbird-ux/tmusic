import { create } from 'zustand'
import { authApi } from '../api/auth'

export const useAuthStore = create((set, get) => ({
  user: null,
  isAuthenticated: false,
  isAuthModalOpen: true,
  isLoading: false,
  error: null,

  setAuthModalOpen: (isOpen) => set({ isAuthModalOpen: isOpen }),

  loginWithPin: async (pin) => {
    set({ isLoading: true, error: null })
    try {
      await authApi.pinLogin(pin)
      set({
        isAuthenticated: true,
        isAuthModalOpen: false,
        isLoading: false,
        error: null,
      })
      await get().checkAuth()
      return true
    } catch (err) {
      set({ isLoading: false, error: err.message || 'Incorrect PIN code' })
      return false
    }
  },

  loginWithPassword: async (username, password) => {
    set({ isLoading: true, error: null })
    try {
      await authApi.login(username, password)
      set({
        isAuthenticated: true,
        isAuthModalOpen: false,
        isLoading: false,
        error: null,
      })
      await get().checkAuth()
      return true
    } catch (err) {
      set({ isLoading: false, error: err.message || 'Login failed' })
      return false
    }
  },

  checkAuth: async () => {
    try {
      const me = await authApi.getMe()
      set({ user: me, isAuthenticated: true, isAuthModalOpen: false })
    } catch {
      set({ user: null, isAuthenticated: false, isAuthModalOpen: true })
    }
  },

  logout: async () => {
    try {
      await authApi.logout()
    } catch {
      // Ignore network errors — still clear local session state
    }
    set({ user: null, isAuthenticated: false, isAuthModalOpen: true })
  },
}))
