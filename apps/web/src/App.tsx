import { useEffect, useState } from 'react'
import {
  ArrowRightOutlined,
  GlobalOutlined,
  LogoutOutlined,
  PlusOutlined,
  RadarChartOutlined,
} from '@ant-design/icons'
import { Alert, Button, Drawer, Form, Input, Select, Spin, Tag } from 'antd'

type Source = {
  id?: string
  name: string
  source_type: string
  homepage_url: string
  description: string
  languages: string[]
  trust_level: number
  topics: string[]
}

type SourceForm = Omit<Source, 'languages' | 'topics'> & {
  languages: string
  topics: string
}

const typeLabels: Record<string, string> = {
  website: '网站',
  newsletter: '通讯',
  rss: 'RSS',
  social: '社交媒体',
}

const trustLabels: Record<number, string> = {
  5: '最高可信',
  4: '高可信',
  3: '待验证',
  2: '观察中',
  1: '低可信',
}

const splitValues = (value: string) => value.split(/[,，]/).map((item) => item.trim()).filter(Boolean)

export default function App() {
  const [token, setToken] = useState(() => localStorage.getItem('access_token'))
  const [sources, setSources] = useState<Source[]>([])
  const [total, setTotal] = useState(0)
  const [loading, setLoading] = useState(false)
  const [drawerOpen, setDrawerOpen] = useState(false)
  const [error, setError] = useState('')

  useEffect(() => {
    if (!token) return

    const loadSources = async () => {
      setLoading(true)
      setError('')
      try {
        const response = await fetch('/api/v1/sources', {
          headers: { Authorization: `Bearer ${token}` },
        })
        if (!response.ok) throw new Error('信源网络暂时不可用')
        const data = await response.json() as { items: Source[]; total: number }
        setSources(data.items)
        setTotal(data.total)
      } catch (requestError) {
        setError(requestError instanceof Error ? requestError.message : '加载信源失败')
      } finally {
        setLoading(false)
      }
    }

    void loadSources()
  }, [token])

  const login = async (values: { username: string; password: string }) => {
    setError('')
    const response = await fetch('/api/v1/auth/login', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(values),
    })
    if (!response.ok) {
      setError('登录失败，请检查账号与密码')
      return
    }
    const data = await response.json() as { access_token: string }
    localStorage.setItem('access_token', data.access_token)
    setToken(data.access_token)
  }

  const createSource = async (values: SourceForm) => {
    if (!token) return
    setError('')
    const payload: Source = {
      name: values.name,
      source_type: values.source_type,
      homepage_url: values.homepage_url,
      description: values.description,
      languages: splitValues(values.languages),
      trust_level: values.trust_level,
      topics: splitValues(values.topics),
    }
    const response = await fetch('/api/v1/sources', {
      method: 'POST',
      headers: {
        Authorization: `Bearer ${token}`,
        'Content-Type': 'application/json',
      },
      body: JSON.stringify(payload),
    })
    if (!response.ok) {
      setError('建立监听失败，请稍后重试')
      return
    }
    const created = await response.json() as Source
    setSources((current) => [created, ...current])
    setTotal((current) => current + 1)
    setDrawerOpen(false)
  }

  const logout = () => {
    localStorage.removeItem('access_token')
    setToken(null)
    setSources([])
  }

  if (!token) {
    return (
      <main className="login-shell">
        <section className="login-story">
          <div className="brand"><RadarChartOutlined /> SIGNAL ATLAS</div>
          <div className="radar-visual" aria-hidden="true">
            <i className="radar-sweep" />
            <i className="radar-ping ping-one" />
            <i className="radar-ping ping-two" />
          </div>
          <div className="story-copy">
            <span className="eyebrow">TECH INTELLIGENCE / 01</span>
            <h1>在噪声成为趋势前，<br />捕捉它。</h1>
            <p>持续校准高价值技术信源，让每一次扫描都有方向。</p>
          </div>
        </section>
        <section className="login-panel">
          <div className="login-card">
            <span className="status-line"><i /> RADAR ONLINE</span>
            <h2>分析员登录</h2>
            <p>进入你的专属情报扇区</p>
            {error && <Alert type="error" message={error} showIcon />}
            <Form layout="vertical" requiredMark={false} onFinish={login}>
              <Form.Item label="用户名" name="username" rules={[{ required: true, message: '请输入用户名' }]}>
                <Input size="large" autoComplete="username" placeholder="analyst" />
              </Form.Item>
              <Form.Item label="密码" name="password" rules={[{ required: true, message: '请输入密码' }]}>
                <Input.Password size="large" autoComplete="current-password" placeholder="输入访问口令" />
              </Form.Item>
              <Button htmlType="submit" type="primary" size="large" block aria-label="进入雷达">
                进入雷达 <ArrowRightOutlined />
              </Button>
            </Form>
            <small>ENCRYPTED ACCESS · TLS 1.3</small>
          </div>
        </section>
      </main>
    )
  }

  return (
    <main className="dashboard-shell">
      <header className="topbar">
        <div className="brand"><RadarChartOutlined /> SIGNAL ATLAS</div>
        <div className="topbar-actions">
          <span className="live-status"><i /> LIVE SCAN</span>
          <Button type="text" icon={<LogoutOutlined />} onClick={logout} aria-label="退出登录" />
        </div>
      </header>

      <section className="dashboard-content">
        <div className="page-intro">
          <div>
            <span className="eyebrow">SOURCE NETWORK / ACTIVE</span>
            <h1>信源矩阵</h1>
            <p>编排可信技术信号，构建你的持续监听网络。</p>
          </div>
          <Button type="primary" size="large" icon={<PlusOutlined />} onClick={() => setDrawerOpen(true)} aria-label="新增信源">
            新增信源
          </Button>
        </div>

        <div className="metric-strip">
          <div><strong>{String(total).padStart(2, '0')}</strong><span>监听节点</span></div>
          <div><strong>{sources.filter((item) => item.trust_level >= 4).length}</strong><span>高可信信号</span></div>
          <div className="scan-line"><span>NETWORK COVERAGE</span><i /></div>
        </div>

        {error && <Alert className="dashboard-alert" type="error" message={error} showIcon />}
        {loading ? (
          <div className="loading-state"><Spin /><span>正在校准信源网络...</span></div>
        ) : sources.length === 0 ? (
          <button className="empty-state" type="button" onClick={() => setDrawerOpen(true)}>
            <RadarChartOutlined />
            <strong>暂无信源，建立第一个监听点。</strong>
            <span>点击接入持续产生技术信号的站点、通讯或频道</span>
          </button>
        ) : (
          <div className="source-grid">
            {sources.map((source, index) => (
              <article className="source-card" key={source.id ?? `${source.name}-${index}`}>
                <div className="card-index">{String(index + 1).padStart(2, '0')}</div>
                <div className="source-heading">
                  <div className="source-icon"><GlobalOutlined /></div>
                  <div><span>{typeLabels[source.source_type] ?? source.source_type}</span><h2>{source.name}</h2></div>
                </div>
                <p>{source.description}</p>
                <a href={source.homepage_url} target="_blank" rel="noreferrer">{source.homepage_url}</a>
                <div className="tag-row">
                  <Tag color={source.trust_level >= 4 ? 'green' : 'default'}>{trustLabels[source.trust_level] ?? source.trust_level}</Tag>
                  {source.languages.map((language) => <Tag key={language}>{language.toUpperCase()}</Tag>)}
                  {source.topics.map((topic) => <Tag key={topic}>#{topic}</Tag>)}
                </div>
              </article>
            ))}
          </div>
        )}
      </section>

      <Drawer
        title={<><span className="drawer-kicker">NEW SIGNAL POINT</span><strong>建立监听</strong></>}
        size="large"
        open={drawerOpen}
        onClose={() => setDrawerOpen(false)}
        destroyOnHidden
      >
        <p className="drawer-copy">定义信源坐标与可信度，系统将持续从此处捕获技术信号。</p>
        <Form layout="vertical" requiredMark={false} onFinish={createSource}>
          <Form.Item label="信源名称" name="name" rules={[{ required: true, message: '请输入信源名称' }]}>
            <Input placeholder="例如 OpenAI Research" />
          </Form.Item>
          <div className="form-pair">
            <Form.Item label="信源类型" name="source_type" rules={[{ required: true, message: '请选择类型' }]}>
              <Select placeholder="选择类型" options={Object.entries(typeLabels).map(([value, label]) => ({ value, label }))} />
            </Form.Item>
            <Form.Item label="可信等级" name="trust_level" rules={[{ required: true, message: '请选择可信度' }]}>
              <Select placeholder="选择等级" options={Object.entries(trustLabels).map(([value, label]) => ({ value: Number(value), label }))} />
            </Form.Item>
          </div>
          <Form.Item label="主页地址" name="homepage_url" rules={[{ required: true, type: 'url', message: '请输入有效地址' }]}>
            <Input placeholder="https://" />
          </Form.Item>
          <Form.Item label="描述" name="description" rules={[{ required: true, message: '请输入描述' }]}>
            <Input.TextArea rows={3} placeholder="这个信源持续提供什么情报？" />
          </Form.Item>
          <Form.Item label="语言" name="languages" rules={[{ required: true, message: '请输入语言' }]} extra="多个值用逗号分隔">
            <Input placeholder="zh-CN, en" />
          </Form.Item>
          <Form.Item label="关注主题" name="topics" rules={[{ required: true, message: '请输入主题' }]} extra="多个值用逗号分隔">
            <Input placeholder="AI Agent, foundation-models" />
          </Form.Item>
          <Button className="submit-source" htmlType="submit" type="primary" size="large" block>
            建立监听
          </Button>
        </Form>
      </Drawer>
    </main>
  )
}
