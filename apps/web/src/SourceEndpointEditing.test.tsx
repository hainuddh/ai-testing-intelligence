import { fireEvent, render, screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { afterEach, expect, it, vi } from 'vitest'
import App from './App'

afterEach(() => vi.restoreAllMocks())

it('edits and pauses an endpoint without deleting its collection history', async () => {
  localStorage.setItem('access_token', 'maintainer-token')
  const source = { id: '7', name: 'Quality source', source_type: 'website', status: 'active', homepage_url: 'https://quality.example', description: '', languages: ['en'], trust_level: 3, topics: ['testing'] }
  let endpoint = { id: '9', source_id: '7', name: 'Official feed', endpoint_type: 'rss', url: 'https://quality.example/feed', fetch_interval_minutes: 360, max_items_per_run: 50, enabled: true, health_status: 'healthy' }
  const fetchMock = vi.spyOn(globalThis, 'fetch').mockImplementation(async (input, init) => {
    const path = String(input)
    let data: unknown = { items: [], total: 0 }
    if (path === '/api/v1/auth/me') data = { id: '1', username: 'maintainer', role: 'maintainer' }
    if (path.startsWith('/api/v1/sources?')) data = { items: [source], total: 1 }
    if (path === '/api/v1/source-exploration/preferences') data = { enabled: true, topics: [], languages: ['en'], excluded_terms: [], max_trial_sources: 3 }
    if (path === '/api/v1/sources/7/endpoints') data = [endpoint]
    if (path === '/api/v1/sources/7/endpoints/9' && init?.method === 'PATCH') { endpoint = { ...endpoint, ...JSON.parse(String(init.body)) }; data = endpoint }
    return new Response(JSON.stringify(data), { status: 200, headers: { 'Content-Type': 'application/json' } })
  })
  render(<App />)
  await screen.findByRole('heading', { name: '内容情报' })
  await userEvent.click(screen.getByRole('button', { name: '信源管理' }))
  await screen.findByRole('heading', { name: 'Quality source' })
  await userEvent.click(screen.getByRole('button', { name: /采集端点/ }))
  const drawer = within(await screen.findByRole('dialog'))
  await userEvent.click(drawer.getByRole('button', { name: '编辑端点 Official feed' }))
  fireEvent.change(drawer.getByRole('textbox', { name: '端点名称' }), { target: { value: 'Updated feed' } })
  fireEvent.change(drawer.getByRole('spinbutton', { name: '采集间隔（分钟）' }), { target: { value: '720' } })
  await userEvent.click(drawer.getByRole('button', { name: '保存端点修改' }))
  await waitFor(() => expect(fetchMock).toHaveBeenCalledWith('/api/v1/sources/7/endpoints/9', expect.objectContaining({ method: 'PATCH', body: expect.stringContaining('Updated feed') })))
  await userEvent.click(await drawer.findByRole('switch', { name: '启用端点 Updated feed' }))
  await waitFor(() => expect(fetchMock).toHaveBeenCalledWith('/api/v1/sources/7/endpoints/9', expect.objectContaining({ method: 'PATCH', body: JSON.stringify({ enabled: false }) })))
  expect(fetchMock.mock.calls.some(([, options]) => options?.method === 'DELETE')).toBe(false)
})
