import { request } from './client'

export const authApi = {
  login: (username, password) =>
    request('/api/auth/login', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ username, password }),
    }),

  getMe: () => request('/api/auth/me'),

  logout: () =>
    request('/api/auth/logout', {
      method: 'POST',
    }),
}
