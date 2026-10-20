import { useEffect, useRef, useState } from 'react'
import { EditOutlined, GlobalOutlined, PlusOutlined, RadarChartOutlined, SyncOutlined } from '@ant-design/icons'
import { Alert, Button, Drawer, Empty, Input, InputNumber, Modal, Pagination, Select, Space, Spin, Switch, Tabs, Tag } from 'antd'
import { TopicChangeSummary, TopicEditor, useTopicDraft } from './TopicEditor'
import { userFacingError } from './errors'

type Request = <T,>(path: string, options?: RequestInit) => Promise<T>
type Metrics = {
  window_days: number; collected_count: number; analyzed_count: number; radar_count: number;
  pending_count: number; failed_count: number; endpoint_count: number; enabled_endpoint_count: number;
  healthy_endpoint_count: number; unhealthy_endpoint_count: number; health_status: string;
  needs_attention: boolean; last_success_at: string | null; last_error: string | null;
  sample_items: { id: number; title: string; url: string }[]
}
export type ManagedSource = {
  id: string; name: string; source_type: string; homepage_url: string | null; description: string | null;
  languages: string[]; topics: string[]; trust_level: number; status?: string; metrics?: Metrics | null
}
type Summary = { total: number; active: number; paused: number; archived: number; draft: number; needs_attention: number }
type Preferences = {
  enabled: boolean; topics: string[]; languages: string[]; excluded_terms: string[]; max_trial_sources: number;
  last_completed_at?: string | null; next_run_at?: string | null; last_error?: string | null; scope_note?: string
}
type Sample = {
  title: string; url: string; summary: string; analysis_status: string; analysis_summary: string | null;
  testing_relevance_score: number | null; testing_value_score: number | null; matched_topics: string[]
}
type Candidate = {
  id: number; name: string; homepage_url: string; feed_url: string; topics: string[]; language: string;
  evidence_url: string; reason: string; status: string; sample_count: number; relevant_count: number;
  analysis_count: number; last_error: string | null; last_checked_at: string | null; next_check_at: string | null;
  samples: Sample[]
  endpoint_type?: 'rss' | 'web_listing' | 'article';
  evaluation?: { recommendation: string; relevant_ratio: number | null; average_value: number | null; check_count: number; success_count: number }
}
type Props = {
  request: Request; canManage: boolean; canDelete: boolean; refreshKey: number;
  onAdd: () => void; onDiscover: () => void; onManualContent: (source: ManagedSource) => void;
  onEndpoints: (source: ManagedSource) => void; onEdit: (source: ManagedSource) => void;
  onDelete: (source: ManagedSource) => void
}
const defaults: Preferences = { enabled: true, topics: [], languages: ['zh', 'en'], excluded_terms: [], max_trial_sources: 3 }
const emptySummary: Summary = { total: 0, active: 0, paused: 0, archived: 0, draft: 0, needs_attention: 0 }
const states: Record<string, string> = { active: '监听中', draft: '待配置', paused: '已暂停', archived: '已归档', trial: '观察中', ready: '可评估', dismissed: '已忽略', promoted: '已启用' }
const health: Record<string, string> = { healthy: '采集正常', partial: '部分异常', unhealthy: '采集异常', unknown: '等待首次采集' }
const sourceTypes: Record<string, string> = { website: '网站', blog: '博客', newsletter: '通讯', rss: 'RSS', social: '社交媒体', wechat: '微信公众号', weibo: '微博' }
const dateText = (value?: string | null) => value ? new Date(value).toLocaleString('zh-CN', { timeZone: 'Asia/Shanghai', hour12: false }) : '暂无记录'
const errorText = (error: unknown) => userFacingError(error instanceof Error ? error.message : '操作失败，请稍后重试')
const safeLink = (value?: string | null) => value && /^https?:\/\//i.test(value) ? value : undefined
const siteLabel = (value: string) => { try { return new URL(value).hostname } catch { return '该网站' } }

export default function SourceManagementView(props: Props) {
  const { request, canManage, canDelete, refreshKey } = props
  const requestRef = useRef(request)
  requestRef.current = request
  const sequence = useRef(0)
  const [view, setView] = useState('sources')
  const [showIgnored, setShowIgnored] = useState(false)
  const [query, setQuery] = useState('')
  const [topic, setTopic] = useState('')
  const [language, setLanguage] = useState('')
  const [status, setStatus] = useState('')
  const [applied, setApplied] = useState({ query: '', topic: '', language: '', status: '' })
  const [page, setPage] = useState(1)
  const [items, setItems] = useState<ManagedSource[]>([])
  const [candidates, setCandidates] = useState<Candidate[]>([])
  const [total, setTotal] = useState(0)
  const [summary, setSummary] = useState(emptySummary)
  const [preferences, setPreferences] = useState(defaults)
  const [focusBase, setFocusBase] = useState(defaults)
  const [focusOpen, setFocusOpen] = useState(false)
  const [selected, setSelected] = useState<ManagedSource | null>(null)
  const [trial, setTrial] = useState<Candidate | null>(null)
  const [loading, setLoading] = useState(false)
  const [saving, setSaving] = useState(false)
  const [error, setError] = useState('')
  const [admissionNotice, setAdmissionNotice] = useState<{ status: string; message: string } | null>(null)
  const [revision, setRevision] = useState(0)
  const [confirmation, confirm] = useState<{ title: string; content: string; okText: string; onOk: () => Promise<unknown> } | null>(null)
  useEffect(() => {
    let active = true
    requestRef.current<Preferences>('/api/v1/source-exploration/preferences').then(data => {
      if (active && Array.isArray(data.topics)) setPreferences(data)
    }).catch(error => { if (active) setError(errorText(error)) })
    return () => { active = false }
  }, [revision, refreshKey])
  useEffect(() => {
    const ticket = ++sequence.current
    const params = new URLSearchParams({ offset: String((page - 1) * 20), limit: '20' })
    const exploring = view === 'trials'
    if (exploring && showIgnored) params.set('status', 'dismissed')
    if (!exploring) {
      if (view === 'archived') params.set('status', 'archived')
      else params.set('exclude_archived', 'true')
      if (view === 'attention') params.set('needs_attention', 'true')
      if (applied.query) params.set('q', applied.query)
      if (applied.topic) params.set('topic', applied.topic)
      if (applied.language) params.set('language', applied.language)
      if (applied.status && view !== 'archived') params.set('status', applied.status)
    }
    setLoading(true)
    requestRef.current<{ items: ManagedSource[] | Candidate[]; total: number; summary?: Summary }>(
      `${exploring ? '/api/v1/source-exploration/candidates' : '/api/v1/sources'}?${params}`,
    ).then(data => {
      if (ticket !== sequence.current) return
      if (exploring) setCandidates(data.items as Candidate[])
      else setItems((data.items as ManagedSource[]).map(source => ({ ...source, id: String(source.id) })))
      setTotal(data.total)
      if (data.summary) setSummary(data.summary)
    }).catch(error => { if (ticket === sequence.current) setError(errorText(error)) })
      .finally(() => { if (ticket === sequence.current) setLoading(false) })
    return () => { if (sequence.current === ticket) sequence.current++ }
  }, [view, page, applied, refreshKey, revision, showIgnored])
  useEffect(() => {
    if (view !== 'trials') return
    const timer = window.setInterval(() => setRevision(value => value + 1), 60_000)
    return () => window.clearInterval(timer)
  }, [view])
  const reload = () => setRevision(value => value + 1)
  const mutate = async (path: string, options: RequestInit) => {
    setSaving(true)
    setError('')
    setAdmissionNotice(null)
    try {
      const result = await requestRef.current<{ admission?: { status: string; message: string } }>(path, options)
      if (result?.admission) setAdmissionNotice(result.admission)
      reload(); setTrial(null)
    }
    catch (error) { setError(errorText(error)); throw error }
    finally { setSaving(false) }
  }
  const lifecycle = (source: ManagedSource, action: string) => {
    const run = () => mutate(`/api/v1/sources/${source.id}/lifecycle`, { method: 'POST', body: JSON.stringify({ action }) })
    if (action === 'archive') confirm({ title: `归档信源「${source.name}」？`, content: '归档后停止新采集，保留历史内容和去重记录，可随时恢复。', okText: '确认归档', onOk: run })
    else void run().catch(() => {})
  }
  const promote = (candidate: Candidate) => confirm({
    title: candidate.endpoint_type === 'article' ? `收录网站「${siteLabel(candidate.homepage_url)}」？` : `正式启用「${candidate.name}」？`,
    content: '确认后将网站加入信源管理并探测RSS/Atom；未发现订阅时尝试验证网页列表，仍不可用则保留为待配置。同站会复用已有信源，样例文章不直接写入正式内容；已暂停或归档的信源保持原状态。',
    okText: candidate.endpoint_type === 'article' ? '确认收录网站' : '确认启用', onOk: async () => {
      await mutate(`/api/v1/source-exploration/candidates/${candidate.id}/promote`, { method: 'POST' })
      setView('sources'); setPage(1)
    },
  })
  const candidateAction = (candidate: Candidate, action: string) => {
    const run = () => mutate(`/api/v1/source-exploration/candidates/${candidate.id}/action`, { method: 'POST', body: JSON.stringify({ action }) })
    if (action === 'dismiss') confirm({ title: `忽略「${candidate.name}」？`, content: '平台会记住这个决定，后续不会反复推荐。', okText: '确认忽略', onOk: run })
    else void run().catch(() => {})
  }
  return <>
    <Modal open={Boolean(confirmation)} title={confirmation?.title} confirmLoading={saving} closable={!saving} mask={{ closable: !saving }} keyboard={!saving} okText={confirmation?.okText} cancelText="取消" onCancel={() => { if (!saving) confirm(null) }} onOk={async () => { if (!confirmation || saving) return; try { await confirmation.onOk(); confirm(null) } catch { /* Preserve the dialog for retry. */ } }}>
      <p>{confirmation?.content}</p>{error && confirmation && <Alert type="error" title={error} />}
    </Modal>
    <div className="page-intro"><div><span className="eyebrow">SOURCE NETWORK</span><h1>信源管理</h1><p>定义关注方向，平台持续探索；用实际内容决定哪些信源值得保留。</p></div>
      {canManage && <Space wrap><Button onClick={() => { setFocusBase(preferences); setFocusOpen(true) }}>管理关注方向</Button><Button type="primary" aria-label="通过网址添加" icon={<PlusOutlined />} onClick={props.onDiscover}>通过网址添加</Button><Button aria-label="新增信源" onClick={props.onAdd}>手动新增信源</Button></Space>}
    </div>
    <div className="source-focus-summary"><strong>平台关注方向</strong><span>{preferences.topics.length ? preferences.topics.join(' · ') : '尚未设置；设置后平台会自动探索新信源。'}</span><Tag>{preferences.enabled ? '自动探索已启用' : '自动探索已暂停'}</Tag></div>
    <div className="source-workspace-metrics"><div><strong>{summary.total}</strong><span>全部信源</span></div><div><strong>{summary.active}</strong><span>正在监听</span></div><div><strong>{summary.paused}</strong><span>已暂停</span></div><div><strong>{summary.needs_attention}</strong><span>需处理</span></div></div>
    {error && <Alert type="error" showIcon title={error} closable onClose={() => setError('')} />}
    {admissionNotice && <Alert type={admissionNotice.status === 'active' ? 'success' : 'warning'} showIcon title={admissionNotice.message} closable onClose={() => setAdmissionNotice(null)} />}
    <Tabs activeKey={view} onChange={value => { setView(value); setPage(1) }} items={[
      { key: 'sources', label: '信源列表' }, { key: 'trials', label: '发现与试运行' },
      { key: 'attention', label: `需处理${summary.needs_attention ? ` ${summary.needs_attention}` : ''}` }, { key: 'archived', label: '已归档' },
    ]} tabBarExtraContent={<Button icon={<SyncOutlined />} onClick={reload} aria-label="刷新信源">刷新</Button>} />
    {view !== 'trials' ? <div className="source-workspace-filters">
      <Input aria-label="搜索信源" placeholder="搜索名称或域名" value={query} onChange={event => setQuery(event.target.value)} onPressEnter={() => { setApplied({ query, topic, language, status }); setPage(1) }} />
      <Input aria-label="筛选内容主题" placeholder="内容主题（精确匹配）" value={topic} onChange={event => setTopic(event.target.value)} />
      <Select id="source-filter-language" aria-label="筛选信源语言" value={language} onChange={setLanguage} options={[{ value: '', label: '全部语言' }, { value: 'zh', label: '中文' }, { value: 'en', label: '英文' }]} />
      {view !== 'archived' && <Select id="source-filter-status" aria-label="筛选运行状态" value={status} onChange={setStatus} options={[{ value: '', label: '全部状态' }, { value: 'active', label: '监听中' }, { value: 'paused', label: '已暂停' }, { value: 'draft', label: '待配置' }]} />}
      <Button onClick={() => { setApplied({ query, topic, language, status }); setPage(1) }}>筛选信源</Button>
      <Button type="text" onClick={() => { setQuery(''); setTopic(''); setLanguage(''); setStatus(''); setApplied({ query: '', topic: '', language: '', status: '' }); setPage(1) }}>重置</Button>
    </div> : <div className="source-trial-explanation"><p>平台沿真实链接发现网站及文章线索，自动积累样例，由你确认收录网站并探测订阅。试运行内容不会进入正式邮件。</p><p>{preferences.scope_note}</p><small>最近完成：{dateText(preferences.last_completed_at)} · 下次检查：{dateText(preferences.next_run_at)}</small><p>持续网站使用配置的试运行上限；文章线索另外最多保留10个待评估候选，确认后收录的是网站。仅支持公开可读页面，登录、验证码或动态加载可能阻止读取。</p><div><Button type="text" onClick={() => { setShowIgnored(value => !value); setPage(1) }}>{showIgnored ? '返回试运行' : '查看已忽略'}</Button></div>{preferences.last_error && <Alert type="warning" title={preferences.last_error} />}</div>}
    <Spin spinning={loading}><div className="source-workspace-list">
      {view === 'trials' ? candidates.map(candidate => <article className="source-evidence-card" key={candidate.id}>
        <div className="source-evidence-heading"><div><h2>{candidate.name}</h2><p>{candidate.reason}</p></div><Tag>{states[candidate.status] ?? candidate.status}</Tag></div>
        <div className="tag-row"><Tag>{candidate.endpoint_type === 'article' ? '文章线索' : candidate.endpoint_type === 'web_listing' ? '网页列表' : 'RSS / Atom'}</Tag>{candidate.topics.map(topic => <Tag key={topic}>{topic}</Tag>)}</div>
        <p>实际样例 {candidate.sample_count} · 已分析 {candidate.analysis_count} · 测试相关 {candidate.relevant_count}/{candidate.analysis_count || '待分析'}</p>
        {candidate.endpoint_type !== 'article' && candidate.analysis_count < 5 && <small>样本仍在积累，低频更新不会被判为低质量。</small>}
        {candidate.evaluation && <p>{candidate.evaluation.recommendation}</p>}
        {candidate.last_error && <Alert type="warning" title={candidate.last_error} />}
        <div className="card-actions"><Button onClick={() => setTrial(candidate)}>查看样例与评估</Button>{canManage && <><Button disabled={saving} onClick={() => candidateAction(candidate, ['paused', 'dismissed'].includes(candidate.status) ? 'resume' : 'pause')}>{['paused', 'dismissed'].includes(candidate.status) ? '恢复试运行' : '暂停试运行'}</Button>{candidate.status !== 'dismissed' && <Button disabled={saving} onClick={() => candidateAction(candidate, 'dismiss')}>忽略</Button>}</>}</div>
      </article>) : items.map(source => <article className="source-evidence-card" key={source.id}>
        <div className="source-evidence-heading"><div><span className="source-evidence-domain"><GlobalOutlined /> {source.homepage_url ? new URL(source.homepage_url).hostname : '人工维护的来源'}</span><h2>{source.name}</h2></div><Tag>{states[source.status ?? 'active'] ?? source.status}</Tag></div>
        <p>{source.description || '暂无描述'}</p><div className="tag-row"><Tag>{sourceTypes[source.source_type] ?? source.source_type}</Tag>{source.languages.map(lang => <Tag key={lang}>{lang.toUpperCase()}</Tag>)}{source.topics.map(topic => <Tag key={topic}>#{topic}</Tag>)}</div>
        {source.metrics ? <><div className="source-contribution"><strong>近30天</strong><span>已分析 {source.metrics.analyzed_count}</span><span>入选 {source.metrics.radar_count}</span><span>待分析 {source.metrics.pending_count}</span>{source.metrics.failed_count > 0 && <span>分析失败 {source.metrics.failed_count}</span>}</div>
          <div className="source-health"><Tag color={source.metrics.health_status === 'unhealthy' ? 'orange' : undefined}>{['wechat', 'weibo'].includes(source.source_type) && source.metrics.endpoint_count === 0 ? '人工录入' : !source.metrics.enabled_endpoint_count ? '尚未启用采集端点' : health[source.metrics.health_status]}</Tag><small>最近成功：{dateText(source.metrics.last_success_at)}</small></div>
          {source.metrics.last_error && <p className="source-inline-warning">{source.metrics.last_error}</p>}</> : <small>贡献数据等待更新</small>}
        <div className="card-actions"><Button onClick={() => setSelected(source)}>查看贡献</Button><Button icon={<RadarChartOutlined />} onClick={() => props.onEndpoints(source)}>采集端点</Button>
          {canManage && <><Button type="text" icon={<EditOutlined />} onClick={() => props.onEdit(source)}>编辑</Button>{['wechat', 'weibo'].includes(source.source_type) && <Button aria-label={`录入内容 ${source.name}`} onClick={() => props.onManualContent(source)}>录入内容</Button>}
            {source.status === 'archived' ? <Button onClick={() => lifecycle(source, 'restore')} disabled={saving}>恢复信源</Button> : <><Button disabled={saving} onClick={() => lifecycle(source, source.status === 'paused' ? 'resume' : 'pause')}>{source.status === 'paused' ? '恢复监听' : '暂停监听'}</Button><Button aria-label="归档信源" disabled={saving} onClick={() => lifecycle(source, 'archive')}>归档</Button></>}
          </>}
        </div>
      </article>)}
      {!loading && (view === 'trials' ? !candidates.length : !items.length) && <Empty description={view === 'trials' ? '暂无试运行信源。配置关注方向后，平台会自动探索并在这里展示样例。' : '没有符合条件的信源'} />}
    </div></Spin>
    {total > 20 && <Pagination current={page} total={total} pageSize={20} showSizeChanger={false} onChange={setPage} />}
    <Drawer open={Boolean(selected)} aria-labelledby="source-contribution-title" title={<span id="source-contribution-title">{selected?.name ?? ''} · 内容贡献</span>} onClose={() => setSelected(null)} size="large">
      <p>统计近30天仍保留且归属于此信源的内容。待分析单列，入选数不代表独家贡献或事实可信度。</p>
      {selected?.metrics && <><p>已分析 {selected.metrics.analyzed_count} · 入选 {selected.metrics.radar_count} · 待分析 {selected.metrics.pending_count}</p><h3>最近内容</h3>{selected.metrics.sample_items.length ? selected.metrics.sample_items.map(item => <p key={item.id}><a href={safeLink(item.url)} target="_blank" rel="noreferrer">{item.title}</a></p>) : <p>该窗口内暂无内容，更新慢不代表质量低。</p>}</>}
      {canDelete && selected && <div className="source-danger-zone"><h3>高级操作</h3><p>日常停用请使用暂停或归档。永久删除会清除关联内容。</p><Button danger onClick={() => { props.onDelete(selected); setSelected(null) }}>永久删除信源</Button></div>}
    </Drawer>
    <Drawer open={Boolean(trial)} aria-labelledby="source-trial-title" title={<span id="source-trial-title">{trial?.name ?? ''} · 试运行评估</span>} onClose={() => setTrial(null)} size="large">
      {trial && <><p>{trial.reason}</p><p><a href={safeLink(trial.homepage_url)} target="_blank" rel="noreferrer">网站主页</a> · <a href={safeLink(trial.evidence_url)} target="_blank" rel="noreferrer">发现依据</a> · <a href={safeLink(trial.feed_url)} target="_blank" rel="noreferrer">{trial.endpoint_type === 'article' ? '原始内容' : trial.endpoint_type === 'web_listing' ? '持续跟踪页面' : '已验证订阅'}</a></p><p>实际样例 {trial.sample_count} · 已分析 {trial.analysis_count} · 测试相关 {trial.relevant_count}</p>
        {trial.evaluation && <><p>{trial.evaluation.recommendation}</p><p>相关样例占比：{trial.evaluation.relevant_ratio === null ? '待分析' : `${trial.evaluation.relevant_ratio}%`} · 平均测试价值：{trial.evaluation.average_value ?? '待分析'} · 成功读取 {trial.evaluation.success_count}/{trial.evaluation.check_count} 次</p><small>指标反映当前样例，不代表内容独立性、事实可信度或生产验证结果。</small></>}
        <p>“可评估”表示已积累分析样本，不保证对所有人都有价值。请根据原文决定。</p>
        {trial.samples.map(sample => <article className="source-trial-sample" key={sample.url}><h3><a href={safeLink(sample.url)} target="_blank" rel="noreferrer">{sample.title}</a></h3><p>{sample.summary}</p>{sample.analysis_status === 'analyzed' ? <><Tag>相关 {sample.testing_relevance_score}</Tag><Tag>价值 {sample.testing_value_score}</Tag><p>{sample.analysis_summary}</p></> : <Tag>{sample.analysis_status === 'failed' ? '分析待重试' : '待分析'}</Tag>}</article>)}
        {canManage && ['trial', 'ready'].includes(trial.status) && <Button type="primary" block disabled={!trial.sample_count || saving} onClick={() => promote(trial)}>{trial.endpoint_type === 'article' ? '收录此网站并探测RSS' : '确认正式启用'}</Button>}
      </>}
    </Drawer>
    <FocusDrawer open={focusOpen} base={focusBase} saving={saving} error={error} onClose={() => setFocusOpen(false)} onSave={async values => {
      setSaving(true); setError('')
      try { const saved = await requestRef.current<Preferences>('/api/v1/source-exploration/preferences', { method: 'PUT', body: JSON.stringify(values) }); setPreferences(saved); setFocusOpen(false); reload() }
      catch (error) { setError(errorText(error)) }
      finally { setSaving(false) }
    }} />
  </>
}

function FocusDrawer({ open, base, saving, error, onClose, onSave }: { open: boolean; base: Preferences; saving: boolean; error: string; onClose: () => void; onSave: (values: Preferences) => void }) {
  const editor = useTopicDraft(base.topics, open, saving)
  const [enabled, setEnabled] = useState(base.enabled)
  const [languages, setLanguages] = useState(base.languages)
  const [excluded, setExcluded] = useState(base.excluded_terms.join('\n'))
  const [cap, setCap] = useState(base.max_trial_sources)
  useEffect(() => { if (open) { setEnabled(base.enabled); setLanguages(base.languages); setExcluded(base.excluded_terms.join('\n')); setCap(base.max_trial_sources) } }, [base, open])
  return <Drawer aria-labelledby="source-focus-title" title={<span id="source-focus-title">平台关注方向</span>} open={open} onClose={() => { if (!saving) onClose() }} closable={!saving} mask={{ closable: !saving }} keyboard={!saving} size="large" destroyOnHidden
    footer={<><TopicChangeSummary editor={editor} /><Space><Button disabled={saving} onClick={onClose}>取消</Button><Button type="primary" loading={saving} disabled={editor.blocked} onClick={() => onSave({ enabled, topics: editor.values, languages, excluded_terms: excluded.split('\n').map(value => value.trim()).filter(Boolean), max_trial_sources: cap })}>保存关注方向</Button></Space></>}>
    <p>告诉平台你需要什么。平台自动探索和限额试运行，再由你确认启用；这里的方向与单个信源的内容主题分别管理。</p>
    {error && <Alert type="error" title={error} />}
    <div className="source-focus-option"><label htmlFor="exploration-enabled">自动探索</label><Switch id="exploration-enabled" checked={enabled} disabled={saving} onChange={setEnabled} /></div>
    <TopicEditor editor={editor} inputId="source-focus-topic" emptyText="例如 AI辅助测试、Agent评测、接口自动化。" clearedText="尚未设置方向时不会自动探索，已有正式监听继续运行。" />
    <div className="source-focus-option"><label htmlFor="exploration-languages">语言范围</label><Select id="exploration-languages" mode="multiple" value={languages} onChange={setLanguages} disabled={saving} options={[{ value: 'zh', label: '中文' }, { value: 'en', label: '英文' }]} /></div>
    <label htmlFor="exploration-excluded">排除方向（每行一项）</label><Input.TextArea id="exploration-excluded" value={excluded} onChange={event => setExcluded(event.target.value)} disabled={saving} rows={3} />
    <div className="source-focus-option"><label htmlFor="exploration-cap">同时试运行上限</label><InputNumber id="exploration-cap" min={1} max={5} value={cap} disabled={saving} onChange={value => setCap(value ?? 3)} /></div>
    <p>每个试运行保留最多20篇独立样例。样本少时继续观察，不因低频自动淘汰。</p>
  </Drawer>
}
