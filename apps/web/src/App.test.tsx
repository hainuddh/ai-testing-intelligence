import { fireEvent, render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import App from './App'

const source = {
  id: 'source-1',
  name: 'OpenAI Research',
  source_type: 'website',
  homepage_url: 'https://openai.com/research',
  description: 'Frontier AI research and releases',
  languages: ['en'],
  trust_level: 5,
  topics: ['foundation-models'],
}

const json = (data: unknown, status = 200) => new Response(JSON.stringify(data), {
  status,
  headers: { 'Content-Type': 'application/json' },
})

describe('management radar workflow', () => {
  beforeEach(() => {
    vi.restoreAllMocks()
  })

  it('logs in, resolves the profile, and opens the content feed', async () => {
    const user = userEvent.setup()
    const fetchMock = vi.spyOn(globalThis, 'fetch')
      .mockResolvedValueOnce(json({ access_token: 'radar-token' }))
      .mockResolvedValueOnce(json({ id: 'user-1', username: 'analyst', role: 'viewer' }))
      .mockResolvedValueOnce(json({ items: [], total: 0 }))

    render(<App />)
    await user.type(screen.getByLabelText('用户名'), 'analyst')
    await user.type(screen.getByLabelText('密码'), 'secret-pass')
    await user.click(screen.getByRole('button', { name: '进入雷达' }))

    expect(await screen.findByRole('heading', { name: '内容情报' })).toBeInTheDocument()
    expect(localStorage.getItem('access_token')).toBe('radar-token')
    expect(fetchMock).toHaveBeenNthCalledWith(1, '/api/v1/auth/login', expect.objectContaining({
      method: 'POST',
      body: JSON.stringify({ username: 'analyst', password: 'secret-pass' }),
    }))
    expect(fetchMock).toHaveBeenNthCalledWith(2, '/api/v1/auth/me', expect.objectContaining({
      headers: expect.objectContaining({ Authorization: 'Bearer radar-token' }),
    }))
    expect(screen.queryByRole('button', { name: '用户管理' })).not.toBeInTheDocument()
  })

  it('filters intelligence by query and date range', async () => {
    localStorage.setItem('access_token', 'existing-token')
    const item = {
      id: 1, source_id: 1, source_name: 'Testing Lab', title: 'Agents gain new tools', url: 'https://example.com/agents',
      summary: 'Original summary', body: '', published_at: '2026-08-20T00:00:00Z', fetched_at: '2026-08-21T00:00:00Z',
      analysis_status: 'analyzed', testing_relevance_score: 88, testing_value_score: 92,
      analysis_summary: 'A concise testing intelligence summary', testing_value_analysis: 'Useful for autonomous regression testing.',
      applicable_scenarios: ['Regression testing'], adoption_suggestions: ['Start with a controlled pilot'],
      analysis_risks: ['False positives'], analysis_tags: ['AI Agent', 'Testing'], analysis_model: 'test-model', analyzed_at: '2026-08-21T01:00:00Z',
    }
    const fetchMock = vi.spyOn(globalThis, 'fetch')
      .mockResolvedValueOnce(json({ id: 'user-1', username: 'reader', role: 'viewer' }))
      .mockResolvedValueOnce(json({ items: [item], total: 1 }))
      .mockResolvedValueOnce(json({ items: [item], total: 1 }))
      .mockResolvedValueOnce(json({ items: [item], total: 1 }))

    render(<App />)
    expect(await screen.findByText('Agents gain new tools')).toBeInTheDocument()
    const search = screen.getByLabelText('检索内容')
    await userEvent.type(search, 'agents')
    await userEvent.type(search, '{Enter}')

    await waitFor(() => expect(fetchMock).toHaveBeenLastCalledWith(
      '/api/v1/content?offset=0&limit=12&query=agents&min_value_score=60',
      expect.objectContaining({ headers: expect.objectContaining({ Authorization: 'Bearer existing-token' }) }),
    ))

    fireEvent.change(screen.getByLabelText('开始日期'), { target: { value: '2026-08-01' } })
    fireEvent.change(screen.getByLabelText('结束日期'), { target: { value: '2026-08-31' } })
    await userEvent.click(screen.getByRole('button', { name: '应用筛选' }))

    await waitFor(() => {
      const path = String(fetchMock.mock.calls.at(-1)?.[0])
      expect(path).toContain('query=agents')
      expect(path).toContain('start_at=')
      expect(path).toContain('end_at=')
    })

    await userEvent.click(screen.getByRole('button', { name: /Agents gain new tools/ }))
    expect(await screen.findByText('测试价值分析')).toBeInTheDocument()
    expect(screen.getByText('Useful for autonomous regression testing.')).toBeInTheDocument()
    expect(screen.getByRole('link', { name: /查看原文/ })).toHaveAttribute('href', item.url)
  })

  it('selects multiple intelligence cards and downloads a Markdown report', async () => {
    localStorage.setItem('access_token', 'existing-token')
    const first = {
      id: 1, source_id: 1, source_name: 'Testing Lab', title: 'Agent regression testing', url: 'https://example.com/agent',
      summary: '', published_at: null, fetched_at: '2026-08-21T00:00:00Z', analysis_status: 'analyzed',
      testing_relevance_score: 90, testing_value_score: 88, analysis_summary: 'Agent testing summary',
      testing_value_analysis: 'Regression testing value', applicable_scenarios: ['Regression testing'],
      adoption_suggestions: ['Pilot'], analysis_risks: ['False positives'], analysis_tags: ['Agent'],
      analysis_model: 'test-model', analyzed_at: '2026-08-21T01:00:00Z',
    }
    const second = { ...first, id: 2, title: 'Visual quality testing', url: 'https://example.com/visual' }
    const fetchMock = vi.spyOn(globalThis, 'fetch')
      .mockResolvedValueOnce(json({ id: 'user-1', username: 'reader', role: 'viewer' }))
      .mockResolvedValueOnce(json({ items: [first, second], total: 2 }))
      .mockResolvedValueOnce(new Response('# 软件测试技术情报报告', {
        status: 200,
        headers: {
          'Content-Type': 'text/markdown; charset=utf-8',
          'Content-Disposition': 'attachment; filename="testing-intelligence-report.md"',
        },
      }))
    const createObjectURL = vi.fn(() => 'blob:testing-report')
    const revokeObjectURL = vi.fn()
    Object.defineProperty(URL, 'createObjectURL', { configurable: true, value: createObjectURL })
    Object.defineProperty(URL, 'revokeObjectURL', { configurable: true, value: revokeObjectURL })
    const clickSpy = vi.spyOn(HTMLAnchorElement.prototype, 'click').mockImplementation(() => undefined)

    render(<App />)
    await screen.findByText(first.title)
    await userEvent.click(screen.getByLabelText(`选择 ${first.title}`))
    await userEvent.click(screen.getByLabelText(`选择 ${second.title}`))
    await userEvent.click(screen.getByRole('button', { name: '导出 Markdown (2)' }))

    await waitFor(() => expect(fetchMock).toHaveBeenLastCalledWith('/api/v1/content/export', {
      method: 'POST',
      headers: { Authorization: 'Bearer existing-token', 'Content-Type': 'application/json' },
      body: JSON.stringify({ content_ids: [1, 2] }),
    }))
    expect(createObjectURL).toHaveBeenCalledOnce()
    expect(clickSpy).toHaveBeenCalledOnce()
    expect(revokeObjectURL).toHaveBeenCalledWith('blob:testing-report')
  })

  it('allows a maintainer to create a source', async () => {
    localStorage.setItem('access_token', 'existing-token')
    const user = userEvent.setup()
    const fetchMock = vi.spyOn(globalThis, 'fetch')
      .mockResolvedValueOnce(json({ id: 'user-2', username: 'maintainer', role: 'maintainer' }))
      .mockResolvedValueOnce(json({ items: [], total: 0 }))
      .mockResolvedValueOnce(json({ items: [], total: 0 }))
      .mockResolvedValueOnce(json(source, 201))
      .mockResolvedValueOnce(json({ items: [source], total: 1 }))

    render(<App />)
    await screen.findByRole('heading', { name: '内容情报' })
    await user.click(screen.getByRole('button', { name: '信源管理' }))
    await screen.findByText('暂无信源监听点。')
    await user.click(screen.getByRole('button', { name: '新增信源' }))
    await user.type(screen.getByLabelText('信源名称'), source.name)
    await user.click(screen.getByLabelText('信源类型'))
    await user.click(await screen.findByText('网站'))
    await user.click(screen.getByLabelText('可信等级'))
    await user.click(await screen.findByText('最高可信'))
    await user.type(screen.getByLabelText('主页地址'), source.homepage_url)
    await user.type(screen.getByLabelText('描述'), source.description)
    await user.type(screen.getByLabelText('语言'), 'en')
    await user.type(screen.getByLabelText('关注主题'), 'foundation-models')
    await user.click(screen.getByRole('button', { name: '建立监听' }))

    await waitFor(() => expect(fetchMock).toHaveBeenNthCalledWith(4, '/api/v1/sources', expect.objectContaining({
      method: 'POST',
      body: JSON.stringify({
        name: source.name, source_type: 'website', trust_level: 5, homepage_url: source.homepage_url,
        description: source.description, languages: ['en'], topics: ['foundation-models'],
      }),
    })))
    expect(await screen.findByText(source.name)).toBeInTheDocument()
  })

  it('submits WeChat content without fetching the platform page', async () => {
    localStorage.setItem('access_token', 'existing-token')
    const user = userEvent.setup()
    const wechatSource = { ...source, id: '12', name: '质量工程前沿', source_type: 'wechat' }
    const fetchMock = vi.spyOn(globalThis, 'fetch')
      .mockResolvedValueOnce(json({ id: 'user-2', username: 'maintainer', role: 'maintainer' }))
      .mockResolvedValueOnce(json({ items: [], total: 0 }))
      .mockResolvedValueOnce(json({ items: [wechatSource], total: 1 }))
      .mockResolvedValueOnce(json({
        id: 31, source_id: 12, source_name: wechatSource.name, title: '智能体测试方法',
        url: 'https://mp.weixin.qq.com/s/example', summary: '智能体回归测试和安全评估实践。',
        published_at: null, fetched_at: '2026-09-02T00:00:00Z', analysis_status: 'pending',
        analysis_attempts: 0, testing_relevance_score: null, testing_value_score: null,
        analysis_error: null, analyzed_at: null,
      }, 201))

    render(<App />)
    await screen.findByRole('heading', { name: '内容情报' })
    await user.click(screen.getByRole('button', { name: '信源管理' }))
    expect(await screen.findByText('微信公众号')).toBeInTheDocument()
    await user.click(screen.getByRole('button', { name: `录入内容 ${wechatSource.name}` }))
    await user.type(screen.getByLabelText('内容标题'), '智能体测试方法')
    await user.type(screen.getByLabelText('原文地址'), 'https://mp.weixin.qq.com/s/example')
    await user.type(screen.getByLabelText('内容摘要'), '智能体回归测试和安全评估实践。')
    await user.click(screen.getByRole('button', { name: '提交分析' }))

    await waitFor(() => {
      const call = fetchMock.mock.calls.at(-1)
      expect(call?.[0]).toBe('/api/v1/collected-content')
      expect(call?.[1]).toEqual(expect.objectContaining({
        method: 'POST',
        headers: { Authorization: 'Bearer existing-token', 'Content-Type': 'application/json' },
      }))
      expect(JSON.parse(String(call?.[1]?.body))).toEqual({
        source_id: 12,
        title: '智能体测试方法',
        url: 'https://mp.weixin.qq.com/s/example',
        summary: '智能体回归测试和安全评估实践。',
        published_at: null,
      })
    })
    expect((await screen.findAllByText('内容已进入分析队列')).length).toBeGreaterThan(0)
  })

  it('shows admin-only user and database management', async () => {
    localStorage.setItem('access_token', 'admin-token')
    const user = userEvent.setup()
    const fetchMock = vi.spyOn(globalThis, 'fetch')
      .mockResolvedValueOnce(json({ id: 'admin-1', username: 'root', role: 'admin' }))
      .mockResolvedValueOnce(json({ items: [], total: 0 }))
      .mockResolvedValueOnce(json({ items: [{ id: 'user-3', username: 'operator', role: 'maintainer', is_active: true }], total: 1 }))
      .mockResolvedValueOnce(json({ dialect: 'postgresql', row_counts: { users: 2, sources: 8, source_endpoints: 11, content_items: 240, fetch_runs: 15 } }))

    render(<App />)
    await screen.findByRole('heading', { name: '内容情报' })
    await user.click(screen.getByRole('button', { name: '用户管理' }))
    expect(await screen.findByText('operator')).toBeInTheDocument()
    expect(screen.getByRole('button', { name: '新增用户' })).toBeInTheDocument()

    await user.click(screen.getByRole('button', { name: '数据库状态' }))
    expect(await screen.findByText('postgresql')).toBeInTheDocument()
    expect(screen.getByText('240')).toBeInTheDocument()
    expect(fetchMock).toHaveBeenCalledWith('/api/v1/database/status', expect.any(Object))
  })

  it('lets an admin query and bulk-delete collected content', async () => {
    localStorage.setItem('access_token', 'admin-token')
    const user = userEvent.setup()
    const collected = [
      { id: 11, source_id: 1, source_name: 'OpenAI News', title: 'Testing candidate', url: 'https://example.com/11', summary: 'A testing summary', published_at: null, fetched_at: '2026-08-30T00:00:00Z', analysis_status: 'pending', analysis_attempts: 0, testing_relevance_score: null, testing_value_score: null, analysis_error: null, analyzed_at: null },
      { id: 12, source_id: 1, source_name: 'OpenAI News', title: 'Filtered update', url: 'https://example.com/12', summary: 'General news', published_at: null, fetched_at: '2026-08-29T00:00:00Z', analysis_status: 'filtered', analysis_attempts: 1, testing_relevance_score: 10, testing_value_score: 5, analysis_error: null, analyzed_at: '2026-08-29T01:00:00Z' },
    ]
    const fetchMock = vi.spyOn(globalThis, 'fetch')
      .mockResolvedValueOnce(json({ id: 1, username: 'root', role: 'admin' }))
      .mockResolvedValueOnce(json({ items: [], total: 0 }))
      .mockResolvedValueOnce(json({ items: collected, total: 2 }))
      .mockResolvedValueOnce(json({ items: [source], total: 1 }))
      .mockResolvedValueOnce(json({ deleted: 2 }))
      .mockResolvedValueOnce(json({ items: [], total: 0 }))

    render(<App />)
    await screen.findByRole('heading', { name: '内容情报' })
    await user.click(screen.getByRole('button', { name: '采集管理' }))
    expect(await screen.findByText('Testing candidate')).toBeInTheDocument()
    await user.click(screen.getByRole('button', { name: 'Testing candidate' }))
    expect(await screen.findByText('原始摘要')).toBeInTheDocument()
    expect(screen.getByText('A testing summary')).toBeInTheDocument()
    expect(screen.getByRole('link', { name: /查看原文/ })).toHaveAttribute('href', collected[0].url)
    await user.click(screen.getByRole('button', { name: 'Close' }))
    await user.click(screen.getByLabelText('选择采集内容 Testing candidate'))
    await user.click(screen.getByLabelText('选择采集内容 Filtered update'))
    await user.click(screen.getByRole('button', { name: '批量删除 (2)' }))
    await user.click(await screen.findByRole('button', { name: '确认删除' }))

    await waitFor(() => expect(fetchMock).toHaveBeenCalledWith('/api/v1/collected-content/bulk-delete', {
      method: 'POST',
      headers: { Authorization: 'Bearer admin-token', 'Content-Type': 'application/json' },
      body: JSON.stringify({ content_ids: [11, 12] }),
    }))
    expect(await screen.findByText('暂无采集内容')).toBeInTheDocument()
  })

  it('shows backend conflict detail when creating a duplicate user', async () => {
    localStorage.setItem('access_token', 'admin-token')
    const user = userEvent.setup()
    const fetchMock = vi.spyOn(globalThis, 'fetch')
      .mockResolvedValueOnce(json({ id: 'admin-1', username: 'root', role: 'admin' }))
      .mockResolvedValueOnce(json({ items: [], total: 0 }))
      .mockResolvedValueOnce(json({ items: [{ id: 'user-1', username: 'viewer1', role: 'viewer', is_active: true }], total: 1 }))
      .mockResolvedValueOnce(json({ detail: '用户名 viewer1 已存在，请更换用户名' }, 409))

    render(<App />)
    await screen.findByRole('heading', { name: '内容情报' })
    await user.click(screen.getByRole('button', { name: '用户管理' }))
    await screen.findByText('viewer1')
    await user.click(screen.getByRole('button', { name: '新增用户' }))
    await user.type(screen.getByLabelText('用户名'), 'viewer1')
    await user.type(screen.getByLabelText('初始密码'), 'password123')
    await user.click(screen.getByRole('button', { name: '创建用户' }))

    expect((await screen.findAllByText('用户名 viewer1 已存在，请更换用户名')).length).toBeGreaterThan(0)
    expect(fetchMock).toHaveBeenCalledWith('/api/v1/users', expect.objectContaining({
      method: 'POST',
      body: JSON.stringify({ username: 'viewer1', password: 'password123', role: 'viewer', is_active: true }),
    }))
  })
})
