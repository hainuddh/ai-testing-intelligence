import { useEffect, useState } from 'react'
import {
  ArrowRightOutlined,
  DatabaseOutlined,
  DeleteOutlined,
  EditOutlined,
  FileSearchOutlined,
  GlobalOutlined,
  LogoutOutlined,
  PlusOutlined,
  RadarChartOutlined,
  TeamOutlined,
} from '@ant-design/icons'
import {
  Alert,
  Button,
  Drawer,
  Form,
  Input,
  Modal,
  Pagination,
  Select,
  Spin,
  Switch,
  Tag,
} from 'antd'
import type { FormInstance } from 'antd'

type Role = 'viewer' | 'maintainer' | 'admin'
type User = { id: string; username: string; role: Role; is_active?: boolean }
type Source = {
  id: string
  name: string
  source_type: string
  homepage_url: string | null
  description: string | null
  languages: string[]
  trust_level: number
  topics: string[]
}
type SourceForm = Omit<Source, 'id' | 'languages' | 'topics'> & { languages: string; topics: string }
type ContentItem = {
  id: string
  source_id: string
  title: string
  url: string
  summary: string
  body: string
  published_at: string | null
  fetched_at: string
}
type Endpoint = {
  id: string
  source_id: string
  name: string
  endpoint_type: 'rss' | 'web'
  url: string
  fetch_interval_minutes: number
  max_items_per_run: number
  enabled: boolean
  health_status: string
}
type EndpointForm = Omit<Endpoint, 'id' | 'source_id' | 'enabled' | 'health_status'>
type UserForm = { username: string; password?: string; role: Role; is_active: boolean }
type DatabaseCounts = { dialect: string; users: number; sources: number; source_endpoints: number; content_items: number; fetch_runs: number }
type Tab = 'content' | 'sources' | 'users' | 'database'

const typeLabels: Record<string, string> = { website: '网站', newsletter: '通讯', rss: 'RSS', social: '社交媒体' }
const trustLabels: Record<number, string> = { 5: '最高可信', 4: '高可信', 3: '待验证', 2: '观察中', 1: '低可信' }
const roleLabels: Record<Role, string> = { viewer: '浏览者', maintainer: '维护者', admin: '管理员' }
const splitValues = (value: string) => value.split(/[,，]/).map((item) => item.trim()).filter(Boolean)
const list = <T,>(data: T[] | { items: T[] }) => Array.isArray(data) ? data : data.items
const dateText = (value: string | null) => value ? new Intl.DateTimeFormat('zh-CN', { dateStyle: 'medium' }).format(new Date(value)) : '时间未知'
const dateBoundary = (value: string, endOfDay = false) => {
  const [year, month, day] = value.split('-').map(Number)
  return new Date(year, month - 1, day, endOfDay ? 23 : 0, endOfDay ? 59 : 0, endOfDay ? 59 : 0, endOfDay ? 999 : 0).toISOString()
}

export default function App() {
  const [token, setToken] = useState(() => localStorage.getItem('access_token'))
  const [me, setMe] = useState<User | null>(null)
  const [tab, setTab] = useState<Tab>('content')
  const [sources, setSources] = useState<Source[]>([])
  const [sourceTotal, setSourceTotal] = useState(0)
  const [content, setContent] = useState<ContentItem[]>([])
  const [contentTotal, setContentTotal] = useState(0)
  const [contentPage, setContentPage] = useState(1)
  const [query, setQuery] = useState('')
  const [startDate, setStartDate] = useState('')
  const [endDate, setEndDate] = useState('')
  const [users, setUsers] = useState<User[]>([])
  const [database, setDatabase] = useState<DatabaseCounts | null>(null)
  const [loading, setLoading] = useState(false)
  const [saving, setSaving] = useState(false)
  const [error, setError] = useState('')
  const [sourceDrawer, setSourceDrawer] = useState(false)
  const [userDrawer, setUserDrawer] = useState(false)
  const [endpointDrawer, setEndpointDrawer] = useState(false)
  const [endpointSource, setEndpointSource] = useState<Source | null>(null)
  const [endpoints, setEndpoints] = useState<Endpoint[]>([])
  const [editingSource, setEditingSource] = useState<Source | null>(null)
  const [editingUser, setEditingUser] = useState<User | null>(null)
  const [sourceForm] = Form.useForm<SourceForm>()
  const [userForm] = Form.useForm<UserForm>()
  const [endpointForm] = Form.useForm<EndpointForm>()
  const canManageSources = me?.role === 'maintainer' || me?.role === 'admin'
  const isAdmin = me?.role === 'admin'

  const logout = () => {
    localStorage.removeItem('access_token')
    setToken(null)
    setMe(null)
    setError('')
  }

  const request = async <T,>(path: string, options: RequestInit = {}): Promise<T> => {
    if (!token) throw new Error('登录状态已失效')
    try {
      const response = await fetch(path, {
        ...options,
        headers: { Authorization: `Bearer ${token}`, ...options.headers },
      })
      if (response.status === 401) {
        logout()
        throw new Error('登录状态已失效，请重新登录')
      }
      if (!response.ok) throw new Error(`请求失败 (${response.status})`)
      if (response.status === 204) return undefined as T
      return await response.json() as T
    } catch (requestError) {
      if (requestError instanceof Error) throw requestError
      throw new Error('网络连接异常，请稍后重试')
    }
  }

  useEffect(() => {
    if (!token) return
    let active = true
    setLoading(true)
    setError('')
    request<User>('/api/v1/auth/me')
      .then((profile) => { if (active) setMe(profile) })
      .catch((requestError) => { if (active) setError(requestError.message) })
      .finally(() => { if (active) setLoading(false) })
    return () => { active = false }
  }, [token])

  const loadContent = async (page = contentPage, search = query, start = startDate, end = endDate) => {
    setLoading(true)
    setError('')
    try {
      const params = new URLSearchParams({ offset: String((page - 1) * 12), limit: '12' })
      if (search) params.set('query', search)
      if (start) params.set('start_at', dateBoundary(start))
      if (end) params.set('end_at', dateBoundary(end, true))
      const data = await request<{ items: ContentItem[]; total: number }>(`/api/v1/content?${params}`)
      setContent(data.items)
      setContentTotal(data.total)
    } catch (requestError) {
      setError(requestError instanceof Error ? requestError.message : '加载内容失败')
    } finally {
      setLoading(false)
    }
  }

  const loadSources = async () => {
    setLoading(true)
    setError('')
    try {
      const data = await request<Source[] | { items: Source[]; total?: number }>('/api/v1/sources?limit=200')
      const items = list(data)
      setSources(items)
      setSourceTotal(Array.isArray(data) ? data.length : data.total ?? items.length)
    } catch (requestError) {
      setError(requestError instanceof Error ? requestError.message : '加载信源失败')
    } finally {
      setLoading(false)
    }
  }

  const loadUsers = async () => {
    setLoading(true)
    setError('')
    try {
      setUsers(list(await request<User[] | { items: User[] }>('/api/v1/users?limit=200')))
    } catch (requestError) {
      setError(requestError instanceof Error ? requestError.message : '加载用户失败')
    } finally {
      setLoading(false)
    }
  }

  const loadDatabase = async () => {
    setLoading(true)
    setError('')
    try {
      const data = await request<DatabaseCounts | ({ dialect: string; row_counts: Omit<DatabaseCounts, 'dialect'> })>('/api/v1/database/status')
      setDatabase('row_counts' in data ? { dialect: data.dialect, ...data.row_counts } : data)
    } catch (requestError) {
      setError(requestError instanceof Error ? requestError.message : '加载数据库状态失败')
    } finally {
      setLoading(false)
    }
  }

  useEffect(() => {
    if (!me) return
    if (tab === 'content') void loadContent()
    if (tab === 'sources') void loadSources()
    if (tab === 'users' && me.role === 'admin') void loadUsers()
    if (tab === 'database' && me.role === 'admin') void loadDatabase()
  }, [me, tab])

  const login = async (values: { username: string; password: string }) => {
    setSaving(true)
    setError('')
    try {
      const response = await fetch('/api/v1/auth/login', {
        method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(values),
      })
      if (!response.ok) throw new Error('登录失败，请检查账号与密码')
      const data = await response.json() as { access_token: string }
      localStorage.setItem('access_token', data.access_token)
      setToken(data.access_token)
    } catch (requestError) {
      setError(requestError instanceof Error ? requestError.message : '网络连接异常，请稍后重试')
    } finally {
      setSaving(false)
    }
  }

  const openSource = (source?: Source) => {
    setEditingSource(source ?? null)
    sourceForm.resetFields()
    sourceForm.setFieldsValue(source ? { ...source, languages: source.languages.join(', '), topics: source.topics.join(', ') } : { trust_level: 3 })
    setSourceDrawer(true)
  }

  const saveSource = async (values: SourceForm) => {
    setSaving(true)
    setError('')
    try {
      const payload = { ...values, languages: splitValues(values.languages), topics: splitValues(values.topics) }
      const path = editingSource ? `/api/v1/sources/${editingSource.id}` : '/api/v1/sources'
      await request<Source>(path, {
        method: editingSource ? 'PATCH' : 'POST',
        headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(payload),
      })
      setSourceDrawer(false)
      await loadSources()
    } catch (requestError) {
      setError(requestError instanceof Error ? requestError.message : '保存信源失败')
    } finally {
      setSaving(false)
    }
  }

  const removeSource = async (source: Source) => {
    setError('')
    try {
      await request(`/api/v1/sources/${source.id}`, { method: 'DELETE' })
      setSources((current) => current.filter((item) => item.id !== source.id))
      setSourceTotal((current) => Math.max(0, current - 1))
    } catch (requestError) {
      setError(requestError instanceof Error ? requestError.message : '删除信源失败')
    }
  }

  const openEndpoints = async (source: Source) => {
    setEndpointSource(source)
    setEndpointDrawer(true)
    setLoading(true)
    setError('')
    try {
      setEndpoints(await request<Endpoint[]>(`/api/v1/sources/${source.id}/endpoints`))
    } catch (requestError) {
      setError(requestError instanceof Error ? requestError.message : '加载采集端点失败')
    } finally {
      setLoading(false)
    }
  }

  const saveEndpoint = async (values: EndpointForm) => {
    if (!endpointSource) return
    setSaving(true)
    setError('')
    try {
      const created = await request<Endpoint>(`/api/v1/sources/${endpointSource.id}/endpoints`, {
        method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(values),
      })
      setEndpoints((current) => [created, ...current])
      endpointForm.resetFields()
    } catch (requestError) {
      setError(requestError instanceof Error ? requestError.message : '保存采集端点失败')
    } finally {
      setSaving(false)
    }
  }

  const removeEndpoint = async (endpoint: Endpoint) => {
    if (!endpointSource) return
    try {
      await request(`/api/v1/sources/${endpointSource.id}/endpoints/${endpoint.id}`, { method: 'DELETE' })
      setEndpoints((current) => current.filter((item) => item.id !== endpoint.id))
    } catch (requestError) {
      setError(requestError instanceof Error ? requestError.message : '删除采集端点失败')
    }
  }

  const openUser = (user?: User) => {
    setEditingUser(user ?? null)
    userForm.resetFields()
    userForm.setFieldsValue(user ? { username: user.username, role: user.role, is_active: user.is_active ?? true } : { role: 'viewer', is_active: true })
    setUserDrawer(true)
  }

  const saveUser = async (values: UserForm) => {
    setSaving(true)
    setError('')
    try {
      const path = editingUser ? `/api/v1/users/${editingUser.id}` : '/api/v1/users'
      await request<User>(path, {
        method: editingUser ? 'PATCH' : 'POST',
        headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(values),
      })
      setUserDrawer(false)
      await loadUsers()
    } catch (requestError) {
      setError(requestError instanceof Error ? requestError.message : '保存用户失败')
    } finally {
      setSaving(false)
    }
  }

  const removeUser = async (user: User) => {
    setError('')
    try {
      await request(`/api/v1/users/${user.id}`, { method: 'DELETE' })
      setUsers((current) => current.filter((item) => item.id !== user.id))
    } catch (requestError) {
      setError(requestError instanceof Error ? requestError.message : '删除用户失败')
    }
  }

  if (!token) return <Login error={error} loading={saving} onLogin={login} />
  if (!me && loading) return <div className="boot-state"><Spin size="large" /><span>正在确认雷达权限...</span></div>

  const tabs: { id: Tab; label: string; icon: React.ReactNode; admin?: boolean }[] = [
    { id: 'content', label: '内容情报', icon: <FileSearchOutlined /> },
    { id: 'sources', label: '信源管理', icon: <GlobalOutlined /> },
    { id: 'users', label: '用户管理', icon: <TeamOutlined />, admin: true },
    { id: 'database', label: '数据库状态', icon: <DatabaseOutlined />, admin: true },
  ]

  return (
    <main className="dashboard-shell">
      <header className="topbar">
        <div className="brand"><RadarChartOutlined /> SIGNAL ATLAS</div>
        <div className="topbar-actions">
          {me && <span className="identity"><strong>{me.username}</strong><small>{roleLabels[me.role]}</small></span>}
          <span className="live-status"><i /> LIVE SCAN</span>
          <Button type="text" icon={<LogoutOutlined />} onClick={logout} aria-label="退出登录" />
        </div>
      </header>
      <nav className="command-nav" aria-label="管理导航">
        {tabs.filter((item) => !item.admin || isAdmin).map((item) => (
          <button key={item.id} aria-label={item.label} className={tab === item.id ? 'active' : ''} onClick={() => { setError(''); setTab(item.id) }}>
            {item.icon}<span>{item.label}</span>
          </button>
        ))}
      </nav>
      <section className="dashboard-content">
        {error && <Alert className="dashboard-alert" type="error" message={error} showIcon closable onClose={() => setError('')} />}
        {tab === 'content' && <ContentView items={content} total={contentTotal} page={contentPage} loading={loading} query={query} startDate={startDate} endDate={endDate} onQuery={setQuery} onStartDate={setStartDate} onEndDate={setEndDate} onSearch={() => { setContentPage(1); void loadContent(1, query) }} onReset={() => { setQuery(''); setStartDate(''); setEndDate(''); setContentPage(1); void loadContent(1, '', '', '') }} onPage={(page) => { setContentPage(page); void loadContent(page) }} />}
        {tab === 'sources' && <SourcesView sources={sources} total={sourceTotal} loading={loading} canManage={canManageSources} onAdd={() => openSource()} onEndpoints={(source) => void openEndpoints(source)} onEdit={openSource} onDelete={(source) => Modal.confirm({ title: `删除信源「${source.name}」？`, content: '删除后无法恢复，并会清除关联内容。', okText: '确认删除', cancelText: '取消', okButtonProps: { danger: true }, onOk: () => removeSource(source) })} />}
        {tab === 'users' && isAdmin && <UsersView users={users} loading={loading} me={me} onAdd={() => openUser()} onEdit={openUser} onDelete={(user) => Modal.confirm({ title: `删除用户「${user.username}」？`, content: '该用户将立即失去访问权限。', okText: '确认删除', cancelText: '取消', okButtonProps: { danger: true }, onOk: () => removeUser(user) })} />}
        {tab === 'database' && isAdmin && <DatabaseView data={database} loading={loading} />}
      </section>
      <SourceDrawer open={sourceDrawer} editing={editingSource} form={sourceForm} saving={saving} onClose={() => setSourceDrawer(false)} onSave={saveSource} />
      <UserDrawer open={userDrawer} editing={editingUser} form={userForm} saving={saving} error={error} onClose={() => setUserDrawer(false)} onSave={saveUser} />
      <EndpointDrawer open={endpointDrawer} source={endpointSource} endpoints={endpoints} form={endpointForm} saving={saving} canManage={canManageSources} onClose={() => setEndpointDrawer(false)} onSave={saveEndpoint} onDelete={removeEndpoint} />
    </main>
  )
}

function Login({ error, loading, onLogin }: { error: string; loading: boolean; onLogin: (values: { username: string; password: string }) => void }) {
  return <main className="login-shell">
    <section className="login-story">
      <div className="brand"><RadarChartOutlined /> SIGNAL ATLAS</div>
      <div className="radar-visual" aria-hidden="true"><i className="radar-sweep" /><i className="radar-ping ping-one" /><i className="radar-ping ping-two" /></div>
      <div className="story-copy"><span className="eyebrow">TECH INTELLIGENCE / 01</span><h1>在噪声成为趋势前，<br />捕捉它。</h1><p>持续校准高价值技术信源，让每一次扫描都有方向。</p></div>
    </section>
    <section className="login-panel"><div className="login-card">
      <span className="status-line"><i /> RADAR ONLINE</span><h2>分析员登录</h2><p>进入你的专属情报扇区</p>
      {error && <Alert type="error" message={error} showIcon />}
      <Form layout="vertical" requiredMark={false} onFinish={onLogin}>
        <Form.Item label="用户名" name="username" rules={[{ required: true, message: '请输入用户名' }]}><Input size="large" autoComplete="username" placeholder="analyst" /></Form.Item>
        <Form.Item label="密码" name="password" rules={[{ required: true, message: '请输入密码' }]}><Input.Password size="large" autoComplete="current-password" placeholder="输入访问口令" /></Form.Item>
        <Button htmlType="submit" type="primary" size="large" block loading={loading} aria-label="进入雷达">进入雷达 <ArrowRightOutlined /></Button>
      </Form><small>ENCRYPTED ACCESS · TLS 1.3</small>
    </div></section>
  </main>
}

function PageIntro({ kicker, title, copy, action }: { kicker: string; title: string; copy: string; action?: React.ReactNode }) {
  return <div className="page-intro"><div><span className="eyebrow">{kicker}</span><h1>{title}</h1><p>{copy}</p></div>{action}</div>
}

function ContentView({ items, total, page, loading, query, startDate, endDate, onQuery, onStartDate, onEndDate, onSearch, onReset, onPage }: { items: ContentItem[]; total: number; page: number; loading: boolean; query: string; startDate: string; endDate: string; onQuery: (value: string) => void; onStartDate: (value: string) => void; onEndDate: (value: string) => void; onSearch: () => void; onReset: () => void; onPage: (page: number) => void }) {
  return <>
    <PageIntro kicker="INTELLIGENCE FEED / LIVE" title="内容情报" copy="聚合监听网络中的最新信号，快速定位值得跟进的变化。" />
    <div className="feed-tools">
      <Input.Search value={query} onChange={(event) => onQuery(event.target.value)} onSearch={onSearch} enterButton="检索" placeholder="检索标题、摘要或正文" aria-label="检索内容" />
      <div className="date-filter"><label>开始日期<input type="date" value={startDate} max={endDate || undefined} onChange={(event) => onStartDate(event.target.value)} /></label><i>至</i><label>结束日期<input type="date" value={endDate} min={startDate || undefined} onChange={(event) => onEndDate(event.target.value)} /></label><Button onClick={onSearch}>应用筛选</Button>{(query || startDate || endDate) && <Button type="text" onClick={onReset}>重置</Button>}</div>
      <span>{total} 条情报</span>
    </div>
    {loading ? <Loading text="正在扫描情报流..." /> : items.length === 0 ? <Empty icon={<FileSearchOutlined />} title="当前扇区没有匹配的情报。" /> : <>
      <div className="content-grid">{items.map((item) => <article className="content-card" key={item.id}>
        <div className="content-meta"><span>{dateText(item.published_at || item.fetched_at)}</span><span>SOURCE / {item.source_id}</span></div>
        <h2><a href={item.url} target="_blank" rel="noreferrer">{item.title}</a></h2>
        <p>{item.summary || item.body || '暂无摘要'}</p><a className="read-link" href={item.url} target="_blank" rel="noreferrer">查看原文 <ArrowRightOutlined /></a>
      </article>)}</div>
      {total > 12 && <Pagination className="radar-pagination" current={page} total={total} pageSize={12} showSizeChanger={false} onChange={onPage} />}
    </>}
  </>
}

function SourcesView({ sources, total, loading, canManage, onAdd, onEndpoints, onEdit, onDelete }: { sources: Source[]; total: number; loading: boolean; canManage: boolean; onAdd: () => void; onEndpoints: (source: Source) => void; onEdit: (source: Source) => void; onDelete: (source: Source) => void }) {
  return <>
    <PageIntro kicker="SOURCE NETWORK / ACTIVE" title="信源管理" copy="编排可信技术信号，构建你的持续监听网络。" action={canManage && <Button type="primary" size="large" icon={<PlusOutlined />} onClick={onAdd} aria-label="新增信源">新增信源</Button>} />
    <div className="metric-strip"><div><strong>{String(total).padStart(2, '0')}</strong><span>监听节点</span></div><div><strong>{sources.filter((item) => item.trust_level >= 4).length}</strong><span>高可信信号</span></div><div className="scan-line"><span>NETWORK COVERAGE</span><i /></div></div>
    {loading ? <Loading text="正在校准信源网络..." /> : sources.length === 0 ? <Empty icon={<RadarChartOutlined />} title="暂无信源监听点。" /> : <div className="source-grid">{sources.map((source, index) => <article className="source-card" key={source.id}>
      <div className="card-index">{String(index + 1).padStart(2, '0')}</div>
      <div className="source-heading"><div className="source-icon"><GlobalOutlined /></div><div><span>{typeLabels[source.source_type] ?? source.source_type}</span><h2>{source.name}</h2></div></div>
      <p>{source.description || '暂无描述'}</p>{source.homepage_url && <a href={source.homepage_url} target="_blank" rel="noreferrer">{source.homepage_url}</a>}
      <div className="tag-row"><Tag color={source.trust_level >= 4 ? 'green' : 'default'}>{trustLabels[source.trust_level] ?? source.trust_level}</Tag>{source.languages.map((value) => <Tag key={value}>{value.toUpperCase()}</Tag>)}{source.topics.map((value) => <Tag key={value}>#{value}</Tag>)}</div>
      <div className="card-actions"><Button type="text" icon={<RadarChartOutlined />} onClick={() => onEndpoints(source)}>采集端点</Button>{canManage && <><Button type="text" icon={<EditOutlined />} onClick={() => onEdit(source)}>编辑</Button><Button type="text" danger icon={<DeleteOutlined />} onClick={() => onDelete(source)}>删除</Button></>}</div>
    </article>)}</div>}
  </>
}

function UsersView({ users, loading, me, onAdd, onEdit, onDelete }: { users: User[]; loading: boolean; me: User; onAdd: () => void; onEdit: (user: User) => void; onDelete: (user: User) => void }) {
  return <>
    <PageIntro kicker="ACCESS CONTROL / ADMIN" title="用户管理" copy="分配雷达访问权限，并保持操作边界清晰。" action={<Button type="primary" size="large" icon={<PlusOutlined />} onClick={onAdd} aria-label="新增用户">新增用户</Button>} />
    {loading ? <Loading text="正在同步访问名单..." /> : <div className="user-table" role="table" aria-label="用户列表">
      <div className="user-row user-head" role="row"><span>分析员</span><span>权限</span><span>标识</span><span>操作</span></div>
      {users.map((user) => <div className="user-row" role="row" key={user.id}><strong>{user.username}</strong><span><Tag color={user.role === 'admin' ? 'green' : 'default'}>{roleLabels[user.role]}</Tag>{user.is_active === false && <Tag>已停用</Tag>}</span><code>{user.id}</code><span className="row-actions"><Button type="text" icon={<EditOutlined />} aria-label={`编辑 ${user.username}`} onClick={() => onEdit(user)} /><Button type="text" danger icon={<DeleteOutlined />} disabled={user.id === me.id} aria-label={`删除 ${user.username}`} onClick={() => onDelete(user)} /></span></div>)}
    </div>}
  </>
}

function DatabaseView({ data, loading }: { data: DatabaseCounts | null; loading: boolean }) {
  const metrics: [keyof Omit<DatabaseCounts, 'dialect'>, string][] = [['users', '用户'], ['sources', '信源'], ['source_endpoints', '采集端点'], ['content_items', '内容条目'], ['fetch_runs', '采集运行']]
  return <><PageIntro kicker="SYSTEM TELEMETRY / ADMIN" title="数据库状态" copy="查看核心数据资产规模与当前存储引擎。" />
    {loading ? <Loading text="正在读取数据库遥测..." /> : data && <><div className="database-hero"><DatabaseOutlined /><div><span>ACTIVE DIALECT</span><strong>{data.dialect}</strong></div><i>CONNECTED</i></div><div className="database-grid">{metrics.map(([key, label]) => <article key={key}><span>{label}</span><strong>{data[key].toLocaleString()}</strong><small>{key.toUpperCase()}</small></article>)}</div></>}
  </>
}

function SourceDrawer({ open, editing, form, saving, onClose, onSave }: { open: boolean; editing: Source | null; form: FormInstance<SourceForm>; saving: boolean; onClose: () => void; onSave: (values: SourceForm) => void }) {
  return <Drawer title={<><span className="drawer-kicker">SIGNAL POINT</span><strong>{editing ? '编辑监听' : '建立监听'}</strong></>} size="large" open={open} onClose={onClose} destroyOnHidden>
    <p className="drawer-copy">定义信源坐标与可信度，系统将持续从此处捕获技术信号。</p>
    <Form form={form} layout="vertical" requiredMark={false} onFinish={onSave}>
      <Form.Item label="信源名称" name="name" rules={[{ required: true, message: '请输入信源名称' }]}><Input /></Form.Item>
      <div className="form-pair"><Form.Item label="信源类型" name="source_type" rules={[{ required: true, message: '请选择类型' }]}><Select options={Object.entries(typeLabels).map(([value, label]) => ({ value, label }))} /></Form.Item><Form.Item label="可信等级" name="trust_level" rules={[{ required: true, message: '请选择可信度' }]}><Select options={Object.entries(trustLabels).map(([value, label]) => ({ value: Number(value), label }))} /></Form.Item></div>
      <Form.Item label="主页地址" name="homepage_url" rules={[{ type: 'url', message: '请输入有效地址' }]}><Input placeholder="https://" /></Form.Item>
      <Form.Item label="描述" name="description"><Input.TextArea rows={3} /></Form.Item>
      <Form.Item label="语言" name="languages" rules={[{ required: true, message: '请输入语言' }]} extra="多个值用逗号分隔"><Input placeholder="zh-CN, en" /></Form.Item>
      <Form.Item label="关注主题" name="topics" rules={[{ required: true, message: '请输入主题' }]} extra="多个值用逗号分隔"><Input placeholder="AI Agent, foundation-models" /></Form.Item>
      <Button htmlType="submit" type="primary" size="large" block loading={saving}>{editing ? '保存修改' : '建立监听'}</Button>
    </Form>
  </Drawer>
}

function UserDrawer({ open, editing, form, saving, error, onClose, onSave }: { open: boolean; editing: User | null; form: FormInstance<UserForm>; saving: boolean; error: string; onClose: () => void; onSave: (values: UserForm) => void }) {
  return <Drawer title={<><span className="drawer-kicker">ACCESS PROFILE</span><strong>{editing ? '编辑用户' : '新增用户'}</strong></>} open={open} onClose={onClose} destroyOnHidden>
    <p className="drawer-copy">配置账号身份及其可访问的雷达扇区。</p>
    {error && <Alert className="drawer-alert" type="error" message={error} showIcon />}
    <Form form={form} layout="vertical" requiredMark={false} onFinish={onSave}>
      <Form.Item label="用户名" name="username" rules={[{ required: true, message: '请输入用户名' }]}><Input /></Form.Item>
      <Form.Item label={editing ? '新密码（留空则不修改）' : '初始密码'} name="password" rules={[...(editing ? [] : [{ required: true, message: '请输入初始密码' }]), { min: 8, message: '密码至少需要 8 个字符' }]}><Input.Password autoComplete="new-password" /></Form.Item>
      <Form.Item label="角色" name="role" rules={[{ required: true }]}><Select options={Object.entries(roleLabels).map(([value, label]) => ({ value, label }))} /></Form.Item>
      <Form.Item label="账号状态" name="is_active" valuePropName="checked"><Switch checkedChildren="启用" unCheckedChildren="停用" /></Form.Item>
      <Button htmlType="submit" type="primary" size="large" block loading={saving}>{editing ? '保存修改' : '创建用户'}</Button>
    </Form>
  </Drawer>
}

function EndpointDrawer({ open, source, endpoints, form, saving, canManage, onClose, onSave, onDelete }: { open: boolean; source: Source | null; endpoints: Endpoint[]; form: FormInstance<EndpointForm>; saving: boolean; canManage: boolean; onClose: () => void; onSave: (values: EndpointForm) => void; onDelete: (endpoint: Endpoint) => void }) {
  return <Drawer title={<><span className="drawer-kicker">FETCH COORDINATES</span><strong>{source?.name ?? '采集端点'}</strong></>} size="large" open={open} onClose={onClose} destroyOnHidden>
    <p className="drawer-copy">RSS/Atom 适合持续订阅；网页类型会把指定页面作为单条内容定期更新检查。</p>
    <div className="endpoint-list">{endpoints.length === 0 ? <span>尚未配置采集端点</span> : endpoints.map((endpoint) => <article key={endpoint.id}><div><strong>{endpoint.name}</strong><small>{endpoint.endpoint_type.toUpperCase()} · 每 {endpoint.fetch_interval_minutes} 分钟 · {endpoint.health_status}</small><a href={endpoint.url} target="_blank" rel="noreferrer">{endpoint.url}</a></div>{canManage && <Button type="text" danger icon={<DeleteOutlined />} aria-label={`删除端点 ${endpoint.name}`} onClick={() => onDelete(endpoint)} />}</article>)}</div>
    {canManage && <Form form={form} layout="vertical" initialValues={{ endpoint_type: 'rss', fetch_interval_minutes: 360, max_items_per_run: 50 }} onFinish={onSave}>
      <Form.Item label="端点名称" name="name" rules={[{ required: true, message: '请输入端点名称' }]}><Input placeholder="官方 RSS" /></Form.Item>
      <div className="form-pair"><Form.Item label="采集类型" name="endpoint_type" rules={[{ required: true }]}><Select options={[{ value: 'rss', label: 'RSS / Atom' }, { value: 'web', label: '网页' }]} /></Form.Item><Form.Item label="采集间隔（分钟）" name="fetch_interval_minutes" rules={[{ required: true }]}><Input type="number" min={15} max={10080} /></Form.Item></div>
      <Form.Item label="采集地址" name="url" rules={[{ required: true, type: 'url', message: '请输入有效地址' }]}><Input placeholder="https://example.com/feed.xml" /></Form.Item>
      <Form.Item label="单次最大条数" name="max_items_per_run" rules={[{ required: true }]}><Input type="number" min={1} max={500} /></Form.Item>
      <Button htmlType="submit" type="primary" block loading={saving}>添加采集端点</Button>
    </Form>}
  </Drawer>
}

function Loading({ text }: { text: string }) { return <div className="loading-state"><Spin /><span>{text}</span></div> }
function Empty({ icon, title }: { icon: React.ReactNode; title: string }) { return <div className="empty-state">{icon}<strong>{title}</strong><span>扫描将在数据抵达后自动呈现结果</span></div> }
