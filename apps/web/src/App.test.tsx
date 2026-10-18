import { fireEvent, render, screen, waitFor, within } from '@testing-library/react'
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

function mockSourceSequence(responses: Response[]) {
  let lastSources = json({ items: [], total: 0 })
  return vi.spyOn(globalThis, 'fetch').mockImplementation(async input => {
    const path = String(input)
    if (path.startsWith('/api/v1/source-exploration/')) return json({ enabled: true, topics: [], languages: ['en'], excluded_terms: [], max_trial_sources: 3 })
    const response = responses.shift()
    if (!response) return lastSources.clone()
    if (path.startsWith('/api/v1/sources?')) lastSources = response.clone()
    return response
  })
}

function mockSourceTopicsPage(initialSources = [{ ...source, topics: ['testing', 'playwright'] }], failSave = false) {
  let items = initialSources
  return vi.spyOn(globalThis, 'fetch').mockImplementation(async (input, init) => {
    const path = String(input)
    if (path === '/api/v1/auth/me') return json({ id: 'maintainer', username: 'maintainer', role: 'maintainer' })
    if (path.startsWith('/api/v1/sources?')) return json({ items, total: items.length })
    if (path.startsWith('/api/v1/sources/') && init?.method === 'PATCH') {
      if (failSave) return json({ detail: 'Source name already exists' }, 409)
      items = items.map((item) => path === `/api/v1/sources/${item.id}` ? { ...item, ...JSON.parse(String(init.body)) } : item)
      return json(items.find((item) => path === `/api/v1/sources/${item.id}`))
    }
    return json({ items: [], total: 0 })
  })
}

async function openSourceTopicsEditor() {
  render(<App />)
  await screen.findByRole('heading', { name: '内容情报' })
  await userEvent.click(screen.getByRole('button', { name: '信源管理' }))
  const card = (await screen.findByRole('heading', { name: source.name })).closest('article') as HTMLElement
  await userEvent.click(within(card).getByRole('button', { name: 'edit 编辑' }))
  return within(await screen.findByRole('dialog'))
}

const exploration: {
  status: string; started_at: string | null; finished_at: string | null; next_run_at: string | null;
  discovered_count: number; report_id: number | null; error_message: string | null;
} = {
  status: 'never_run', started_at: null, finished_at: null, next_run_at: null,
  discovered_count: 0, report_id: null, error_message: null,
}

const githubReport = {
  id: 4, report_type: 'daily', title: 'GitHub 项目研究 2026-10-09',
  body_markdown: '# GitHub 项目研究 2026-10-09\n\n**项目摘要**\n自动探索得到的项目\n\n**测试价值分析**\n提高回归效率\n\n**应用场景推荐**\n- 回归测试\n\n**落地建议**\n- 小规模试点',
  status: 'ready', generated_at: '2026-10-09T00:00:00Z', items: [],
}

function mockGithubPage({ status = exploration, reports = [githubReport], role = 'maintainer', topics = ['testing'] } = {}) {
  return vi.spyOn(globalThis, 'fetch').mockImplementation(async (input, init) => {
    const path = String(input)
    if (path === '/api/v1/auth/me') return json({ id: 'user-2', username: 'reader', role })
    if (path === '/api/v1/github/exploration') return json(status)
    if (path === '/api/v1/github/reports') return json({ items: reports, total: reports.length })
    if (path === '/api/v1/github/preferences') return json(init?.method === 'PUT' ? JSON.parse(String(init.body)) : { topics })
    return json({ items: [], total: 0 })
  })
}

describe('daily email digest', () => {
  beforeEach(() => { vi.restoreAllMocks() })

  it('lets an administrator configure the daily digest and read delivery history', async () => {
    localStorage.setItem('access_token', 'admin-token')
    const preferences = { enabled: false, value_threshold: 70, send_time: '09:00', smtp_configured: false }
    const fetchMock = vi.spyOn(globalThis, 'fetch').mockImplementation(async (input, init) => {
      const path = String(input)
      if (path === '/api/v1/auth/me') return json({ id: 'admin', username: 'admin', role: 'admin' })
      if (path === '/api/v1/email-digest/preferences') return json(init?.method === 'PUT' ? { ...preferences, ...JSON.parse(String(init.body)) } : preferences)
      if (path === '/api/v1/email-digest/recipients') return json([{ id: 1, email: 'reader@example.com', name: '测试组', enabled: true, started_at: null, created_at: '2026-10-10T01:00:00Z' }])
      if (path === '/api/v1/email-digest/batches') return json({ items: [{ id: 1, business_date: '2026-10-11', status: 'delayed', prepare_at: '2026-10-10T23:00:00Z', cutoff_at: '2026-10-11T00:50:00Z', send_at: '2026-10-11T01:00:00Z', sources_total: 3, sources_completed: 2, sources_skipped: 1, analysis_total: 5, analysis_pending: 1, github_status: 'completed', error_message: '等待本批内容分析完成或失败重试', next_attempt_at: '2026-10-11T01:05:00Z' }] })
      if (path.startsWith('/api/v1/email-digest/deliveries')) return json({ items: [{ id: 1, recipient: 'reader@example.com', business_date: '2026-10-10', status: 'sent', item_count: 3, attempts: 1, created_at: '2026-10-10T01:00:00Z', sent_at: '2026-10-10T01:00:02Z', next_attempt_at: null, error_message: null }], total: 1 })
      return json({ items: [], total: 0 })
    })
    render(<App />)
    await screen.findByRole('heading', { name: '内容情报' })
    await userEvent.click(screen.getByRole('button', { name: '邮件推送' }))
    await screen.findByRole('heading', { name: '每日情报邮件' })
    expect(await screen.findByText(/尚未配置发件服务/)).toBeInTheDocument()
    expect(screen.getByText(/内容情报和GitHub项目研究日报/)).toBeInTheDocument()
    expect(screen.getByText(/已发布的GitHub日报按研究结果发送/)).toBeInTheDocument()
    expect(screen.getByText(/准备时间：每天07:00/)).toBeInTheDocument()
    expect(screen.getByText(/截稿时间：每天08:50/)).toBeInTheDocument()
    expect(await screen.findByText('延迟准备')).toBeInTheDocument()
    expect(screen.getByText(/已跳过1/)).toBeInTheDocument()
    expect(screen.getByText('等待本批内容分析完成或失败重试')).toBeInTheDocument()
    expect(screen.getByText(/采集2\/3/)).toBeInTheDocument()
    expect(screen.getByRole('button', { name: '刷新准备进度' })).toBeInTheDocument()
    expect(screen.getByText(/采集、内容分析和GitHub研究完成后/)).toBeInTheDocument()
    expect(screen.getByLabelText('价值分超过')).toHaveValue('70')
    expect(screen.getByLabelText('每日发送时间')).toHaveValue('09:00')
    expect(await screen.findByText('已发送')).toBeInTheDocument()
    await userEvent.click(screen.getByRole('switch', { name: '启用每日邮件' }))
    fireEvent.change(screen.getByLabelText('每日发送时间'), { target: { value: '10:30' } })
    expect(await screen.findByText(/准备时间：每天08:30/)).toBeInTheDocument()
    expect(screen.getByText(/截稿时间：每天10:20/)).toBeInTheDocument()
    await userEvent.click(screen.getByRole('button', { name: '保存邮件设置' }))
    await waitFor(() => expect(fetchMock).toHaveBeenCalledWith('/api/v1/email-digest/preferences', expect.objectContaining({ method: 'PUT', body: JSON.stringify({ enabled: true, value_threshold: 70, send_time: '10:30' }) })))
    expect(await screen.findByText('邮件设置已保存')).toBeInTheDocument()
    fireEvent.change(screen.getByLabelText('每日发送时间'), { target: { value: '01:00' } })
    expect(await screen.findByText(/准备时间：前一天23:00/)).toBeInTheDocument()
    fireEvent.change(screen.getByLabelText('每日发送时间'), { target: { value: '00:05' } })
    expect(await screen.findByText(/截稿时间：前一天23:55/)).toBeInTheDocument()
  })

  it('hides email administration from viewers and maintainers', async () => {
    localStorage.setItem('access_token', 'reader-token')
    vi.spyOn(globalThis, 'fetch').mockImplementation(async (input) => String(input) === '/api/v1/auth/me' ? json({ id: 'reader', username: 'reader', role: 'maintainer' }) : json({ items: [], total: 0 }))
    render(<App />)
    await screen.findByRole('heading', { name: '内容情报' })
    expect(screen.queryByRole('button', { name: '邮件推送' })).not.toBeInTheDocument()
  })

  it('manages multiple recipient mailboxes with names, editing, pause and deletion', async () => {
    localStorage.setItem('access_token', 'admin-token')
    let recipients = [{ id: 1, email: 'reader@example.com', name: '测试组', enabled: true, started_at: null, created_at: '2026-10-10T01:00:00Z' }]
    const fetchMock = vi.spyOn(globalThis, 'fetch').mockImplementation(async (input, init) => {
      const path = String(input)
      if (path === '/api/v1/auth/me') return json({ id: 'admin', username: 'admin', role: 'admin' })
      if (path === '/api/v1/email-digest/preferences') return json({ enabled: true, value_threshold: 70, send_time: '09:00', smtp_configured: true })
      if (path === '/api/v1/email-digest/recipients') {
        if (init?.method === 'POST') {
          const added = { ...recipients[0], id: 2, ...JSON.parse(String(init.body)) }
          recipients = [...recipients, added]
          return json(added, 201)
        }
        return json(recipients)
      }
      if (path === '/api/v1/email-digest/recipients/2') {
        if (init?.method === 'DELETE') { recipients = recipients.filter(row => row.id !== 2); return new Response(null, { status: 204 }) }
        const changed = { ...recipients[1], ...JSON.parse(String(init?.body)) }
        recipients = [recipients[0], changed]
        return json(changed)
      }
      return json({ items: [], total: 0 })
    })
    render(<App />)
    await screen.findByRole('heading', { name: '内容情报' })
    await userEvent.click(screen.getByRole('button', { name: '邮件推送' }))
    await screen.findByRole('heading', { name: '收件邮箱管理' })
    expect(await screen.findByText('测试组')).toBeInTheDocument()
    await userEvent.click(screen.getByRole('button', { name: '新增收件邮箱' }))
    const dialog = within(await screen.findByRole('dialog', { name: '新增收件邮箱' }))
    await userEvent.type(dialog.getByLabelText('邮箱地址'), 'second@example.com')
    await userEvent.type(dialog.getByLabelText('名称或备注'), '研发组')
    await userEvent.click(dialog.getByRole('button', { name: '保存邮箱' }))
    expect(await screen.findByText('second@example.com')).toBeInTheDocument()
    expect(screen.getByText('研发组')).toBeInTheDocument()
    await userEvent.click(screen.getByRole('button', { name: '编辑second@example.com' }))
    const editor = within(await screen.findByRole('dialog', { name: '编辑收件邮箱' }))
    await userEvent.clear(editor.getByLabelText('名称或备注'))
    await userEvent.type(editor.getByLabelText('名称或备注'), '工程组')
    await userEvent.click(editor.getByRole('button', { name: '保存邮箱' }))
    expect(await screen.findByText('工程组')).toBeInTheDocument()
    await userEvent.click(screen.getByRole('switch', { name: '启用second@example.com' }))
    await waitFor(() => expect(fetchMock).toHaveBeenCalledWith('/api/v1/email-digest/recipients/2', expect.objectContaining({ method: 'PATCH', body: JSON.stringify({ enabled: false }) })))
    await userEvent.click(screen.getByRole('button', { name: '删除second@example.com' }))
    const confirmation = within(await screen.findByRole('dialog', { name: '删除收件邮箱？' }))
    await userEvent.click(confirmation.getByRole('button', { name: '确认删除' }))
    await waitFor(() => expect(screen.queryByText('second@example.com')).not.toBeInTheDocument())
    expect(screen.getByText('reader@example.com')).toBeInTheDocument()
  }, 30000)

  it('keeps the mailbox editor open for invalid or duplicate addresses', async () => {
    localStorage.setItem('access_token', 'admin-token')
    const fetchMock = vi.spyOn(globalThis, 'fetch').mockImplementation(async (input, init) => {
      const path = String(input)
      if (path === '/api/v1/auth/me') return json({ id: 'admin', username: 'admin', role: 'admin' })
      if (path === '/api/v1/email-digest/preferences') return json({ enabled: false, value_threshold: 70, send_time: '09:00', smtp_configured: true })
      if (path === '/api/v1/email-digest/recipients') return init?.method === 'POST' ? json({ detail: '该收件邮箱已存在' }, 409) : json([])
      return json({ items: [], total: 0 })
    })
    render(<App />)
    await screen.findByRole('heading', { name: '内容情报' })
    await userEvent.click(screen.getByRole('button', { name: '邮件推送' }))
    await userEvent.click(await screen.findByRole('button', { name: '新增收件邮箱' }))
    const dialog = within(await screen.findByRole('dialog', { name: '新增收件邮箱' }))
    await userEvent.type(dialog.getByLabelText('邮箱地址'), 'bad')
    await userEvent.click(dialog.getByRole('button', { name: '保存邮箱' }))
    expect(await dialog.findByText('请输入有效的邮箱地址')).toBeInTheDocument()
    expect(fetchMock.mock.calls.filter(([, init]) => init?.method === 'POST')).toHaveLength(0)
    await userEvent.clear(dialog.getByLabelText('邮箱地址'))
    await userEvent.type(dialog.getByLabelText('邮箱地址'), 'reader@example.com')
    await userEvent.click(dialog.getByRole('button', { name: '保存邮箱' }))
    expect(await dialog.findByText('该收件邮箱已存在')).toBeInTheDocument()
    expect(screen.getByRole('dialog', { name: '新增收件邮箱' })).toBeInTheDocument()
  })

  it('disables retries for paused or deleted recipients', async () => {
    localStorage.setItem('access_token', 'admin-token')
    vi.spyOn(globalThis, 'fetch').mockImplementation(async (input) => {
      const path = String(input)
      if (path === '/api/v1/auth/me') return json({ id: 'admin', username: 'admin', role: 'admin' })
      if (path === '/api/v1/email-digest/preferences') return json({ enabled: true, value_threshold: 70, send_time: '09:00', smtp_configured: true })
      if (path === '/api/v1/email-digest/recipients') return json([{ id: 1, email: 'paused@example.com', name: '', enabled: false, started_at: null, created_at: '2026-10-10T01:00:00Z' }])
      return json({ items: ['paused@example.com', 'deleted@example.com'].map((recipient, index) => ({ id: index + 1, recipient, business_date: '2026-10-10', status: 'failed', item_count: 1, attempts: 3, created_at: '2026-10-10T01:00:00Z', sent_at: null, next_attempt_at: null, error_message: null })), total: 2 })
    })
    render(<App />)
    await screen.findByRole('heading', { name: '内容情报' })
    await userEvent.click(screen.getByRole('button', { name: '邮件推送' }))
    await screen.findByRole('heading', { name: '收件邮箱管理' })
    expect(screen.getAllByRole('button', { name: '重试' })).toHaveLength(2)
    screen.getAllByRole('button', { name: '重试' }).forEach(button => expect(button).toBeDisabled())
  })
})

async function openGithubTopicsEditor() {
  render(<App />)
  await screen.findByRole('heading', { name: '内容情报' })
  await userEvent.click(screen.getByRole('button', { name: 'GitHub 追踪' }))
  await screen.findByText('关注方向')
  await userEvent.click(screen.getByRole('button', { name: /关注主题/ }))
  return within(await screen.findByRole('dialog', { name: /关注主题/ }))
}

describe('source topic management', () => {
  beforeEach(() => { vi.restoreAllMocks() })

  it('keeps source topics when backspace clears the independent add input', async () => {
    localStorage.setItem('access_token', 'existing-token')
    const user = userEvent.setup()
    const fetchMock = mockSourceTopicsPage()
    const editor = await openSourceTopicsEditor()
    const input = editor.getByRole('textbox', { name: '新增关注主题' })
    await user.type(input, 'draft')
    await user.clear(input)
    await user.keyboard('{Backspace}')
    const list = within(editor.getByRole('list', { name: '关注主题列表' }))
    expect(list.getAllByRole('listitem')).toHaveLength(2)
    expect(list.getByText('testing')).toBeInTheDocument()
    expect(list.getByText('playwright')).toBeInTheDocument()
    expect(editor.queryByRole('button', { name: /移除/ })).not.toBeInTheDocument()
    expect(editor.getByText('新增 0 · 修改 0 · 移除 0')).toBeInTheDocument()
    expect(fetchMock.mock.calls.filter(([, init]) => init?.method === 'PATCH')).toHaveLength(0)
  })

  it('reviews source topic edits and removals before saving the source', async () => {
    localStorage.setItem('access_token', 'existing-token')
    const user = userEvent.setup()
    const fetchMock = mockSourceTopicsPage()
    const editor = await openSourceTopicsEditor()
    await user.click(editor.getByRole('button', { name: '编辑主题 testing' }))
    expect(editor.getByRole('button', { name: '保存修改' })).toBeDisabled()
    const editInput = editor.getByRole('textbox', { name: '编辑关注主题' })
    fireEvent.change(editInput, { target: { value: ' AI testing ' } })
    await user.type(editInput, '{Enter}')
    const addInput = editor.getByRole('textbox', { name: '新增关注主题' })
    fireEvent.change(addInput, { target: { value: ' rag ' } })
    await user.type(addInput, '{Enter}')
    await user.click(editor.getByRole('button', { name: '更多操作 playwright' }))
    await user.click(await screen.findByRole('menuitem', { name: /移除主题/ }))
    expect(editor.getByText('新增 1 · 修改 1 · 移除 1')).toBeInTheDocument()
    expect(editor.getByText('待移除')).toBeInTheDocument()
    expect(screen.getByText('#playwright')).toBeInTheDocument()
    expect(fetchMock.mock.calls.filter(([, init]) => init?.method === 'PATCH')).toHaveLength(0)
    await user.click(editor.getByRole('button', { name: '保存修改' }))
    await waitFor(() => expect(fetchMock).toHaveBeenCalledWith(`/api/v1/sources/${source.id}`, expect.objectContaining({
      method: 'PATCH', headers: expect.objectContaining({ 'Content-Type': 'application/json', Authorization: 'Bearer existing-token' }),
      body: JSON.stringify({ name: source.name, source_type: source.source_type, trust_level: source.trust_level, homepage_url: source.homepage_url, description: source.description, languages: source.languages, topics: ['AI testing', 'rag'] }),
    })))
    await waitFor(() => expect(screen.queryByRole('dialog')).not.toBeInTheDocument())
    expect(screen.getByText('#AI testing')).toBeInTheDocument()
    expect(screen.queryByText('#playwright')).not.toBeInTheDocument()
  })

  it('validates source topics and prevents saving unconfirmed input', async () => {
    localStorage.setItem('access_token', 'existing-token')
    const user = userEvent.setup()
    const fetchMock = mockSourceTopicsPage()
    const editor = await openSourceTopicsEditor()
    const addInput = editor.getByRole('textbox', { name: '新增关注主题' })
    fireEvent.change(addInput, { target: { value: ' testing ' } })
    expect(editor.getByRole('button', { name: '保存修改' })).toBeDisabled()
    await user.click(editor.getByRole('button', { name: /添加主题/ }))
    expect(editor.getByRole('alert')).toHaveTextContent('该主题已存在')
    fireEvent.change(addInput, { target: { value: '' } })
    await user.click(editor.getByRole('button', { name: '编辑主题 testing' }))
    const editInput = editor.getByRole('textbox', { name: '编辑关注主题' })
    fireEvent.change(editInput, { target: { value: '' } })
    await user.click(editor.getByRole('button', { name: '确认修改' }))
    expect(editor.getByRole('alert')).toHaveTextContent('主题不能为空')
    fireEvent.change(editInput, { target: { value: 'playwright' } })
    await user.click(editor.getByRole('button', { name: '确认修改' }))
    expect(editor.getByRole('alert')).toHaveTextContent('该主题已存在')
    await user.click(editor.getByRole('button', { name: '取消编辑' }))
    expect(editor.getByText('新增 0 · 修改 0 · 移除 0')).toBeInTheDocument()
    expect(editor.getByRole('list', { name: '关注主题列表' })).toHaveTextContent('testing')
    expect(editor.getByRole('button', { name: '保存修改' })).toBeEnabled()
    expect(fetchMock.mock.calls.filter(([, init]) => init?.method === 'PATCH')).toHaveLength(0)
  })

  it('requires at least one source topic and recovers from clearing the list with undo', async () => {
    localStorage.setItem('access_token', 'existing-token')
    const user = userEvent.setup()
    const fetchMock = mockSourceTopicsPage()
    const editor = await openSourceTopicsEditor()
    for (const topic of ['testing', 'playwright']) {
      await user.click(editor.getByRole('button', { name: `更多操作 ${topic}` }))
      await user.click(await screen.findByRole('menuitem', { name: /移除主题/ }))
    }
    expect(editor.getByRole('button', { name: '保存修改' })).toBeDisabled()
    expect(editor.getByText(/请至少添加一个关注主题/)).toBeInTheDocument()
    await user.click(editor.getByRole('button', { name: '撤销移除 testing' }))
    expect(editor.getByRole('button', { name: '保存修改' })).toBeEnabled()
    expect(fetchMock.mock.calls.filter(([, init]) => init?.method === 'PATCH')).toHaveLength(0)
  })

  it('discards source topic drafts on close and isolates topics between sources', async () => {
    localStorage.setItem('access_token', 'existing-token')
    const user = userEvent.setup()
    const another = { ...source, id: 'source-2', name: 'Testing Weekly', topics: ['evaluation'] }
    const fetchMock = mockSourceTopicsPage([{ ...source, topics: ['testing', 'playwright'] }, another])
    const editor = await openSourceTopicsEditor()
    await user.type(editor.getByRole('textbox', { name: '新增关注主题' }), 'rag{Enter}')
    await user.click(editor.getByRole('button', { name: '更多操作 testing' }))
    await user.click(await screen.findByRole('menuitem', { name: /移除主题/ }))
    await user.click(editor.getByRole('button', { name: '取消' }))
    await waitFor(() => expect(screen.queryByRole('dialog')).not.toBeInTheDocument())
    const originalCard = screen.getByRole('heading', { name: source.name }).closest('article') as HTMLElement
    await user.click(within(originalCard).getByRole('button', { name: 'edit 编辑' }))
    const reopened = within(await screen.findByRole('dialog'))
    expect(reopened.getByRole('list', { name: '关注主题列表' })).toHaveTextContent('testing')
    expect(reopened.queryByText('rag')).not.toBeInTheDocument()
    expect(reopened.queryByText('待移除')).not.toBeInTheDocument()
    await user.click(reopened.getByRole('button', { name: '取消' }))
    await waitFor(() => expect(screen.queryByRole('dialog')).not.toBeInTheDocument())
    const otherCard = screen.getByRole('heading', { name: another.name }).closest('article') as HTMLElement
    await user.click(within(otherCard).getByRole('button', { name: 'edit 编辑' }))
    const otherEditor = within(await screen.findByRole('dialog'))
    expect(otherEditor.getByRole('list', { name: '关注主题列表' })).toHaveTextContent('evaluation')
    expect(otherEditor.queryByText('testing')).not.toBeInTheDocument()
    expect(otherEditor.getByText('新增 0 · 修改 0 · 移除 0')).toBeInTheDocument()
    expect(fetchMock.mock.calls.filter(([, init]) => init?.method === 'PATCH')).toHaveLength(0)
  })

  it('retains source topic drafts when saving the source fails', async () => {
    localStorage.setItem('access_token', 'existing-token')
    const user = userEvent.setup()
    mockSourceTopicsPage(undefined, true)
    const editor = await openSourceTopicsEditor()
    await user.type(editor.getByRole('textbox', { name: '新增关注主题' }), 'rag{Enter}')
    await user.click(editor.getByRole('button', { name: '保存修改' }))
    expect(await screen.findByText('信源名称已存在，请修改名称后重试。')).toBeInTheDocument()
    expect(editor.getByRole('list', { name: '关注主题列表' })).toHaveTextContent('rag')
    expect(editor.getByText('新增 1 · 修改 0 · 移除 0')).toBeInTheDocument()
    await waitFor(() => expect(editor.getByRole('button', { name: '保存修改' })).toBeEnabled())
  })
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
    expect(screen.getByRole('button', { name: '内容情报' })).toHaveAttribute('aria-current', 'page')
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
      analysis_risks: ['False positives'], analysis_tags: ['AI Agent', 'Testing'],
      related_links: [{ title: 'Evaluation benchmark', url: 'https://example.org/benchmark' }], analysis_model: 'test-model', analyzed_at: '2026-08-21T01:00:00Z',
    }
    const fetchMock = vi.spyOn(globalThis, 'fetch')
      .mockResolvedValueOnce(json({ id: 'user-1', username: 'reader', role: 'viewer' }))
      .mockResolvedValueOnce(json({ items: [item], total: 1 }))
      .mockResolvedValueOnce(json({ items: [item], total: 1 }))
      .mockResolvedValueOnce(json({ items: [item], total: 1 }))
      .mockResolvedValueOnce(json({ items: [item], total: 1 }))

    render(<App />)
    expect(await screen.findByText('Agents gain new tools')).toBeInTheDocument()
    expect(screen.getByRole('heading', { name: '内容情报' }).closest('.page-intro')).toHaveClass('compact')
    expect(screen.getByText('最新优先')).toBeInTheDocument()
    const compactView = screen.getByRole('button', { name: '紧凑列表' })
    const cardView = screen.getByRole('button', { name: '卡片视图' })
    expect(compactView).toHaveAttribute('aria-pressed', 'true')
    expect(document.querySelector('.content-grid')).toHaveClass('compact-view')
    await userEvent.click(cardView)
    expect(screen.getByRole('button', { name: '卡片视图' })).toHaveAttribute('aria-pressed', 'true')
    expect(document.querySelector('.content-grid')).toHaveClass('card-view')
    await userEvent.click(screen.getByRole('button', { name: '紧凑列表' }))

    await userEvent.click(screen.getByLabelText('情报分层'))
    await userEvent.click(await screen.findByText('观察池'))
    await waitFor(() => expect(fetchMock).toHaveBeenLastCalledWith(
      '/api/v1/content?offset=0&limit=12&disposition=watch&min_value_score=40',
      expect.objectContaining({ headers: expect.objectContaining({ Authorization: 'Bearer existing-token' }) }),
    ))

    const search = screen.getByLabelText('检索内容')
    await userEvent.type(search, 'agents')
    await userEvent.type(search, '{Enter}')

    await waitFor(() => expect(fetchMock).toHaveBeenLastCalledWith(
      '/api/v1/content?offset=0&limit=12&query=agents&disposition=watch&min_value_score=40',
      expect.objectContaining({ headers: expect.objectContaining({ Authorization: 'Bearer existing-token' }) }),
    ))

    expect(screen.queryByLabelText('开始日期')).not.toBeInTheDocument()
    await userEvent.click(screen.getByRole('button', { name: '更多筛选' }))
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
    expect(screen.getByRole('link', { name: /Evaluation benchmark/ })).toHaveAttribute('href', 'https://example.org/benchmark')
    expect(screen.getByRole('link', { name: /查看原文/ })).toHaveAttribute('href', item.url)
  })

  it('announces the initial intelligence loading state', async () => {
    localStorage.setItem('access_token', 'existing-token')
    vi.spyOn(globalThis, 'fetch')
      .mockResolvedValueOnce(json({ id: 'user-1', username: 'reader', role: 'viewer' }))
      .mockImplementationOnce(() => new Promise<Response>(() => undefined))

    render(<App />)

    expect(await screen.findByRole('status', { name: '正在扫描情报流' })).toBeInTheDocument()
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
    const fetchMock = mockSourceSequence([
      json({ id: 'user-2', username: 'maintainer', role: 'maintainer' }),
      json({ items: [], total: 0 }),
      json({ items: [], total: 0 }),
      json(source, 201),
      json({ items: [source], total: 1 })
    ])
    render(<App />)
    await screen.findByRole('heading', { name: '内容情报' })
    await user.click(screen.getByRole('button', { name: '信源管理' }))
    await screen.findByText('没有符合条件的信源')
    await user.click(screen.getByRole('button', { name: '新增信源' }))
    const editor = within(await screen.findByRole('dialog'))
    fireEvent.change(editor.getByLabelText('信源名称'), { target: { value: source.name } })
    await user.click(editor.getByLabelText('信源类型'))
    await user.click(await screen.findByText('网站'))
    await user.click(editor.getByLabelText('可信等级'))
    await user.click(await screen.findByText('最高可信'))
    fireEvent.change(editor.getByLabelText('主页地址'), { target: { value: source.homepage_url } })
    fireEvent.change(editor.getByLabelText('描述'), { target: { value: source.description } })
    await user.click(editor.getByLabelText('语言'))
    await user.click(await screen.findByText('英语'))
    fireEvent.change(editor.getByRole('textbox', { name: '新增关注主题' }), { target: { value: 'foundation-models' } })
    await user.click(editor.getByRole('button', { name: /添加主题/ }))
    await user.click(editor.getByRole('button', { name: '建立监听' }))

    await waitFor(() => expect(fetchMock).toHaveBeenCalledWith('/api/v1/sources', expect.objectContaining({
      method: 'POST',
      body: JSON.stringify({
        name: source.name, source_type: 'website', trust_level: 5, homepage_url: source.homepage_url,
        description: source.description, languages: ['en'], topics: ['foundation-models'],
      }),
    })))
    expect(await screen.findByText(source.name)).toBeInTheDocument()
  })

  it('discovers previews and installs a source from its homepage', async () => {
    localStorage.setItem('access_token', 'existing-token')
    const user = userEvent.setup()
    const discoveredSource = { ...source, id: 'source-2', name: 'example.com', homepage_url: 'https://example.com/blog/' }
    const discovery = {
      homepage_url: discoveredSource.homepage_url,
      feed_url: 'https://example.com/blog/rss.xml',
      suggested_name: 'example.com',
      samples: [{ title: 'Agent testing update', url: 'https://example.com/article', summary: 'A verified feed sample', published_at: '2026-09-03T00:00:00Z' }],
    }
    const fetchMock = mockSourceSequence([
      json({ id: 'user-2', username: 'maintainer', role: 'maintainer' }),
      json({ items: [], total: 0 }),
      json({ items: [], total: 0 }),
      json(discovery),
      json({ source: discoveredSource, endpoint: { id: 9, source_id: 'source-2', name: 'Auto-discovered RSS/Atom', endpoint_type: 'rss', url: discovery.feed_url, fetch_interval_minutes: 360, max_items_per_run: 50, enabled: true, health_status: 'healthy' } }, 201),
      json({ items: [discoveredSource], total: 1 })
    ])
    render(<App />)
    await screen.findByRole('heading', { name: '内容情报' })
    await user.click(screen.getByRole('button', { name: '信源管理' }))
    await screen.findByText('没有符合条件的信源')
    await user.click(screen.getByRole('button', { name: '通过网址添加' }))
    await user.type(screen.getByLabelText('网站主页'), discoveredSource.homepage_url)
    await user.click(screen.getByRole('button', { name: '开始探测' }))

    expect(await screen.findByText('Agent testing update')).toBeInTheDocument()
    expect(screen.getByText('A verified feed sample')).toBeInTheDocument()
    await user.click(screen.getByRole('button', { name: '确认并启用监听' }))

    await waitFor(() => expect(fetchMock).toHaveBeenCalledWith('/api/v1/sources/discover/install', expect.objectContaining({
      method: 'POST',
      body: JSON.stringify({
        homepage_url: discovery.homepage_url, feed_url: discovery.feed_url, name: 'example.com',
        languages: ['en'], trust_level: 3, topics: [],
      }),
    })))
    expect(await screen.findByRole('heading', { name: 'example.com' })).toBeInTheDocument()
  })

  it('manages source topics before installing an automatically discovered source', async () => {
    localStorage.setItem('access_token', 'existing-token')
    const user = userEvent.setup()
    const discovery = { homepage_url: 'https://example.com/', feed_url: 'https://example.com/feed.xml', suggested_name: 'example.com', samples: [] }
    const fetchMock = vi.spyOn(globalThis, 'fetch').mockImplementation(async (input) => {
      const path = String(input)
      if (path === '/api/v1/auth/me') return json({ id: 'maintainer', username: 'maintainer', role: 'maintainer' })
      if (path === '/api/v1/sources/discover') return json(discovery)
      if (path === '/api/v1/sources/discover/install') return json({ source, endpoint: {} }, 201)
      return json({ items: [], total: 0 })
    })
    render(<App />)
    await screen.findByRole('heading', { name: '内容情报' })
    await user.click(screen.getByRole('button', { name: '信源管理' }))
    await screen.findByText('没有符合条件的信源')
    await user.click(screen.getByRole('button', { name: '通过网址添加' }))
    const editor = within(await screen.findByRole('dialog'))
    fireEvent.change(editor.getByLabelText('网站主页'), { target: { value: discovery.homepage_url } })
    await user.click(editor.getByRole('button', { name: '开始探测' }))
    await editor.findByRole('button', { name: '确认并启用监听' })
    const addInput = editor.getByRole('textbox', { name: '新增关注主题' })
    fireEvent.change(addInput, { target: { value: ' AI testing ' } })
    await user.type(addInput, '{Enter}')
    fireEvent.change(addInput, { target: { value: 'rag' } })
    expect(editor.getByRole('button', { name: '确认并启用监听' })).toBeDisabled()
    await user.click(editor.getByRole('button', { name: /添加主题/ }))
    await user.click(editor.getByRole('button', { name: '更多操作 rag' }))
    await user.click(await screen.findByRole('menuitem', { name: /移除主题/ }))
    await user.click(editor.getByRole('button', { name: '撤销移除 rag' }))
    expect(editor.getByText('新增 2 · 修改 0 · 移除 0')).toBeInTheDocument()
    expect(fetchMock.mock.calls.filter(([path]) => path === '/api/v1/sources/discover/install')).toHaveLength(0)
    await user.click(editor.getByRole('button', { name: '确认并启用监听' }))
    await waitFor(() => expect(fetchMock).toHaveBeenCalledWith('/api/v1/sources/discover/install', expect.objectContaining({
      method: 'POST', body: JSON.stringify({ homepage_url: discovery.homepage_url, feed_url: discovery.feed_url, name: 'example.com', languages: ['en'], trust_level: 3, topics: ['AI testing', 'rag'] }),
    })))
  })

  it.each([
    ['No valid RSS or Atom feed was found for this website', '未发现可用的 RSS / Atom 订阅，请检查网站地址，或尝试输入博客、新闻栏目地址。'],
    ['Internal Server Error', '自动发现失败，请检查网站地址或稍后重试。'],
  ])('shows a Chinese discovery notification for %s above the drawer', async (detail, expected) => {
    localStorage.setItem('access_token', 'existing-token')
    const user = userEvent.setup()
    mockSourceSequence([
      json({ id: 'user-2', username: 'maintainer', role: 'maintainer' }),
      json({ items: [], total: 0 }),
      json({ items: [], total: 0 }),
      json({ detail }, 422)
    ])
    render(<App />)
    await screen.findByRole('heading', { name: '内容情报' })
    await user.click(screen.getByRole('button', { name: '信源管理' }))
    await screen.findByText('没有符合条件的信源')
    await user.click(screen.getByRole('button', { name: '通过网址添加' }))
    await user.type(screen.getByLabelText('网站主页'), 'https://example.com/')
    await user.click(screen.getByRole('button', { name: '开始探测' }))

    const notice = await screen.findByText(expected)
    expect(notice.closest('.ant-notification-notice')).not.toBeNull()
    expect(notice.closest('.ant-drawer')).toBeNull()
    expect(screen.getByLabelText('网站主页')).toBeVisible()
    expect(screen.queryByText(detail)).not.toBeInTheDocument()
    expect(document.querySelector('.dashboard-alert')).toBeNull()
  })

  it.each([
    ['The discovered feed is no longer valid', '订阅地址已失效，请重新探测后再启用监听。'],
    ['Source name already exists', '信源名称已存在，请修改名称后重试。'],
  ])('shows a Chinese installation notification for %s', async (detail, expected) => {
    localStorage.setItem('access_token', 'existing-token')
    const user = userEvent.setup()
    mockSourceSequence([
      json({ id: 'user-2', username: 'maintainer', role: 'maintainer' }),
      json({ items: [], total: 0 }),
      json({ items: [], total: 0 }),
      json({ homepage_url: 'https://example.com/', feed_url: 'https://example.com/feed.xml', suggested_name: 'example.com', samples: [] }),
      json({ detail }, 422)
    ])
    render(<App />)
    await screen.findByRole('heading', { name: '内容情报' })
    await user.click(screen.getByRole('button', { name: '信源管理' }))
    await screen.findByText('没有符合条件的信源')
    await user.click(screen.getByRole('button', { name: '通过网址添加' }))
    await user.type(screen.getByLabelText('网站主页'), 'https://example.com/')
    await user.click(screen.getByRole('button', { name: '开始探测' }))
    await user.click(await screen.findByRole('button', { name: '确认并启用监听' }))

    expect((await screen.findByText(expected)).closest('.ant-notification-notice')).not.toBeNull()
    expect(screen.getByLabelText('信源名称')).toHaveValue('example.com')
    expect(screen.queryByText(detail)).not.toBeInTheDocument()
    expect(document.querySelector('.dashboard-alert')).toBeNull()
  })

  it.each(['source', 'endpoint', 'manual'])('shows Chinese %s errors outside the drawer', async (operation) => {
    localStorage.setItem('access_token', 'existing-token')
    const user = userEvent.setup()
    const item = { ...source, id: '12', source_type: 'wechat' }
    const details = {
      source: ['Source name already exists', '信源名称已存在，请修改名称后重试。'],
      endpoint: ['Endpoint not found', '采集端点不存在，请刷新后重试。'],
      manual: ['Content with this URL or title already exists', '相同链接或标题的内容已存在，请勿重复录入。'],
    }
    const [detail, expected] = details[operation as keyof typeof details]
    mockSourceSequence([
      json({ id: 'user-2', username: 'maintainer', role: 'maintainer' }),
      json({ items: [], total: 0 }),
      json({ items: [item], total: 1 }),
      json({ detail }, 409)
    ])
    render(<App />)
    await screen.findByRole('heading', { name: '内容情报' })
    await user.click(screen.getByRole('button', { name: '信源管理' }))
    await screen.findByText(item.name)
    if (operation === 'source') {
      await user.click(screen.getByRole('button', { name: 'edit 编辑' }))
      await user.click(screen.getByRole('button', { name: '保存修改' }))
    } else if (operation === 'endpoint') {
      await user.click(screen.getByRole('button', { name: 'radar-chart 采集端点' }))
    } else {
      await user.click(screen.getByRole('button', { name: `录入内容 ${item.name}` }))
      fireEvent.change(screen.getByLabelText('内容标题'), { target: { value: '重复标题' } })
      fireEvent.change(screen.getByLabelText('原文地址'), { target: { value: 'https://example.com/article' } })
      fireEvent.change(screen.getByLabelText('内容摘要'), { target: { value: '测试摘要' } })
      await user.click(screen.getByRole('button', { name: '提交分析' }))
    }
    expect((await screen.findByText(expected)).closest('.ant-notification-notice')).not.toBeNull()
    expect(screen.queryByText(detail)).not.toBeInTheDocument()
    expect(document.querySelector('.dashboard-alert')).toBeNull()
  })

  it('shows Chinese validation errors when saving GitHub topics', async () => {
    localStorage.setItem('access_token', 'existing-token')
    const user = userEvent.setup()
    vi.spyOn(globalThis, 'fetch')
      .mockResolvedValueOnce(json({ id: 'user-2', username: 'maintainer', role: 'maintainer' }))
      .mockResolvedValueOnce(json({ items: [], total: 0 }))
      .mockResolvedValueOnce(json({ items: [], total: 0 }))
      .mockResolvedValueOnce(json({ items: [], total: 0 }))
      .mockResolvedValueOnce(json({ topics: [] }))
      .mockResolvedValueOnce(json(exploration))
      .mockResolvedValueOnce(json({ detail: [{ loc: ['body', 'topics'], msg: 'Input should be a valid list' }] }, 422))
    render(<App />)
    await screen.findByRole('heading', { name: '内容情报' })
    await user.click(screen.getByRole('button', { name: 'GitHub 追踪' }))
    await user.click(await screen.findByRole('button', { name: 'edit 关注主题' }))
    await user.type(screen.getByRole('textbox', { name: '新增关注主题' }), 'testing')
    await user.click(screen.getByRole('button', { name: /添加主题/ }))
    await user.click(screen.getByRole('button', { name: '保存关注主题' }))
    expect((await screen.findByText('提交的数据不符合要求，请检查填写内容后重试。')).closest('.ant-notification-notice')).not.toBeNull()
    expect(screen.queryByText('[object Object]')).not.toBeInTheDocument()
    expect(screen.getByRole('list', { name: '关注主题列表' })).toHaveTextContent('testing')
    expect(screen.getByText('新增 1 · 修改 0 · 移除 0')).toBeInTheDocument()
  })

  it('submits WeChat content without fetching the platform page', async () => {
    localStorage.setItem('access_token', 'existing-token')
    const user = userEvent.setup()
    const wechatSource = { ...source, id: '12', name: '质量工程前沿', source_type: 'wechat' }
    const fetchMock = mockSourceSequence([
      json({ id: 'user-2', username: 'maintainer', role: 'maintainer' }),
      json({ items: [], total: 0 }),
      json({ items: [wechatSource], total: 1 }),
      json({
        id: 31, source_id: 12, source_name: wechatSource.name, title: '智能体测试方法',
        url: 'https://mp.weixin.qq.com/s/example', summary: '智能体回归测试和安全评估实践。',
        published_at: null, fetched_at: '2026-09-02T00:00:00Z', analysis_status: 'pending',
        analysis_attempts: 0, testing_relevance_score: null, testing_value_score: null,
        analysis_error: null, analyzed_at: null,
      }, 201),
    ])

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
    expect(within(screen.getByRole('navigation', { name: '管理导航' })).getAllByRole('button').map((button) => button.getAttribute('aria-label'))).toEqual([
      '内容情报', 'GitHub 追踪', '信源管理', '采集管理', '数据库状态', '用户管理', '邮件推送',
    ])
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
    expect(screen.getByText('待分析')).toHaveClass('status-pending')
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

  it('shows filter reasons and lets an admin reanalyze selected content', async () => {
    localStorage.setItem('access_token', 'admin-token')
    const user = userEvent.setup()
    const filtered = {
      id: 21, source_id: 1, source_name: 'General AI News', title: 'Agent architecture update',
      url: 'https://example.com/21', summary: 'A potentially useful agent update',
      published_at: null, fetched_at: '2026-09-20T00:00:00Z', analysis_status: 'analyzed',
      analysis_disposition: 'filtered', filter_reason: 'model_irrelevant', analysis_attempts: 1,
      testing_relevance_score: 20, testing_value_score: 10, analysis_error: null,
      analyzed_at: '2026-09-20T01:00:00Z',
    }
    const pending = {
      ...filtered, analysis_status: 'pending', analysis_disposition: null, filter_reason: null,
      analysis_attempts: 0, testing_relevance_score: null, testing_value_score: null,
      analyzed_at: null,
    }
    const fetchMock = vi.spyOn(globalThis, 'fetch')
      .mockResolvedValueOnce(json({ id: 1, username: 'root', role: 'admin' }))
      .mockResolvedValueOnce(json({ items: [], total: 0 }))
      .mockResolvedValueOnce(json({ items: [filtered], total: 1 }))
      .mockResolvedValueOnce(json({ items: [source], total: 1 }))
      .mockResolvedValueOnce(json({ reanalyzed: 1 }))
      .mockResolvedValueOnce(json({ items: [pending], total: 1 }))

    render(<App />)
    await screen.findByRole('heading', { name: '内容情报' })
    await user.click(screen.getByRole('button', { name: '采集管理' }))
    expect(await screen.findByText('模型判定不相关')).toBeInTheDocument()
    await user.click(screen.getByLabelText('选择采集内容 Agent architecture update'))
    await user.click(screen.getByRole('button', { name: '重新分析 (1)' }))
    await user.click(await screen.findByRole('button', { name: '确认重新分析' }))

    await waitFor(() => expect(fetchMock).toHaveBeenCalledWith(
      '/api/v1/collected-content/bulk-reanalyze',
      {
        method: 'POST',
        headers: { Authorization: 'Bearer admin-token', 'Content-Type': 'application/json' },
        body: JSON.stringify({ content_ids: [21] }),
      },
    ))
    expect(await screen.findByText('待分析')).toBeInTheDocument()
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

  it.each([
    ['discovered', '关注', 'watched'],
    ['discovered', '忽略', 'ignored'],
    ['watched', '忽略', 'ignored'],
    ['ignored', '关注', 'watched'],
  ])('updates GitHub status from %s via %s with JSON headers', async (initialStatus, action, nextStatus) => {
    localStorage.setItem('access_token', 'existing-token')
    const user = userEvent.setup()
    const repo = {
      id: 7,
      full_name: 'acme/awesome-agent',
      description: 'An agent framework',
      html_url: 'https://github.com/acme/awesome-agent',
      primary_language: 'Python',
      topics: ['ai', 'agent'],
      stars: 120,
      forks: 8,
      status: initialStatus,
      momentum_score: 72.5,
      momentum_tier: 'new_notable',
      summary: null,
      repo_pushed_at: '2026-09-01T00:00:00Z',
      first_seen_at: '2026-09-10T00:00:00Z',
    }
    const fetchMock = vi.spyOn(globalThis, 'fetch')
      .mockResolvedValueOnce(json({ id: 'user-2', username: 'maintainer', role: 'maintainer' }))
      .mockResolvedValueOnce(json({ items: [], total: 0 }))
      .mockResolvedValueOnce(json({ items: [repo], total: 1 }))
      .mockResolvedValueOnce(json({ items: [], total: 0 }))
      .mockResolvedValueOnce(json({ topics: [] }))
      .mockResolvedValueOnce(json(exploration))
      .mockResolvedValueOnce(json({ ...repo, status: nextStatus }))
      .mockResolvedValueOnce(json({ items: [{ ...repo, status: nextStatus }], total: 1 }))

    render(<App />)
    await screen.findByRole('heading', { name: '内容情报' })
    await user.click(screen.getByRole('button', { name: 'GitHub 追踪' }))
    await user.click(screen.getByText('候选管理'))
    await screen.findByText('acme/awesome-agent')
    await user.click(screen.getByRole('button', { name: new RegExp(`${action}$`) }))

    await waitFor(() => expect(fetchMock).toHaveBeenCalledWith('/api/v1/github/repos/7/status', expect.objectContaining({
      method: 'PATCH',
      headers: expect.objectContaining({ 'Content-Type': 'application/json', Authorization: 'Bearer existing-token' }),
      body: JSON.stringify({ status: nextStatus }),
    })))
  })

  it('opens the latest GitHub research without triggering discovery or report generation', async () => {
    localStorage.setItem('access_token', 'existing-token')
    const fetchMock = mockGithubPage()
    render(<App />)
    await screen.findByRole('heading', { name: '内容情报' })
    await userEvent.click(screen.getByRole('button', { name: 'GitHub 追踪' }))
    expect(await screen.findByText('自动探索得到的项目')).toBeInTheDocument()
    expect(within(screen.getByRole('article')).getAllByRole('heading', { name: githubReport.title })).toHaveLength(1)
    expect(screen.getByText(/进入过历史报告的项目不会再次推荐/)).toBeInTheDocument()
    expect(screen.queryByRole('button', { name: /立即发现候选|生成今日日报/ })).not.toBeInTheDocument()
    expect(fetchMock.mock.calls.every(([, init]) => !init?.method || init.method === 'GET')).toBe(true)
    expect(screen.getByText('候选管理').closest('details')).not.toHaveAttribute('open')
  })

  it.each([
    ['never_run', '等待平台首次自动探索'],
    ['running', '平台正在探索和分析新项目'],
    ['completed', '本轮暂无符合条件的新项目'],
    ['failed', '本轮探索失败，平台会自动重试'],
  ])('shows the GitHub %s exploration state without exposing internal errors', async (status, message) => {
    localStorage.setItem('access_token', 'existing-token')
    mockGithubPage({ status: { ...exploration, status, error_message: 'secret-upstream-token' } as typeof exploration, reports: [] })
    render(<App />)
    await screen.findByRole('heading', { name: '内容情报' })
    await userEvent.click(screen.getByRole('button', { name: 'GitHub 追踪' }))
    expect(await screen.findByText(message)).toBeInTheDocument()
    expect(screen.queryByText('secret-upstream-token')).not.toBeInTheDocument()
    expect(screen.getByText(/最近探索/)).toBeInTheDocument()
    expect(screen.getByText(/下次探索/)).toBeInTheDocument()
  })

  it('refreshes completed research with GET requests only and reloads after changing pages', async () => {
    localStorage.setItem('access_token', 'existing-token')
    const fetchMock = mockGithubPage({ reports: [] })
    render(<App />)
    await screen.findByRole('heading', { name: '内容情报' })
    await userEvent.click(screen.getByRole('button', { name: 'GitHub 追踪' }))
    await screen.findByText('等待平台首次自动探索')
    fetchMock.mockImplementation(async (input) => {
      if (String(input) === '/api/v1/github/exploration') return json({ ...exploration, status: 'completed', report_id: 4 })
      if (String(input) === '/api/v1/github/reports') return json({ items: [githubReport], total: 1 })
      if (String(input) === '/api/v1/github/preferences') return json({ topics: [] })
      return json({ items: [], total: 0 })
    })
    await userEvent.click(screen.getByRole('button', { name: '刷新结果' }))
    expect(await screen.findByText('自动探索得到的项目')).toBeInTheDocument()
    expect(await screen.findByText('本轮研究已完成')).toBeInTheDocument()
    await userEvent.click(screen.getByRole('button', { name: '内容情报' }))
    await userEvent.click(screen.getByRole('button', { name: 'GitHub 追踪' }))
    await waitFor(() => expect(fetchMock.mock.calls.filter(([url]) => url === '/api/v1/github/exploration')).toHaveLength(3))
    expect(fetchMock.mock.calls.every(([, init]) => !init?.method || init.method === 'GET')).toBe(true)
  })

  it('keeps GitHub preferences and candidate mutations unavailable to viewers', async () => {
    localStorage.setItem('access_token', 'existing-token')
    mockGithubPage({ role: 'viewer' })
    render(<App />)
    await screen.findByRole('heading', { name: '内容情报' })
    await userEvent.click(screen.getByRole('button', { name: 'GitHub 追踪' }))
    await screen.findByText('自动探索得到的项目')
    expect(screen.getByText('testing')).toBeInTheDocument()
    expect(screen.queryByRole('button', { name: /关注主题|关注$|忽略$/ })).not.toBeInTheDocument()
    expect(screen.queryByText('候选管理')).not.toBeInTheDocument()
  })

  it('opens historical research and downloads the selected Markdown', async () => {
    localStorage.setItem('access_token', 'existing-token')
    const olderReport = { ...githubReport, id: 3, title: '历史研究 2026-10-08', body_markdown: '# 历史研究正文', generated_at: '2026-10-08T00:00:00Z' }
    mockGithubPage({ reports: [githubReport, olderReport] })
    const createObjectURL = vi.fn((_blob: Blob) => 'blob:github-report')
    const revokeObjectURL = vi.fn()
    Object.defineProperty(URL, 'createObjectURL', { configurable: true, value: createObjectURL })
    Object.defineProperty(URL, 'revokeObjectURL', { configurable: true, value: revokeObjectURL })
    const clickSpy = vi.spyOn(HTMLAnchorElement.prototype, 'click').mockImplementation(() => {})
    render(<App />)
    await screen.findByRole('heading', { name: '内容情报' })
    await userEvent.click(screen.getByRole('button', { name: 'GitHub 追踪' }))
    await screen.findByText('自动探索得到的项目')
    await userEvent.click(screen.getByText('历史报告'))
    await userEvent.click(screen.getByRole('button', { name: '查看 历史研究 2026-10-08' }))
    const dialog = (await screen.findByText('历史研究正文')).closest('[role="dialog"]') as HTMLElement
    expect(dialog).toBeInTheDocument()
    expect(within(dialog).getByText('历史研究正文')).toBeInTheDocument()
    await userEvent.click(within(dialog).getByRole('button', { name: /下载 Markdown/ }))
    expect(createObjectURL).toHaveBeenCalledOnce()
    const blob = createObjectURL.mock.calls[0][0] as Blob
    const contents = await new Promise<string>((resolve) => {
      const reader = new FileReader()
      reader.onload = () => resolve(String(reader.result))
      reader.readAsText(blob)
    })
    expect(contents).toBe('# 历史研究正文')
    expect(clickSpy).toHaveBeenCalledOnce()
    expect(clickSpy.mock.instances[0]).toHaveAttribute('download', 'github-report-3.md')
    await waitFor(() => expect(revokeObjectURL).toHaveBeenCalledWith('blob:github-report'))
  })
  it.each(['button', 'enter'])('saves AI testing as a GitHub topic with JSON headers (%s)', async (method) => {
    localStorage.setItem('access_token', 'existing-token')
    const user = userEvent.setup()
    const fetchMock = vi.spyOn(globalThis, 'fetch')
      .mockResolvedValueOnce(json({ id: 'user-2', username: 'maintainer', role: 'maintainer' }))
      .mockResolvedValueOnce(json({ items: [], total: 0 }))
      .mockResolvedValueOnce(json({ items: [], total: 0 }))
      .mockResolvedValueOnce(json({ items: [], total: 0 }))
      .mockResolvedValueOnce(json({ topics: [] }))
      .mockResolvedValueOnce(json(exploration))
      .mockResolvedValueOnce(json({ topics: ['AI testing'] }))

    render(<App />)
    await screen.findByRole('heading', { name: '内容情报' })
    await user.click(screen.getByRole('button', { name: 'GitHub 追踪' }))
    await user.click(await screen.findByRole('button', { name: /关注主题/ }))
    await user.type(screen.getByRole('textbox', { name: '新增关注主题' }), ' AI testing ')
    if (method === 'enter') await user.keyboard('{Enter}')
    else await user.click(screen.getByRole('button', { name: /添加主题/ }))
    expect(screen.getByText('新增 1 · 修改 0 · 移除 0')).toBeInTheDocument()
    await user.click(await screen.findByRole('button', { name: '保存关注主题' }))

    await waitFor(() => expect(fetchMock).toHaveBeenCalledWith('/api/v1/github/preferences', expect.objectContaining({
      method: 'PUT',
      headers: expect.objectContaining({ 'Content-Type': 'application/json', Authorization: 'Bearer existing-token' }),
      body: JSON.stringify({ topics: ['AI testing'] }),
    })))
    await waitFor(() => expect(screen.queryByRole('button', { name: '保存关注主题' })).not.toBeInTheDocument())
  })

  it('keeps saved GitHub topics when backspace clears the separate add input', async () => {
    localStorage.setItem('access_token', 'existing-token')
    const user = userEvent.setup()
    const fetchMock = mockGithubPage({ topics: ['testing', 'rag'] })
    const editor = await openGithubTopicsEditor()
    const input = editor.getByRole('textbox', { name: '新增关注主题' })
    await user.type(input, 'draft')
    await user.clear(input)
    await user.keyboard('{Backspace}')
    const list = within(editor.getByRole('list', { name: '关注主题列表' }))
    expect(list.getAllByRole('listitem')).toHaveLength(2)
    expect(list.getByText('testing')).toBeInTheDocument()
    expect(list.getByText('rag')).toBeInTheDocument()
    expect(editor.queryByRole('button', { name: /移除/ })).not.toBeInTheDocument()
    expect(editor.getByText('新增 0 · 修改 0 · 移除 0')).toBeInTheDocument()
    expect(editor.getByRole('button', { name: '保存关注主题' })).toBeDisabled()
    expect(fetchMock.mock.calls.filter(([, init]) => init?.method === 'PUT')).toHaveLength(0)
  })

  it('stages GitHub topic removal with undo and persists it only after saving', async () => {
    localStorage.setItem('access_token', 'existing-token')
    const user = userEvent.setup()
    const fetchMock = mockGithubPage({ topics: ['testing', 'rag'] })
    const editor = await openGithubTopicsEditor()
    const removeTesting = async () => {
      await user.click(editor.getByRole('button', { name: '更多操作 testing' }))
      await user.click(await screen.findByRole('menuitem', { name: /移除主题/ }))
    }
    await removeTesting()
    expect(editor.getByText('待移除')).toBeInTheDocument()
    expect(editor.getByText('新增 0 · 修改 0 · 移除 1')).toBeInTheDocument()
    expect(fetchMock.mock.calls.filter(([, init]) => init?.method === 'PUT')).toHaveLength(0)
    expect(screen.getByText('testing', { selector: '.github-focus .ant-tag' })).toBeInTheDocument()
    await user.click(editor.getByRole('button', { name: '撤销移除 testing' }))
    expect(editor.queryByText('待移除')).not.toBeInTheDocument()
    expect(editor.getByRole('button', { name: '保存关注主题' })).toBeDisabled()
    await removeTesting()
    await user.click(editor.getByRole('button', { name: '保存关注主题' }))
    await waitFor(() => expect(fetchMock).toHaveBeenCalledWith('/api/v1/github/preferences', expect.objectContaining({
      method: 'PUT', body: JSON.stringify({ topics: ['rag'] }),
    })))
    await waitFor(() => expect(screen.queryByRole('dialog', { name: /关注主题/ })).not.toBeInTheDocument())
    expect(screen.queryByText('testing', { selector: '.github-focus .ant-tag' })).not.toBeInTheDocument()
  })

  it('reviews additions, edits and removals before saving the final GitHub topics', async () => {
    localStorage.setItem('access_token', 'existing-token')
    const user = userEvent.setup()
    const fetchMock = mockGithubPage({ topics: ['testing', 'playwright'] })
    const editor = await openGithubTopicsEditor()
    await user.click(editor.getByRole('button', { name: '编辑主题 testing' }))
    expect(editor.getByRole('button', { name: '保存关注主题' })).toBeDisabled()
    const editInput = editor.getByRole('textbox', { name: '编辑关注主题' })
    await user.clear(editInput)
    await user.type(editInput, ' AI testing ')
    await user.click(editor.getByRole('button', { name: '确认修改' }))
    await user.type(editor.getByRole('textbox', { name: '新增关注主题' }), 'rag')
    expect(editor.getByRole('button', { name: '保存关注主题' })).toBeDisabled()
    await user.click(editor.getByRole('button', { name: /添加主题/ }))
    await user.click(editor.getByRole('button', { name: '更多操作 playwright' }))
    await user.click(await screen.findByRole('menuitem', { name: /移除主题/ }))
    expect(editor.getByText('新增 1 · 修改 1 · 移除 1')).toBeInTheDocument()
    await user.click(editor.getByRole('button', { name: '保存关注主题' }))
    await waitFor(() => expect(fetchMock).toHaveBeenCalledWith('/api/v1/github/preferences', expect.objectContaining({
      method: 'PUT', body: JSON.stringify({ topics: ['AI testing', 'rag'] }),
    })))
  })

  it('validates duplicate and empty GitHub topic edits without losing saved topics', async () => {
    localStorage.setItem('access_token', 'existing-token')
    const user = userEvent.setup()
    mockGithubPage({ topics: ['testing', 'rag'] })
    const editor = await openGithubTopicsEditor()
    const addInput = editor.getByRole('textbox', { name: '新增关注主题' })
    await user.type(addInput, ' testing ')
    await user.click(editor.getByRole('button', { name: /添加主题/ }))
    expect(editor.getByRole('alert')).toHaveTextContent('该主题已存在')
    await user.clear(addInput)
    await user.click(editor.getByRole('button', { name: '编辑主题 testing' }))
    const editInput = editor.getByRole('textbox', { name: '编辑关注主题' })
    await user.clear(editInput)
    await user.click(editor.getByRole('button', { name: '确认修改' }))
    expect(editor.getByRole('alert')).toHaveTextContent('主题不能为空')
    await user.type(editInput, ' rag ')
    await user.click(editor.getByRole('button', { name: '确认修改' }))
    expect(editor.getByRole('alert')).toHaveTextContent('该主题已存在')
    await user.click(editor.getByRole('button', { name: '取消编辑' }))
    expect(editor.getByText('新增 0 · 修改 0 · 移除 0')).toBeInTheDocument()
    expect(editor.getByRole('list', { name: '关注主题列表' })).toHaveTextContent('testing')
    expect(editor.getByRole('button', { name: '保存关注主题' })).toBeDisabled()
  })

  it('discards unsaved GitHub topic changes when the editor is cancelled', async () => {
    localStorage.setItem('access_token', 'existing-token')
    const user = userEvent.setup()
    const fetchMock = mockGithubPage({ topics: ['testing'] })
    const editor = await openGithubTopicsEditor()
    await user.type(editor.getByRole('textbox', { name: '新增关注主题' }), 'rag')
    await user.click(editor.getByRole('button', { name: /添加主题/ }))
    await user.click(editor.getByRole('button', { name: '更多操作 testing' }))
    await user.click(await screen.findByRole('menuitem', { name: /移除主题/ }))
    await user.click(editor.getByRole('button', { name: '取消' }))
    await waitFor(() => expect(screen.queryByRole('dialog', { name: /关注主题/ })).not.toBeInTheDocument())
    await user.click(screen.getByRole('button', { name: /关注主题/ }))
    const reopened = within(await screen.findByRole('dialog', { name: /关注主题/ }))
    expect(reopened.getByRole('list', { name: '关注主题列表' })).toHaveTextContent('testing')
    expect(reopened.queryByText('rag')).not.toBeInTheDocument()
    expect(reopened.queryByText('待移除')).not.toBeInTheDocument()
    expect(reopened.getByRole('button', { name: '保存关注主题' })).toBeDisabled()
    expect(fetchMock.mock.calls.filter(([, init]) => init?.method === 'PUT')).toHaveLength(0)
  })

  it('renders report markdown with clickable repository link', async () => {
    localStorage.setItem('access_token', 'existing-token')
    const user = userEvent.setup()
    const report = {
      id: 4,
      report_type: 'daily',
      title: 'GitHub 项目日报 2026-09-10',
      body_markdown: '# GitHub 项目日报\n\n## acme/deep\n- 星数 300 · 语言 Python · 测试价值 85/100\n- 仓库地址：https://github.com/acme/deep\n\n**项目摘要**\n一个深度摘要\n\n**测试价值分析**\n价值分析\n\n**应用场景推荐**\n- 回归测试\n\n**落地建议**\n- 试点落地\n',
      status: 'ready',
      generated_at: '2026-09-10T00:00:00Z',
      items: [],
    }
    vi.spyOn(globalThis, 'fetch')
      .mockResolvedValueOnce(json({ id: 'user-2', username: 'maintainer', role: 'maintainer' }))
      .mockResolvedValueOnce(json({ items: [], total: 0 }))
      .mockResolvedValueOnce(json({ items: [], total: 0 }))
      .mockResolvedValueOnce(json({ items: [report], total: 1 }))
      .mockResolvedValueOnce(json({ topics: [] }))
      .mockResolvedValueOnce(json(exploration))

    render(<App />)
    await screen.findByRole('heading', { name: '内容情报' })
    await user.click(screen.getByRole('button', { name: 'GitHub 追踪' }))
    await screen.findByRole('heading', { name: 'GitHub 项目日报 2026-09-10', level: 3 })

    const link = await screen.findByRole('link', { name: 'https://github.com/acme/deep' })
    expect(link).toHaveAttribute('href', 'https://github.com/acme/deep')
    expect(screen.getByText('项目摘要')).toBeInTheDocument()
    expect(screen.getByText('一个深度摘要')).toBeInTheDocument()
    expect(screen.getByText('测试价值分析')).toBeInTheDocument()
    expect(screen.getByText('应用场景推荐')).toBeInTheDocument()
    expect(screen.getByText('落地建议')).toBeInTheDocument()
  })
})
