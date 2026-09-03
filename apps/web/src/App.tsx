import { useEffect, useRef, useState } from 'react'
import {
  ArrowRightOutlined,
  DatabaseOutlined,
  DeleteOutlined,
  DownloadOutlined,
  EditOutlined,
  FileSearchOutlined,
  GlobalOutlined,
  InboxOutlined,
  LogoutOutlined,
  PlusOutlined,
  RadarChartOutlined,
  TeamOutlined,
} from '@ant-design/icons'
import {
  Alert,
  Button,
  Checkbox,
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
  id: number
  source_id: number
  source_name: string
  title: string
  url: string
  summary: string
  published_at: string | null
  fetched_at: string
  analysis_status: string
  testing_relevance_score: number | null
  testing_value_score: number | null
  analysis_summary: string | null
  testing_value_analysis: string | null
  applicable_scenarios: string[]
  adoption_suggestions: string[]
  analysis_risks: string[]
  analysis_tags: string[]
  analysis_model: string | null
  analyzed_at: string | null
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
type ManualContentForm = { title: string; url: string; summary: string; published_at?: string }
type SourceDiscovery = { homepage_url: string; feed_url: string; suggested_name: string; samples: { title: string; url: string; summary: string | null; published_at: string | null }[] }
type SourceDiscoveryForm = { homepage_url: string; name: string; languages: string; trust_level: number; topics: string }
type CollectedItem = {
  id: number
  source_id: number
  source_name: string
  title: string
  url: string
  summary: string | null
  published_at: string | null
  fetched_at: string
  analysis_status: 'pending' | 'analyzed' | 'filtered' | 'failed'
  analysis_attempts: number
  testing_relevance_score: number | null
  testing_value_score: number | null
  analysis_error: string | null
  analyzed_at: string | null
}
type UserForm = { username: string; password?: string; role: Role; is_active: boolean }
type DatabaseCounts = { dialect: string; users: number; sources: number; source_endpoints: number; content_items: number; fetch_runs: number }
type Tab = 'content' | 'sources' | 'collection' | 'users' | 'database'

const typeLabels: Record<string, string> = { website: '网站', newsletter: '通讯', rss: 'RSS', social: '社交媒体', wechat: '微信公众号', weibo: '微博' }
const trustLabels: Record<number, string> = { 5: '最高可信', 4: '高可信', 3: '待验证', 2: '观察中', 1: '低可信' }
const roleLabels: Record<Role, string> = { viewer: '浏览者', maintainer: '维护者', admin: '管理员' }
const analysisStatusLabels = { pending: '待分析', analyzed: '已入雷达', filtered: '已过滤', failed: '分析失败' }
const splitValues = (value: string) => value.split(/[,，]/).map((item) => item.trim()).filter(Boolean)
const list = <T,>(data: T[] | { items: T[] }) => Array.isArray(data) ? data : data.items
const dateText = (value: string | null) => value ? new Intl.DateTimeFormat('zh-CN', { dateStyle: 'medium' }).format(new Date(value)) : '时间未知'
const dateTimeText = (value: string | null) => value ? new Intl.DateTimeFormat('zh-CN', { dateStyle: 'medium', timeStyle: 'medium' }).format(new Date(value)) : '时间未知'
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
  const [minValueScore, setMinValueScore] = useState(60)
  const [selectedContent, setSelectedContent] = useState<ContentItem | null>(null)
  const [selectedContentIds, setSelectedContentIds] = useState<number[]>([])
  const [exporting, setExporting] = useState(false)
  const [users, setUsers] = useState<User[]>([])
  const [collectedItems, setCollectedItems] = useState<CollectedItem[]>([])
  const [collectedTotal, setCollectedTotal] = useState(0)
  const [collectedPage, setCollectedPage] = useState(1)
  const [collectedQuery, setCollectedQuery] = useState('')
  const [collectedStatus, setCollectedStatus] = useState('')
  const [collectedSource, setCollectedSource] = useState('')
  const [collectedStartDate, setCollectedStartDate] = useState('')
  const [collectedEndDate, setCollectedEndDate] = useState('')
  const [selectedCollectedIds, setSelectedCollectedIds] = useState<number[]>([])
  const [appliedCollectedFilters, setAppliedCollectedFilters] = useState({ query: '', status: '', sourceId: '', startDate: '', endDate: '' })
  const collectedRequestId = useRef(0)
  const contentRequestId = useRef(0)
  const contentCache = useRef<Map<string, { items: ContentItem[]; total: number; ts: number }>>(new Map())
  const CACHE_TTL = 30_000
  const [database, setDatabase] = useState<DatabaseCounts | null>(null)
  const [loading, setLoading] = useState(false)
  const [saving, setSaving] = useState(false)
  const [error, setError] = useState('')
  const [sourceDrawer, setSourceDrawer] = useState(false)
  const [userDrawer, setUserDrawer] = useState(false)
  const [endpointDrawer, setEndpointDrawer] = useState(false)
  const [manualContentDrawer, setManualContentDrawer] = useState(false)
  const [discoveryDrawer, setDiscoveryDrawer] = useState(false)
  const [discovery, setDiscovery] = useState<SourceDiscovery | null>(null)
  const [endpointSource, setEndpointSource] = useState<Source | null>(null)
  const [manualContentSource, setManualContentSource] = useState<Source | null>(null)
  const [endpoints, setEndpoints] = useState<Endpoint[]>([])
  const [editingSource, setEditingSource] = useState<Source | null>(null)
  const [editingUser, setEditingUser] = useState<User | null>(null)
  const [sourceForm] = Form.useForm<SourceForm>()
  const [userForm] = Form.useForm<UserForm>()
  const [endpointForm] = Form.useForm<EndpointForm>()
  const [manualContentForm] = Form.useForm<ManualContentForm>()
  const [discoveryForm] = Form.useForm<SourceDiscoveryForm>()
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
      if (!response.ok) {
        let detail = `请求失败 (${response.status})`
        try {
          const body = await response.json()
          if (body?.detail) detail = body.detail
        } catch { /* ignore parse errors */ }
        throw new Error(detail)
      }
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

  const loadContent = async (page = contentPage, search = query, start = startDate, end = endDate, valueScore = minValueScore) => {
    const params = new URLSearchParams({ offset: String((page - 1) * 12), limit: '12' })
    if (search) params.set('query', search)
    if (start) params.set('start_at', dateBoundary(start))
    if (end) params.set('end_at', dateBoundary(end, true))
    if (valueScore) params.set('min_value_score', String(valueScore))
    const cacheKey = params.toString()
    const hit = contentCache.current.get(cacheKey)
    if (hit && Date.now() - hit.ts < CACHE_TTL) {
      setContent(hit.items)
      setContentTotal(hit.total)
    }
    const requestId = ++contentRequestId.current
    setLoading(true)
    setError('')
    try {
      const data = await request<{ items: ContentItem[]; total: number }>(`/api/v1/content?${params}`)
      if (requestId !== contentRequestId.current) return
      contentCache.current.set(cacheKey, { items: data.items, total: data.total, ts: Date.now() })
      setContent(data.items)
      setContentTotal(data.total)
    } catch (requestError) {
      if (requestId !== contentRequestId.current) return
      setError(requestError instanceof Error ? requestError.message : '加载内容失败')
    } finally {
      if (requestId === contentRequestId.current) setLoading(false)
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

  const toggleContentSelection = (id: number) => {
    setSelectedContentIds((current) => {
      if (current.includes(id)) return current.filter((contentId) => contentId !== id)
      if (current.length >= 100) {
        setError('单次报告最多选择 100 条情报')
        return current
      }
      return [...current, id]
    })
  }

  const toggleCurrentPageSelection = () => {
    const pageIds = content.map((item) => item.id)
    const allSelected = pageIds.every((id) => selectedContentIds.includes(id))
    setSelectedContentIds((current) => {
      if (allSelected) return current.filter((id) => !pageIds.includes(id))
      const additions = pageIds.filter((id) => !current.includes(id))
      const available = 100 - current.length
      if (additions.length > available) setError('单次报告最多选择 100 条情报')
      return [...current, ...additions.slice(0, available)]
    })
  }

  const exportSelectedContent = async () => {
    if (!token || selectedContentIds.length === 0) return
    setExporting(true)
    setError('')
    try {
      const response = await fetch('/api/v1/content/export', {
        method: 'POST',
        headers: { Authorization: `Bearer ${token}`, 'Content-Type': 'application/json' },
        body: JSON.stringify({ content_ids: selectedContentIds }),
      })
      if (response.status === 401) {
        logout()
        throw new Error('登录状态已失效，请重新登录')
      }
      if (!response.ok) throw new Error(`导出失败 (${response.status})`)
      const blob = await response.blob()
      const disposition = response.headers.get('Content-Disposition') ?? ''
      const filename = disposition.match(/filename="?([^";]+)"?/i)?.[1] ?? 'testing-intelligence-report.md'
      const url = URL.createObjectURL(blob)
      const anchor = document.createElement('a')
      try {
        anchor.href = url
        anchor.download = filename
        document.body.appendChild(anchor)
        anchor.click()
      } finally {
        anchor.remove()
        window.setTimeout(() => URL.revokeObjectURL(url), 0)
      }
    } catch (requestError) {
      setError(requestError instanceof Error ? requestError.message : '导出 Markdown 报告失败')
    } finally {
      setExporting(false)
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

  const loadCollectedContent = async (page = collectedPage, filters = appliedCollectedFilters) => {
    const requestId = ++collectedRequestId.current
    setLoading(true)
    setError('')
    try {
      const params = new URLSearchParams({ offset: String((page - 1) * 20), limit: '20' })
      if (filters.query) params.set('query', filters.query)
      if (filters.status) params.set('status', filters.status)
      if (filters.sourceId) params.set('source_id', filters.sourceId)
      if (filters.startDate) params.set('start_at', dateBoundary(filters.startDate))
      if (filters.endDate) params.set('end_at', dateBoundary(filters.endDate, true))
      const contentRequest = request<{ items: CollectedItem[]; total: number }>(`/api/v1/collected-content?${params}`)
      const sourceRequest = sources.length === 0
        ? request<{ items: Source[]; total: number }>('/api/v1/sources?limit=200')
        : Promise.resolve(null)
      const [data, sourceData] = await Promise.all([contentRequest, sourceRequest])
      if (requestId !== collectedRequestId.current) return
      setCollectedItems(data.items)
      setCollectedTotal(data.total)
      if (sourceData) {
        setSources(sourceData.items)
        setSourceTotal(sourceData.total)
      }
    } catch (requestError) {
      if (requestId === collectedRequestId.current) {
        setError(requestError instanceof Error ? requestError.message : '加载采集内容失败')
      }
    } finally {
      if (requestId === collectedRequestId.current) setLoading(false)
    }
  }

  const deleteCollectedItems = async (ids: number[]) => {
    setError('')
    try {
      if (ids.length === 1) {
        await request(`/api/v1/collected-content/${ids[0]}`, { method: 'DELETE' })
      } else {
        await request('/api/v1/collected-content/bulk-delete', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ content_ids: ids }),
        })
      }
      setSelectedCollectedIds([])
      const currentPageDeleted = collectedItems.length > 0
        && collectedItems.every((item) => ids.includes(item.id))
      const nextPage = currentPageDeleted && collectedPage > 1 ? collectedPage - 1 : collectedPage
      setCollectedPage(nextPage)
      await loadCollectedContent(nextPage)
    } catch (requestError) {
      setError(requestError instanceof Error ? requestError.message : '删除采集内容失败')
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
    if (tab === 'collection' && me.role === 'admin') void loadCollectedContent()
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

  const openDiscovery = () => {
    setDiscovery(null)
    discoveryForm.resetFields()
    discoveryForm.setFieldsValue({ languages: 'en', trust_level: 3, topics: '' })
    setDiscoveryDrawer(true)
  }

  const discoverSource = async () => {
    const homepage_url = discoveryForm.getFieldValue('homepage_url')
    if (!homepage_url) return
    setSaving(true)
    setError('')
    try {
      const result = await request<SourceDiscovery>('/api/v1/sources/discover', {
        method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ homepage_url }),
      })
      setDiscovery(result)
      discoveryForm.setFieldValue('name', result.suggested_name)
    } catch (requestError) {
      setError(requestError instanceof Error ? requestError.message : '没有发现有效订阅源')
    } finally {
      setSaving(false)
    }
  }

  const installDiscoveredSource = async (values: SourceDiscoveryForm) => {
    if (!discovery) return
    setSaving(true)
    setError('')
    try {
      await request('/api/v1/sources/discover/install', {
        method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({
          homepage_url: discovery.homepage_url, feed_url: discovery.feed_url, name: values.name,
          languages: splitValues(values.languages), trust_level: values.trust_level, topics: splitValues(values.topics),
        }),
      })
      setDiscoveryDrawer(false)
      await loadSources()
    } catch (requestError) {
      setError(requestError instanceof Error ? requestError.message : '启用发现信源失败')
    } finally {
      setSaving(false)
    }
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
    endpointForm.resetFields()
    endpointForm.setFieldsValue({ endpoint_type: 'rss', fetch_interval_minutes: 360, max_items_per_run: 50 })
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

  const openManualContent = (source: Source) => {
    setManualContentSource(source)
    manualContentForm.resetFields()
    setManualContentDrawer(true)
  }

  const saveManualContent = async (values: ManualContentForm) => {
    if (!manualContentSource) return
    setSaving(true)
    setError('')
    try {
      await request<CollectedItem>('/api/v1/collected-content', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          ...values,
          source_id: Number(manualContentSource.id),
          published_at: values.published_at ? dateBoundary(values.published_at) : null,
        }),
      })
      contentCache.current.clear()
      setManualContentDrawer(false)
      Modal.success({ title: '内容已进入分析队列', content: '系统不会抓取平台页面，将使用你填写的标题和摘要进行测试情报分析。' })
    } catch (requestError) {
      setError(requestError instanceof Error ? requestError.message : '录入内容失败')
    } finally {
      setSaving(false)
    }
  }

  const openUser = (user?: User) => {
    setEditingUser(user ?? null)
    userForm.resetFields()
    userForm.setFieldsValue(user ? { username: user.username, role: user.role, is_active: user.is_active ?? true } : { role: 'viewer', is_active: true })
    setUserDrawer(true)
  }

  const saveUser = async (values: UserForm) => {
    if (saving) return
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
    { id: 'collection', label: '采集管理', icon: <InboxOutlined />, admin: true },
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
        {tab === 'content' && <ContentView items={content} total={contentTotal} page={contentPage} loading={loading} query={query} startDate={startDate} endDate={endDate} minValueScore={minValueScore} selectedIds={selectedContentIds} exporting={exporting} onQuery={setQuery} onStartDate={setStartDate} onEndDate={setEndDate} onMinValueScore={setMinValueScore} onOpen={setSelectedContent} onToggleSelection={toggleContentSelection} onToggleCurrentPage={toggleCurrentPageSelection} onClearSelection={() => setSelectedContentIds([])} onExport={() => void exportSelectedContent()} onSearch={() => { setSelectedContentIds([]); setContentPage(1); void loadContent(1, query) }} onReset={() => { setSelectedContentIds([]); setQuery(''); setStartDate(''); setEndDate(''); setMinValueScore(60); setContentPage(1); void loadContent(1, '', '', '', 60) }} onPage={(page) => { setContentPage(page); void loadContent(page) }} />}
        {tab === 'sources' && <SourcesView sources={sources} total={sourceTotal} loading={loading} canManage={canManageSources} canDelete={isAdmin} onAdd={() => openSource()} onDiscover={openDiscovery} onManualContent={openManualContent} onEndpoints={(source) => void openEndpoints(source)} onEdit={openSource} onDelete={(source) => Modal.confirm({ title: `删除信源「${source.name}」？`, content: '删除后无法恢复，并会清除关联内容。', okText: '确认删除', cancelText: '取消', okButtonProps: { danger: true }, onOk: () => removeSource(source) })} />}
        {tab === 'collection' && isAdmin && <CollectedContentView items={collectedItems} total={collectedTotal} page={collectedPage} loading={loading} sources={sources} query={collectedQuery} status={collectedStatus} sourceId={collectedSource} startDate={collectedStartDate} endDate={collectedEndDate} selectedIds={selectedCollectedIds} onQuery={setCollectedQuery} onStatus={setCollectedStatus} onSource={setCollectedSource} onStartDate={setCollectedStartDate} onEndDate={setCollectedEndDate} onToggle={(id) => setSelectedCollectedIds((current) => { if (current.includes(id)) return current.filter((value) => value !== id); if (current.length >= 100) { setError('单次最多删除 100 条采集内容'); return current } return [...current, id] })} onTogglePage={() => { const pageIds = collectedItems.map((item) => item.id); const all = pageIds.every((id) => selectedCollectedIds.includes(id)); setSelectedCollectedIds((current) => { if (all) return current.filter((id) => !pageIds.includes(id)); const additions = pageIds.filter((id) => !current.includes(id)); const available = 100 - current.length; if (additions.length > available) setError('单次最多删除 100 条采集内容'); return [...current, ...additions.slice(0, available)] }) }} onApply={() => { const filters = { query: collectedQuery, status: collectedStatus, sourceId: collectedSource, startDate: collectedStartDate, endDate: collectedEndDate }; setAppliedCollectedFilters(filters); setSelectedCollectedIds([]); setCollectedPage(1); void loadCollectedContent(1, filters) }} onReset={() => { const filters = { query: '', status: '', sourceId: '', startDate: '', endDate: '' }; setCollectedQuery(''); setCollectedStatus(''); setCollectedSource(''); setCollectedStartDate(''); setCollectedEndDate(''); setAppliedCollectedFilters(filters); setSelectedCollectedIds([]); setCollectedPage(1); void loadCollectedContent(1, filters) }} onPage={(page) => { setCollectedPage(page); void loadCollectedContent(page, appliedCollectedFilters) }} onDelete={(ids) => Modal.confirm({ title: `删除 ${ids.length} 条采集内容？`, content: '删除后原始采集记录和分析结果均无法恢复。', okText: '确认删除', cancelText: '取消', okButtonProps: { danger: true }, onOk: () => deleteCollectedItems(ids) })} />}
        {tab === 'users' && isAdmin && <UsersView users={users} loading={loading} me={me} onAdd={() => openUser()} onEdit={openUser} onDelete={(user) => Modal.confirm({ title: `删除用户「${user.username}」？`, content: '该用户将立即失去访问权限。', okText: '确认删除', cancelText: '取消', okButtonProps: { danger: true }, onOk: () => removeUser(user) })} />}
        {tab === 'database' && isAdmin && <DatabaseView data={database} loading={loading} />}
      </section>
      <SourceDrawer open={sourceDrawer} editing={editingSource} form={sourceForm} saving={saving} onClose={() => setSourceDrawer(false)} onSave={saveSource} />
      <UserDrawer open={userDrawer} editing={editingUser} form={userForm} saving={saving} error={error} onClose={() => setUserDrawer(false)} onSave={saveUser} />
      <EndpointDrawer open={endpointDrawer} source={endpointSource} endpoints={endpoints} form={endpointForm} saving={saving} canManage={canManageSources} onClose={() => setEndpointDrawer(false)} onSave={saveEndpoint} onDelete={removeEndpoint} />
      <ManualContentDrawer open={manualContentDrawer} source={manualContentSource} form={manualContentForm} saving={saving} onClose={() => setManualContentDrawer(false)} onSave={saveManualContent} />
      <SourceDiscoveryDrawer open={discoveryDrawer} discovery={discovery} form={discoveryForm} saving={saving} onClose={() => setDiscoveryDrawer(false)} onDiscover={() => void discoverSource()} onInstall={installDiscoveredSource} />
      <IntelligenceModal item={selectedContent} onClose={() => setSelectedContent(null)} />
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

function ContentView({ items, total, page, loading, query, startDate, endDate, minValueScore, selectedIds, exporting, onQuery, onStartDate, onEndDate, onMinValueScore, onOpen, onToggleSelection, onToggleCurrentPage, onClearSelection, onExport, onSearch, onReset, onPage }: { items: ContentItem[]; total: number; page: number; loading: boolean; query: string; startDate: string; endDate: string; minValueScore: number; selectedIds: number[]; exporting: boolean; onQuery: (value: string) => void; onStartDate: (value: string) => void; onEndDate: (value: string) => void; onMinValueScore: (value: number) => void; onOpen: (item: ContentItem) => void; onToggleSelection: (id: number) => void; onToggleCurrentPage: () => void; onClearSelection: () => void; onExport: () => void; onSearch: () => void; onReset: () => void; onPage: (page: number) => void }) {
  const pageSelected = items.length > 0 && items.every((item) => selectedIds.includes(item.id))
  return <>
    <PageIntro kicker="INTELLIGENCE FEED / LIVE" title="内容情报" copy="聚合监听网络中的最新信号，快速定位值得跟进的变化。" />
    <div className="feed-tools">
      <Input.Search value={query} onChange={(event) => onQuery(event.target.value)} onSearch={onSearch} enterButton="检索" placeholder="检索标题、摘要或正文" aria-label="检索内容" />
      <div className="date-filter"><label>开始日期<input type="date" value={startDate} max={endDate || undefined} onChange={(event) => onStartDate(event.target.value)} /></label><i>至</i><label>结束日期<input type="date" value={endDate} min={startDate || undefined} onChange={(event) => onEndDate(event.target.value)} /></label><label>最低价值<Select value={minValueScore} onChange={onMinValueScore} options={[{ value: 40, label: '观察 40+' }, { value: 60, label: '推荐 60+' }, { value: 80, label: '高价值 80+' }]} /></label><Button onClick={onSearch}>应用筛选</Button>{(query || startDate || endDate || minValueScore !== 60) && <Button type="text" onClick={onReset}>重置</Button>}</div>
      <span>{total} 条情报</span>
    </div>
    <div className="selection-bar"><Checkbox checked={pageSelected} indeterminate={!pageSelected && items.some((item) => selectedIds.includes(item.id))} onChange={onToggleCurrentPage}>全选当前页</Checkbox><span>已选择 {selectedIds.length} 条</span>{selectedIds.length > 0 && <Button type="text" onClick={onClearSelection}>清空选择</Button>}<Button type="primary" icon={<DownloadOutlined />} disabled={selectedIds.length === 0} loading={exporting} onClick={onExport} aria-label={`导出 Markdown (${selectedIds.length})`}>导出 Markdown ({selectedIds.length})</Button></div>
    {loading && items.length === 0 ? <Loading text="正在扫描情报流..." /> : items.length === 0 ? <Empty icon={<FileSearchOutlined />} title="当前扇区没有匹配的情报。" /> : <>
      <div className={`content-grid${loading ? ' is-refreshing' : ''}`}>{items.map((item) => <article className={`content-card ${selectedIds.includes(item.id) ? 'selected' : ''}`} key={item.id}>
        <span className="card-selector" onClick={(event) => event.stopPropagation()} onKeyDown={(event) => event.stopPropagation()}><Checkbox checked={selectedIds.includes(item.id)} onChange={() => onToggleSelection(item.id)} aria-label={`选择 ${item.title}`} /></span>
        <div className="content-meta"><span>{dateText(item.published_at || item.fetched_at)}</span><span>{item.source_name}</span></div>
        <div className="score-line"><Tag color="green">测试相关 {item.testing_relevance_score ?? 0}</Tag><Tag color={(item.testing_value_score ?? 0) >= 80 ? 'gold' : 'blue'}>测试价值 {item.testing_value_score ?? 0}</Tag></div>
        <h2><button type="button" onClick={(event) => { event.stopPropagation(); onOpen(item) }}>{item.title}</button></h2>
        <p>{item.analysis_summary || '等待测试价值摘要'}</p><button className="read-link" type="button" onClick={() => onOpen(item)}>查看测试情报 <ArrowRightOutlined /></button>
      </article>)}</div>
      {total > 12 && <Pagination className="radar-pagination" current={page} total={total} pageSize={12} showSizeChanger={false} onChange={onPage} />}
    </>}
  </>
}

function IntelligenceModal({ item, onClose }: { item: ContentItem | null; onClose: () => void }) {
  if (!item) return null
  const Section = ({ title, text, values }: { title: string; text?: string | null; values?: string[] }) => <section className="intel-section"><h3>{title}</h3>{text && <p>{text}</p>}{values && (values.length ? <ul>{values.map((value) => <li key={value}>{value}</li>)}</ul> : <p>暂无</p>)}</section>
  return <Modal className="intel-modal" width={900} open title={null} footer={null} onCancel={onClose}>
    <div className="intel-kicker">TESTING INTELLIGENCE / SCORE {item.testing_value_score ?? 0}</div>
    <h2>{item.title}</h2>
    <div className="intel-meta"><span>{item.source_name}</span><span>{dateText(item.published_at || item.fetched_at)}</span><Tag color="green">相关性 {item.testing_relevance_score ?? 0}</Tag><Tag color="gold">价值 {item.testing_value_score ?? 0}</Tag></div>
    <Section title="情报摘要" text={item.analysis_summary} />
    <Section title="测试价值分析" text={item.testing_value_analysis} />
    <div className="intel-columns"><Section title="适用测试场景" values={item.applicable_scenarios} /><Section title="落地验证建议" values={item.adoption_suggestions} /></div>
    <Section title="风险与边界" values={item.analysis_risks} />
    <div className="intel-tags">{item.analysis_tags.map((tag) => <Tag key={tag}>{tag}</Tag>)}</div>
    <a className="original-link" href={item.url} target="_blank" rel="noreferrer">查看原文（外部链接） <ArrowRightOutlined /></a>
  </Modal>
}

function SourcesView({ sources, total, loading, canManage, canDelete, onAdd, onDiscover, onManualContent, onEndpoints, onEdit, onDelete }: { sources: Source[]; total: number; loading: boolean; canManage: boolean; canDelete: boolean; onAdd: () => void; onDiscover: () => void; onManualContent: (source: Source) => void; onEndpoints: (source: Source) => void; onEdit: (source: Source) => void; onDelete: (source: Source) => void }) {
  return <>
    <PageIntro kicker="SOURCE NETWORK / ACTIVE" title="信源管理" copy="编排可信技术信号，构建你的持续监听网络。" action={canManage && <div className="source-intro-actions"><Button size="large" icon={<RadarChartOutlined />} onClick={onDiscover}>自动发现</Button><Button type="primary" size="large" icon={<PlusOutlined />} onClick={onAdd} aria-label="新增信源">新增信源</Button></div>} />
    <div className="metric-strip"><div><strong>{String(total).padStart(2, '0')}</strong><span>监听节点</span></div><div><strong>{sources.filter((item) => item.trust_level >= 4).length}</strong><span>高可信信号</span></div><div className="scan-line"><span>NETWORK COVERAGE</span><i /></div></div>
    {loading ? <Loading text="正在校准信源网络..." /> : sources.length === 0 ? <Empty icon={<RadarChartOutlined />} title="暂无信源监听点。" /> : <div className="source-grid">{sources.map((source, index) => <article className="source-card" key={source.id}>
      <div className="card-index">{String(index + 1).padStart(2, '0')}</div>
      <div className="source-heading"><div className="source-icon"><GlobalOutlined /></div><div><span>{typeLabels[source.source_type] ?? source.source_type}</span><h2>{source.name}</h2></div></div>
      <p>{source.description || '暂无描述'}</p>{source.homepage_url && <a href={source.homepage_url} target="_blank" rel="noreferrer">{source.homepage_url}</a>}
      <div className="tag-row"><Tag color={source.trust_level >= 4 ? 'green' : 'default'}>{trustLabels[source.trust_level] ?? source.trust_level}</Tag>{source.languages.map((value) => <Tag key={value}>{value.toUpperCase()}</Tag>)}{source.topics.map((value) => <Tag key={value}>#{value}</Tag>)}</div>
      <div className="card-actions">{canManage && ['wechat', 'weibo'].includes(source.source_type) && <Button type="text" icon={<PlusOutlined />} aria-label={`录入内容 ${source.name}`} onClick={() => onManualContent(source)}>录入内容</Button>}<Button type="text" icon={<RadarChartOutlined />} onClick={() => onEndpoints(source)}>采集端点</Button>{canManage && <Button type="text" icon={<EditOutlined />} onClick={() => onEdit(source)}>编辑</Button>}{canDelete && <Button type="text" danger icon={<DeleteOutlined />} onClick={() => onDelete(source)}>删除</Button>}</div>
    </article>)}</div>}
  </>
}

function SourceDiscoveryDrawer({ open, discovery, form, saving, onClose, onDiscover, onInstall }: { open: boolean; discovery: SourceDiscovery | null; form: FormInstance<SourceDiscoveryForm>; saving: boolean; onClose: () => void; onDiscover: () => void; onInstall: (values: SourceDiscoveryForm) => void }) {
  return <Drawer title={<><span className="drawer-kicker">SOURCE DISCOVERY</span><strong>自动发现信源</strong></>} size="large" open={open} onClose={onClose} destroyOnHidden>
    <p className="drawer-copy">输入网站主页，平台会在站内发现并验证 RSS / Atom，展示样例后再创建监听。</p>
    <Form form={form} layout="vertical" requiredMark={false} onFinish={onInstall}>
      <Form.Item label="网站主页" name="homepage_url" rules={[{ required: true, type: 'url', message: '请输入有效主页地址' }]}><Input.Search placeholder="https://example.com/blog/" enterButton="开始探测" loading={saving && !discovery} onSearch={onDiscover} /></Form.Item>
      {discovery && <>
        <div className="discovery-result"><span>已验证订阅地址</span><a href={discovery.feed_url} target="_blank" rel="noreferrer">{discovery.feed_url}</a></div>
        <div className="discovery-samples">{discovery.samples.map((item) => <article key={item.url}><strong>{item.title}</strong><small>{dateText(item.published_at)}</small><p>{item.summary || '该条目未提供摘要'}</p></article>)}</div>
        <Form.Item label="信源名称" name="name" rules={[{ required: true, message: '请输入信源名称' }]}><Input /></Form.Item>
        <div className="form-pair"><Form.Item label="语言" name="languages" rules={[{ required: true, message: '请输入语言' }]}><Input placeholder="zh-CN, en" /></Form.Item><Form.Item label="可信等级" name="trust_level" rules={[{ required: true }]}><Select options={Object.entries(trustLabels).map(([value, label]) => ({ value: Number(value), label }))} /></Form.Item></div>
        <Form.Item label="关注主题" name="topics" extra="多个值用逗号分隔"><Input placeholder="AI Agent, testing" /></Form.Item>
        <Button htmlType="submit" type="primary" size="large" block loading={saving}>确认并启用监听</Button>
      </>}
    </Form>
  </Drawer>
}

function CollectedContentView({ items, total, page, loading, sources, query, status, sourceId, startDate, endDate, selectedIds, onQuery, onStatus, onSource, onStartDate, onEndDate, onToggle, onTogglePage, onApply, onReset, onPage, onDelete }: { items: CollectedItem[]; total: number; page: number; loading: boolean; sources: Source[]; query: string; status: string; sourceId: string; startDate: string; endDate: string; selectedIds: number[]; onQuery: (value: string) => void; onStatus: (value: string) => void; onSource: (value: string) => void; onStartDate: (value: string) => void; onEndDate: (value: string) => void; onToggle: (id: number) => void; onTogglePage: () => void; onApply: () => void; onReset: () => void; onPage: (page: number) => void; onDelete: (ids: number[]) => void }) {
  const pageSelected = items.length > 0 && items.every((item) => selectedIds.includes(item.id))
  const hasFilters = query || status || sourceId || startDate || endDate
  const [selectedItem, setSelectedItem] = useState<CollectedItem | null>(null)
  return <>
    <PageIntro kicker="COLLECTION ARCHIVE / ADMIN" title="采集管理" copy="管理自动采集的全部原始信息，包括待分析、已入雷达、已过滤和失败内容。" />
    <div className="collection-tools">
      <Input.Search value={query} onChange={(event) => onQuery(event.target.value)} onSearch={onApply} enterButton="查询" placeholder="查询标题或摘要" aria-label="查询采集内容" />
      <Select value={status} onChange={onStatus} options={[{ value: '', label: '全部状态' }, ...Object.entries(analysisStatusLabels).map(([value, label]) => ({ value, label }))]} aria-label="采集分析状态" />
      <Select value={sourceId} onChange={onSource} options={[{ value: '', label: '全部信源' }, ...sources.map((source) => ({ value: String(source.id), label: source.name }))]} aria-label="采集信源" />
      <label>开始日期<input type="date" value={startDate} max={endDate || undefined} onChange={(event) => onStartDate(event.target.value)} /></label>
      <label>结束日期<input type="date" value={endDate} min={startDate || undefined} onChange={(event) => onEndDate(event.target.value)} /></label>
      <Button onClick={onApply}>应用筛选</Button>{hasFilters && <Button type="text" onClick={onReset}>重置</Button>}
    </div>
    <div className="collection-actions"><Checkbox checked={pageSelected} indeterminate={!pageSelected && items.some((item) => selectedIds.includes(item.id))} onChange={onTogglePage}>全选当前页</Checkbox><span>共 {total} 条，已选择 {selectedIds.length} 条</span><Button danger icon={<DeleteOutlined />} disabled={selectedIds.length === 0} onClick={() => onDelete(selectedIds)} aria-label={`批量删除 (${selectedIds.length})`}>批量删除 ({selectedIds.length})</Button></div>
    {loading ? <Loading text="正在读取采集档案..." /> : items.length === 0 ? <Empty icon={<InboxOutlined />} title="暂无采集内容" /> : <div className="collection-table" role="table" aria-label="采集内容列表">
      <div className="collection-row collection-head" role="row"><span>选择</span><span>内容</span><span>状态</span><span>评分</span><span>采集时间</span><span>操作</span></div>
      {items.map((item) => <div className="collection-row" role="row" key={item.id}>
        <Checkbox checked={selectedIds.includes(item.id)} onChange={() => onToggle(item.id)} aria-label={`选择采集内容 ${item.title}`} />
        <div className="collection-title"><button type="button" onClick={() => setSelectedItem(item)}>{item.title}</button><small>{item.source_name}</small>{item.analysis_error && <em title={item.analysis_error}>{item.analysis_error}</em>}</div>
        <Tag className="collection-status" color={item.analysis_status === 'analyzed' ? 'green' : item.analysis_status === 'failed' ? 'red' : item.analysis_status === 'filtered' ? 'default' : 'blue'}>{analysisStatusLabels[item.analysis_status]}</Tag>
        <span className="collection-score" data-label="相关/价值">{item.testing_relevance_score ?? '-'} / {item.testing_value_score ?? '-'}</span>
        <time className="collection-time" data-label="采集时间">{dateText(item.fetched_at)}</time>
        <Button type="text" danger icon={<DeleteOutlined />} aria-label={`删除采集内容 ${item.title}`} onClick={() => onDelete([item.id])} />
      </div>)}
    </div>}
    {total > 20 && <Pagination className="radar-pagination" current={page} total={total} pageSize={20} showSizeChanger={false} onChange={onPage} />}
    <CollectedContentModal item={selectedItem} onClose={() => setSelectedItem(null)} />
  </>
}

function CollectedContentModal({ item, onClose }: { item: CollectedItem | null; onClose: () => void }) {
  if (!item) return null
  return <Modal className="intel-modal collected-modal" width={760} open title={null} footer={null} onCancel={onClose}>
    <div className="intel-kicker">COLLECTED CONTENT / #{item.id}</div>
    <h2>{item.title}</h2>
    <div className="intel-meta"><span>{item.source_name}</span><Tag color={item.analysis_status === 'analyzed' ? 'green' : item.analysis_status === 'failed' ? 'red' : item.analysis_status === 'filtered' ? 'default' : 'blue'}>{analysisStatusLabels[item.analysis_status]}</Tag><span>相关性 {item.testing_relevance_score ?? '-'}</span><span>价值 {item.testing_value_score ?? '-'}</span></div>
    <section className="intel-section"><h3>原始摘要</h3><p>{item.summary || '暂无摘要'}</p></section>
    <div className="collected-detail-grid"><div><span>发布时间</span><strong>{dateTimeText(item.published_at)}</strong></div><div><span>采集时间</span><strong>{dateTimeText(item.fetched_at)}</strong></div><div><span>分析时间</span><strong>{dateTimeText(item.analyzed_at)}</strong></div><div><span>分析次数</span><strong>{item.analysis_attempts}</strong></div></div>
    {item.analysis_error && <section className="intel-section collected-error"><h3>失败原因</h3><p>{item.analysis_error}</p></section>}
    <a className="original-link" href={item.url} target="_blank" rel="noreferrer">查看原文（外部链接） <ArrowRightOutlined /></a>
  </Modal>
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
  const socialPlatform = source && ['wechat', 'weibo'].includes(source.source_type)
  return <Drawer title={<><span className="drawer-kicker">FETCH COORDINATES</span><strong>{source?.name ?? '采集端点'}</strong></>} size="large" open={open} onClose={onClose} destroyOnHidden>
    <p className="drawer-copy">{socialPlatform ? '微信公众号和微博仅接入来源明确、已获授权的 RSS/Atom 地址；平台网页请通过人工录入提交，不会自动抓取。' : 'RSS/Atom 适合持续订阅；网页类型会把指定页面作为单条内容定期更新检查。'}</p>
    <div className="endpoint-list">{endpoints.length === 0 ? <span>尚未配置采集端点</span> : endpoints.map((endpoint) => <article key={endpoint.id}><div><strong>{endpoint.name}</strong><small>{endpoint.endpoint_type.toUpperCase()} · 每 {endpoint.fetch_interval_minutes} 分钟 · {endpoint.health_status}</small><a href={endpoint.url} target="_blank" rel="noreferrer">{endpoint.url}</a></div>{canManage && <Button type="text" danger icon={<DeleteOutlined />} aria-label={`删除端点 ${endpoint.name}`} onClick={() => Modal.confirm({ title: `删除端点「${endpoint.name}」？`, content: '删除后采集配置及其运行历史无法恢复。', okText: '确认删除', cancelText: '取消', okButtonProps: { danger: true }, onOk: () => onDelete(endpoint) })} />}</article>)}</div>
    {canManage && <Form form={form} layout="vertical" initialValues={{ endpoint_type: 'rss', fetch_interval_minutes: 360, max_items_per_run: 50 }} onFinish={onSave}>
      <Form.Item label="端点名称" name="name" rules={[{ required: true, message: '请输入端点名称' }]}><Input placeholder="官方 RSS" /></Form.Item>
      <div className="form-pair"><Form.Item label="采集类型" name="endpoint_type" rules={[{ required: true }]}><Select options={socialPlatform ? [{ value: 'rss', label: 'RSS / Atom' }] : [{ value: 'rss', label: 'RSS / Atom' }, { value: 'web', label: '网页' }]} /></Form.Item><Form.Item label="采集间隔（分钟）" name="fetch_interval_minutes" rules={[{ required: true }]}><Input type="number" min={15} max={10080} /></Form.Item></div>
      <Form.Item label="采集地址" name="url" rules={[{ required: true, type: 'url', message: '请输入有效地址' }]}><Input placeholder="https://example.com/feed.xml" /></Form.Item>
      <Form.Item label="单次最大条数" name="max_items_per_run" rules={[{ required: true }]}><Input type="number" min={1} max={500} /></Form.Item>
      <Button htmlType="submit" type="primary" block loading={saving}>添加采集端点</Button>
    </Form>}
  </Drawer>
}

function ManualContentDrawer({ open, source, form, saving, onClose, onSave }: { open: boolean; source: Source | null; form: FormInstance<ManualContentForm>; saving: boolean; onClose: () => void; onSave: (values: ManualContentForm) => void }) {
  return <Drawer title={<><span className="drawer-kicker">MANUAL SIGNAL</span><strong>录入 {source?.name ?? '平台内容'}</strong></>} size="large" open={open} onClose={onClose} destroyOnHidden>
    <p className="drawer-copy">仅保存原文链接和你填写的信息，不自动访问微信或微博页面。请确认内容来源合法，并保留原文证据链接。</p>
    <Form form={form} layout="vertical" requiredMark={false} onFinish={onSave}>
      <Form.Item label="内容标题" name="title" rules={[{ required: true, message: '请输入内容标题' }, { max: 500, message: '标题不能超过 500 个字符' }]}><Input /></Form.Item>
      <Form.Item label="原文地址" name="url" rules={[{ required: true, type: 'url', message: '请输入有效地址' }]}><Input placeholder="https://" /></Form.Item>
      <Form.Item label="内容摘要" name="summary" extra="摘要将用于测试相关性筛选和情报分析，请包含关键事实与测试价值线索。" rules={[{ required: true, message: '请输入内容摘要' }, { max: 20000, message: '摘要不能超过 20000 个字符' }]}><Input.TextArea rows={8} /></Form.Item>
      <Form.Item label="发布日期（可选）" name="published_at"><Input type="date" /></Form.Item>
      <Button htmlType="submit" type="primary" size="large" block loading={saving}>提交分析</Button>
    </Form>
  </Drawer>
}

function Loading({ text }: { text: string }) { return <div className="loading-state"><Spin /><span>{text}</span></div> }
function Empty({ icon, title }: { icon: React.ReactNode; title: string }) { return <div className="empty-state">{icon}<strong>{title}</strong><span>扫描将在数据抵达后自动呈现结果</span></div> }
