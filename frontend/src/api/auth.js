import { request } from './client'

export const authApi = {
  login: (username, password) =>
    request('/api/auth/login', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ username, password }),
    }),

  getMe: () => request('/api/auth/me'),

  changePassword: (currentPassword, newPassword) =>
    request('/api/auth/change-password', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ current_password: currentPassword, new_password: newPassword }),
    }),

  logout: () =>
    request('/api/auth/logout', {
      method: 'POST',
    }),

  uploadAvatar: (file) => {
    const formData = new FormData()
    formData.append('file', file)
    return request('/api/auth/avatar', { method: 'POST', body: formData })
  },

  setAvatarUrl: (url) => {
    const formData = new FormData()
    formData.append('url', url)
    return request('/api/auth/avatar', { method: 'POST', body: formData })
  },

  resetAvatar: () => request('/api/auth/avatar', { method: 'DELETE' }),
}
