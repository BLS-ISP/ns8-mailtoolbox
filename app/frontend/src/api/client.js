export class ApiError extends Error {
  constructor(message, status, detail = null, data = null) {
    super(message)
    this.name = 'ApiError'
    this.status = status
    this.detail = detail
    this.data = data
  }
}

let unauthorizedHandler = null

export function setUnauthorizedHandler(fn) {
  unauthorizedHandler = fn
}

async function request(url, options = {}, { skipAuthRedirect = false } = {}) {
  const headers = {
    Accept: 'application/json',
    ...(options.body ? { 'Content-Type': 'application/json' } : {}),
    ...(options.headers || {}),
  }

  let response
  try {
    response = await fetch(url, {
      credentials: 'same-origin',
      ...options,
      headers,
    })
  } catch (networkErr) {
    throw new ApiError(
      networkErr?.message || 'Network request failed',
      0,
      null,
      null
    )
  }

  const contentType = response.headers.get('content-type') || ''
  let data = null
  if (contentType.includes('application/json')) {
    try {
      data = await response.json()
    } catch {
      data = null
    }
  } else {
    try {
      const text = await response.text()
      data = text ? { detail: text } : null
    } catch {
      data = null
    }
  }

  if (!response.ok) {
    const detail =
      typeof data?.detail === 'string'
        ? data.detail
        : typeof data?.error === 'string'
          ? data.error
          : Array.isArray(data?.detail)
            ? data.detail.map((d) => d?.msg || JSON.stringify(d)).join('; ')
            : null

    if (response.status === 401 && !skipAuthRedirect && unauthorizedHandler) {
      unauthorizedHandler()
    }

    throw new ApiError(
      detail || `HTTP ${response.status}`,
      response.status,
      detail,
      data
    )
  }

  return data
}

export const api = {
  getMe() {
    return request('/api/auth/me', { method: 'GET' }, { skipAuthRedirect: true })
  },

  login(username, password) {
    return request(
      '/api/auth/login',
      {
        method: 'POST',
        body: JSON.stringify({ username, password }),
      },
      { skipAuthRedirect: true }
    )
  },

  logout() {
    return request(
      '/api/auth/logout',
      { method: 'POST' },
      { skipAuthRedirect: true }
    )
  },

  getMailboxes() {
    return request('/api/mailboxes', { method: 'GET' })
  },

  updateVacation(user, payload) {
    return request(`/api/mailboxes/${encodeURIComponent(user)}/vacation`, {
      method: 'PUT',
      body: JSON.stringify(payload),
    })
  },

  getTemplates() {
    return request('/api/templates', { method: 'GET' })
  },

  getAuditLog() {
    return request('/api/audit', { method: 'GET' })
  },

  searchMailTracking(filters = {}) {
    return request('/api/mail-tracking/search', {
      method: 'POST',
      body: JSON.stringify({
        sender: filters.sender || '',
        recipient: filters.recipient || '',
        subject: filters.subject || '',
        action_filter: filters.action_filter || 'all',
        date_from: filters.date_from || '',
        date_to: filters.date_to || '',
        limit: filters.limit ?? 100,
      }),
    })
  },

  inspectQuarantineToken(token) {
    return request(
      `/api/quarantine/token/${encodeURIComponent(token)}`,
      { method: 'GET' },
      { skipAuthRedirect: true }
    )
  },

  executeQuarantineAction(token, op) {
    return request(
      '/api/quarantine/action',
      {
        method: 'POST',
        body: JSON.stringify({ token, op }),
      },
      { skipAuthRedirect: true }
    )
  },
}
