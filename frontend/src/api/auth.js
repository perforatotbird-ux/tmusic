import { request } from './client'

export const authApi = {
  login: (username, password) =>
    request('/api/auth/login', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ username, password }),
    }),

  pinLogin: (pin) =>
    request('/api/auth/pin', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ pin }),
    }),

  getMe: () => request('/api/auth/me'),

  logout: () =>
    request('/api/auth/logout', {
      method: 'POST',
    }),
}
