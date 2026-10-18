import { fireEvent, render, screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import SourceManagementView from './SourceManagementView'

const metrics = { window_days: 30, collected_count: 14, analyzed_count: 8, radar_count: 3, pending_count: 6, failed_count: 0, endpoint_count: 1, enabled_endpoint_count: 1, healthy_endpoint_count: 1, unhealthy_endpoint_count: 0, health_status: 'healthy', needs_attention: false, last_success_at: '2026-10-17T01:00:00Z', last_error: null, sample_items: [{ id: 1, title: '真实测试实践', url: 'https://example.com/testing' }] }
const source = { id: '1', name: 'Testing Blog', source_type: 'website', homepage_url: 'https://example.com', description: '测试工程实践', languages: ['en'], topics: ['testing'], trust_level: 3, status: 'active', metrics }
const preferences = { enabled: true, topics: ['testing'], languages: ['zh', 'en'], excluded_terms: [], max_trial_sources: 3, next_run_at: '2026-10-18T01:00:00Z', last_completed_at: null, last_error: null, scope_note: '从可信目录与已采集内容的真实链接中发现，当前不覆盖全网。' }
const candidate = { id: 3, name: 'New Testing Blog', homepage_url: 'https://new.example.com', feed_url: 'https://new.example.com/feed', topics: ['testing'], language: 'en', discovery_kind: 'catalog', evidence_url: 'https://new.example.com', reason: '补充接口测试实践', status: 'trial', sample_count: 1, relevant_count: 0, analysis_count: 0, last_checked_at: '2026-10-17T01:00:00Z', next_check_at: null, last_error: null, samples: [{ title: '接口回归样例', url: 'https://new.example.com/article', summary: '真实文章摘要', published_at: null, analysis_status: 'pending', analysis_summary: null, testing_relevance_score: null, testing_value_score: null, matched_topics: ['testing'] }] }

function setup(role: 'maintainer' | 'viewer' | 'admin' = 'maintainer', kind = 'rss') {
  let sources = [source]
  const request = vi.fn(async (path: string, options?: RequestInit) => {
    if (path === '/api/v1/source-exploration/preferences') return options?.method === 'PUT' ? { ...preferences, ...JSON.parse(String(options.body)) } : preferences
    if (path.startsWith('/api/v1/source-exploration/candidates')) return options?.method === 'POST' ? { candidate: { ...candidate, status: 'promoted' }, source_id: 7 } : { items: [{ ...candidate, endpoint_type: kind, evaluation: { recommendation: '继续观察实际证据', relevant_ratio: null, average_value: null, check_count: 1, success_count: 1 }, status: path.includes('status=dismissed') ? 'dismissed' : candidate.status }], total: 1 }
    if (path.endsWith('/lifecycle')) { sources = sources.map(s => ({ ...s, status: JSON.parse(String(options?.body)).action === 'pause' ? 'paused' : 'active' })); return sources[0] }
    return { items: sources, total: 42, summary: { total: 42, active: 30, paused: 8, archived: 3, draft: 1, needs_attention: 2 } }
  })
  const actions = { onAdd: vi.fn(), onDiscover: vi.fn(), onManualContent: vi.fn(), onEndpoints: vi.fn(), onEdit: vi.fn(), onDelete: vi.fn() }
  render(<SourceManagementView request={request as never} canManage={role !== 'viewer'} canDelete={role === 'admin'} refreshKey={0} {...actions} />)
  return { request, actions }
}

describe('source management workspace', () => {
  beforeEach(() => vi.restoreAllMocks())

  it('reviews standalone articles as one-time admission without a subscription action', async () => {
    const { request } = setup('maintainer', 'article')
    await userEvent.click(await screen.findByRole('tab', { name: '发现与试运行' }))
    await userEvent.click(await screen.findByRole('button', { name: '查看样例与评估' }))
    const detail = within(await screen.findByRole('dialog', { name: 'New Testing Blog · 试运行评估' }))
    expect(detail.getByRole('link', { name: '原始内容' })).toBeInTheDocument()
    expect(detail.queryByRole('button', { name: '确认正式启用' })).not.toBeInTheDocument()
    await userEvent.click(detail.getByRole('button', { name: '确认收录此内容' }))
    const confirmation = within((await screen.findByText('收录「New Testing Blog」？')).closest('[role="dialog"]') as HTMLElement)
    expect(confirmation.getByText(/不创建持续采集任务/)).toBeInTheDocument()
    await userEvent.click(confirmation.getByRole('button', { name: '确认收录' }))
    await waitFor(() => expect(request).toHaveBeenCalledWith('/api/v1/source-exploration/candidates/3/promote', { method: 'POST' }))
  })

  it('labels a monitored webpage correctly and shows the evidence-based evaluation', async () => {
    setup('maintainer', 'web_listing')
    await userEvent.click(await screen.findByRole('tab', { name: '发现与试运行' }))
    expect(await screen.findByText('网页列表')).toBeInTheDocument()
    await userEvent.click(screen.getByRole('button', { name: '查看样例与评估' }))
    const detail = within(await screen.findByRole('dialog', { name: 'New Testing Blog · 试运行评估' }))
    expect(detail.getByRole('link', { name: '持续跟踪页面' })).toBeInTheDocument()
    expect(detail.getByText('继续观察实际证据')).toBeInTheDocument()
  })

  it('uses server filters and pagination while showing evidence and analysis backlog separately', async () => {
    const { request } = setup()
    await screen.findByRole('heading', { name: 'Testing Blog' })
    expect(screen.getByText('已分析 8')).toBeInTheDocument()
    expect(screen.getByText('入选 3')).toBeInTheDocument()
    expect(screen.getByText('待分析 6')).toBeInTheDocument()
    fireEvent.change(screen.getByRole('textbox', { name: '搜索信源' }), { target: { value: 'example.com' } })
    await userEvent.click(screen.getByRole('button', { name: '筛选信源' }))
    await waitFor(() => expect(request).toHaveBeenCalledWith(expect.stringMatching(/q=example.com/)))
    await userEvent.click(screen.getByTitle('2'))
    await waitFor(() => expect(request).toHaveBeenCalledWith(expect.stringMatching(/offset=20/)))
    await userEvent.click(screen.getByRole('tab', { name: /需处理/ }))
    await waitFor(() => expect(request).toHaveBeenCalledWith(expect.stringMatching(/needs_attention=true/)))
  })

  it('pauses without deleting history and requires confirmation to archive', async () => {
    const { request } = setup()
    const card = within((await screen.findByRole('heading', { name: 'Testing Blog' })).closest('article')!)
    await userEvent.click(card.getByRole('button', { name: '暂停监听' }))
    await waitFor(() => expect(request).toHaveBeenCalledWith('/api/v1/sources/1/lifecycle', expect.objectContaining({ method: 'POST', body: JSON.stringify({ action: 'pause' }) })))
    expect(await screen.findByRole('button', { name: '恢复监听' })).toBeInTheDocument()
    await userEvent.click(card.getByRole('button', { name: '归档信源' }))
    const confirmation = within(await screen.findByRole('dialog', { name: '归档信源「Testing Blog」？' }))
    expect(confirmation.getByText(/保留历史内容/)).toBeInTheDocument()
    expect(request.mock.calls.some(([, opts]) => opts?.method === 'DELETE')).toBe(false)
  })

  it('shows real trial samples and explicitly asks before enabling a discovered source', async () => {
    const { request } = setup()
    await screen.findByRole('heading', { name: 'Testing Blog' })
    await userEvent.click(screen.getByRole('tab', { name: '发现与试运行' }))
    await screen.findByRole('heading', { name: 'New Testing Blog' })
    expect(screen.getByText('补充接口测试实践')).toBeInTheDocument()
    expect(screen.getByText(/试运行内容不会进入正式邮件/)).toBeInTheDocument()
    await userEvent.click(screen.getByRole('button', { name: '查看样例与评估' }))
    const detail = within(await screen.findByRole('dialog', { name: 'New Testing Blog · 试运行评估' }))
    expect(detail.getByRole('link', { name: '接口回归样例' })).toHaveAttribute('href', 'https://new.example.com/article')
    expect(detail.getByText('待分析')).toBeInTheDocument()
    expect(detail.queryByText('价值 0')).not.toBeInTheDocument()
    await userEvent.click(detail.getByRole('button', { name: '确认正式启用' }))
    // Ant Design uses a shared test-only generated ID when a drawer and modal coexist.
    const confirmation = within((await screen.findByText('正式启用「New Testing Blog」？')).closest('[role="dialog"]') as HTMLElement)
    expect(request.mock.calls.some(([path]) => path.endsWith('/promote'))).toBe(false)
    await userEvent.click(confirmation.getByRole('button', { name: '确认启用' }))
    await waitFor(() => expect(request).toHaveBeenCalledWith('/api/v1/source-exploration/candidates/3/promote', { method: 'POST' }))
  })

  it('keeps direction edits as a reversible draft and saves only when asked', async () => {
    const { request } = setup()
    await screen.findByRole('heading', { name: 'Testing Blog' })
    await userEvent.click(screen.getByRole('button', { name: '管理关注方向' }))
    const editor = within(await screen.findByRole('dialog', { name: '平台关注方向' }))
    await userEvent.click(editor.getByRole('button', { name: '更多操作 testing' }))
    await userEvent.click(await screen.findByRole('menuitem', { name: /移除主题/ }))
    expect(editor.getByText('待移除')).toBeInTheDocument()
    expect(request.mock.calls.some(([, opts]) => opts?.method === 'PUT')).toBe(false)
    await userEvent.click(editor.getByRole('button', { name: '撤销移除 testing' }))
    fireEvent.change(editor.getByRole('textbox', { name: '新增关注主题' }), { target: { value: '接口自动化' } })
    await userEvent.click(editor.getByRole('button', { name: /添加主题/ }))
    await userEvent.click(editor.getByRole('button', { name: '保存关注方向' }))
    await waitFor(() => expect(request).toHaveBeenCalledWith('/api/v1/source-exploration/preferences', expect.objectContaining({ method: 'PUT', body: expect.stringContaining('接口自动化') })))
  })

  it('keeps maintenance and candidate promotion hidden for viewers', async () => {
    setup('viewer')
    await screen.findByRole('heading', { name: 'Testing Blog' })
    expect(screen.queryByRole('button', { name: '管理关注方向' })).not.toBeInTheDocument()
    expect(screen.queryByRole('button', { name: '暂停监听' })).not.toBeInTheDocument()
    expect(screen.queryByRole('button', { name: '通过网址添加' })).not.toBeInTheDocument()
    await userEvent.click(screen.getByRole('tab', { name: '发现与试运行' }))
    await userEvent.click(await screen.findByRole('button', { name: '查看样例与评估' }))
    expect(screen.queryByRole('button', { name: '确认正式启用' })).not.toBeInTheDocument()
  })

  it('lets users review and restore an ignored source instead of losing the decision history', async () => {
    const { request } = setup()
    await screen.findByRole('heading', { name: 'Testing Blog' })
    await userEvent.click(screen.getByRole('tab', { name: '发现与试运行' }))
    await userEvent.click(screen.getByRole('button', { name: '查看已忽略' }))
    await waitFor(() => expect(request).toHaveBeenCalledWith(expect.stringContaining('status=dismissed')))
    await userEvent.click(await screen.findByRole('button', { name: '恢复试运行' }))
    await waitFor(() => expect(request).toHaveBeenCalledWith('/api/v1/source-exploration/candidates/3/action', expect.objectContaining({ method: 'POST', body: JSON.stringify({ action: 'resume' }) })))
  })

  it('preserves the direction draft and shows the error inside the drawer when saving fails', async () => {
    const { request } = setup()
    await screen.findByRole('heading', { name: 'Testing Blog' })
    await userEvent.click(screen.getByRole('button', { name: '管理关注方向' }))
    const editor = within(await screen.findByRole('dialog', { name: '平台关注方向' }))
    fireEvent.change(editor.getByRole('textbox', { name: '新增关注主题' }), { target: { value: '接口自动化' } })
    await userEvent.click(editor.getByRole('button', { name: /添加主题/ }))
    request.mockRejectedValueOnce(new Error('保存失败，请重试。'))
    await userEvent.click(editor.getByRole('button', { name: '保存关注方向' }))
    expect(await editor.findByText('保存失败，请重试。')).toBeInTheDocument()
    expect(editor.getByRole('list', { name: '关注主题列表' })).toHaveTextContent('接口自动化')
    expect(editor.getByRole('button', { name: '保存关注方向' })).toBeEnabled()
  })
})
