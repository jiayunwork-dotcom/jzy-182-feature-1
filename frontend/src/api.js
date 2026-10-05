// Thin backend client. All planning/projection math lives on the server.

async function request(path, options = {}) {
  const res = await fetch(path, {
    headers: { 'Content-Type': 'application/json' },
    ...options,
  })
  const body = await res.json().catch(() => ({}))
  if (!res.ok) {
    const err = new Error(`HTTP ${res.status}`)
    err.body = body
    throw err
  }
  return body
}

export const api = {
  rule: () => request('/api/rule'),
  listProjects: () => request('/api/projects'),
  getProject: (id) => request(`/api/projects/${id}`),
  createProject: (payload) =>
    request('/api/projects', { method: 'POST', body: JSON.stringify(payload) }),
  submitReport: (id, payload) =>
    request(`/api/projects/${id}/reports`, {
      method: 'POST',
      body: JSON.stringify(payload),
    }),
  listReports: (id) => request(`/api/projects/${id}/reports`),
  state: (id, at) =>
    request(`/api/projects/${id}/state${at ? `?at=${encodeURIComponent(at)}` : ''}`),
  trajectory: (id) => request(`/api/projects/${id}/trajectory`),
  listPortfolios: () => request('/api/portfolios'),
  getPortfolio: (id) => request(`/api/portfolios/${id}`),
  createPortfolio: (payload) =>
    request('/api/portfolios', { method: 'POST', body: JSON.stringify(payload) }),
  updatePortfolio: (id, payload) =>
    request(`/api/portfolios/${id}`, { method: 'PUT', body: JSON.stringify(payload) }),
  joinPortfolio: (id, projectId) =>
    request(`/api/portfolios/${id}/projects`, {
      method: 'POST',
      body: JSON.stringify({ project_id: projectId }),
    }),
}
