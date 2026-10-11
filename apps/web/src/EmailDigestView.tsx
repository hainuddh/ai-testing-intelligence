import { useEffect, useState } from 'react'
import { Alert, Button, Form, Input, InputNumber, Modal, Space, Spin, Switch, Table, Tag } from 'antd'

type Request = <T>(path: string, options?: RequestInit) => Promise<T>
type PreferenceForm = { enabled: boolean; recipient: string; value_threshold: number; send_time: string }
type Preferences = PreferenceForm & { smtp_configured: boolean }
type Delivery = {
  id: number; recipient: string; business_date: string; status: string; item_count: number;
  attempts: number; created_at: string; sent_at: string | null;
  next_attempt_at: string | null; error_message: string | null
}
const statusLabels: Record<string, string> = {
  sent: '已发送', skipped: '无新内容', pending: '等待发送', sending: '发送中',
  failed: '发送失败', uncertain: '待确认',
}
const timeText = (value: string | null) => value
  ? new Intl.DateTimeFormat('zh-CN', { timeZone: 'Asia/Shanghai', dateStyle: 'short', timeStyle: 'short' }).format(new Date(value.endsWith('Z') || /[+-]\d\d:\d\d$/.test(value) ? value : `${value}Z`))
  : '—'

export default function EmailDigestView({ request }: { request: Request }) {
  const [form] = Form.useForm<PreferenceForm>()
  const [preferences, setPreferences] = useState<Preferences | null>(null)
  const [deliveries, setDeliveries] = useState<Delivery[]>([])
  const [total, setTotal] = useState(0)
  const [page, setPage] = useState(1)
  const [loading, setLoading] = useState(true)
  const [historyLoading, setHistoryLoading] = useState(false)
  const [saving, setSaving] = useState(false)
  const [saved, setSaved] = useState(false)
  const [error, setError] = useState('')

  const loadHistory = async (nextPage = page) => {
    setHistoryLoading(true)
    try {
      const data = await request<{ items: Delivery[]; total: number }>(`/api/v1/email-digest/deliveries?offset=${(nextPage - 1) * 20}&limit=20`)
      setDeliveries(data.items)
      setTotal(data.total)
      setPage(nextPage)
    } catch (err) {
      setError(err instanceof Error ? err.message : '读取发送记录失败')
    } finally { setHistoryLoading(false) }
  }

  useEffect(() => {
    let active = true
    Promise.all([
      request<Preferences>('/api/v1/email-digest/preferences'),
      request<{ items: Delivery[]; total: number }>('/api/v1/email-digest/deliveries?offset=0&limit=20'),
    ]).then(([prefs, history]) => {
      if (!active) return
      setPreferences(prefs)
      form.setFieldsValue(prefs)
      setDeliveries(history.items)
      setTotal(history.total)
    }).catch((err: unknown) => {
      if (active) setError(err instanceof Error ? err.message : '读取邮件设置失败')
    }).finally(() => { if (active) setLoading(false) })
    return () => { active = false }
  }, [])

  const save = async (values: PreferenceForm) => {
    setSaving(true)
    setSaved(false)
    setError('')
    try {
      const payload = { enabled: values.enabled, recipient: values.recipient.trim(), value_threshold: values.value_threshold, send_time: values.send_time }
      const data = await request<Preferences>('/api/v1/email-digest/preferences', { method: 'PUT', body: JSON.stringify(payload) })
      setPreferences(data)
      form.setFieldsValue(data)
      setSaved(true)
    } catch (err) {
      setError(err instanceof Error ? err.message : '保存邮件设置失败')
    } finally { setSaving(false) }
  }

  const retry = async (delivery: Delivery) => {
    setError('')
    try {
      await request(`/api/v1/email-digest/deliveries/${delivery.id}/retry`, { method: 'POST' })
      await loadHistory()
    } catch (err) { setError(err instanceof Error ? err.message : '重试失败') }
  }

  return <div className="email-digest-view">
    <div className="page-intro"><div><span className="eyebrow">DAILY INTELLIGENCE / EMAIL</span><h1>每日情报邮件</h1><p>每天汇总新完成分析的高价值内容，直接送达指定邮箱。</p></div></div>
    {error && <Alert type="error" title={error} showIcon />}
    {saved && <Alert type="success" title="邮件设置已保存" showIcon />}
    {loading ? <div className="loading-state"><Spin /><span>正在读取邮件设置...</span></div> : preferences && <>
      {!preferences.smtp_configured && <Alert type="warning" showIcon title="尚未配置发件服务，保存设置后仍需由部署管理员配置发件服务才能投递。" />}
      <div className="email-settings-grid">
        <section className="email-settings-panel" aria-label="邮件设置">
          <Form form={form} layout="vertical" onFinish={save}>
            <Form.Item name="enabled" label="启用每日邮件" valuePropName="checked"><Switch aria-label="启用每日邮件" /></Form.Item>
            <Form.Item name="recipient" label="收件邮箱" dependencies={['enabled']} rules={[
              { type: 'email', message: '请输入有效的收件邮箱' },
              ({ getFieldValue }) => ({ required: getFieldValue('enabled'), message: '启用前请填写收件邮箱' }),
            ]}><Input type="email" maxLength={254} placeholder="reader@example.com" /></Form.Item>
            <Form.Item name="value_threshold" label="价值分超过" rules={[{ required: true, message: '请填写价值分门槛' }]} extra="严格超过此分数，默认70分即71分及以上。"><InputNumber min={0} max={100} precision={0} /></Form.Item>
            <Form.Item name="send_time" label="每日发送时间" rules={[{ required: true, message: '请选择发送时间' }]} extra="北京时间（Asia/Shanghai）"><Input type="time" /></Form.Item>
            <Button type="primary" htmlType="submit" loading={saving}>保存邮件设置</Button>
          </Form>
        </section>
        <aside className="email-settings-panel email-rules"><h2>收到什么</h2><p>只推送主雷达中达到价值门槛的内容，按价值分排序。邮件正文包含标题、评分、摘要和落地建议，完整报告随信附上。</p><h2>何时收到</h2><p>新邮箱从启用时开始接收新完成分析的情报。没有新内容时跳过当天，晚完成分析的内容留到下一次汇总。每封最多100条，剩余内容留待后续发送。</p><h2>发送记录</h2><p>已发送内容按邮箱去重。失败会自动重试，最多尝试3次；待确认的邮件需要核对收件箱，再决定是否重试。</p></aside>
      </div>
      <section className="email-history"><div className="email-history-heading"><h2>发送记录</h2><Button onClick={() => void loadHistory()} loading={historyLoading}>刷新发送记录</Button></div>
        <Table<Delivery> rowKey="id" dataSource={deliveries} loading={historyLoading} scroll={{ x: 850 }} pagination={{ current: page, total, pageSize: 20, showSizeChanger: false, onChange: next => void loadHistory(next) }} columns={[
          { title: '日报日期', dataIndex: 'business_date' },
          { title: '收件邮箱', dataIndex: 'recipient' },
          { title: '情报数', dataIndex: 'item_count' },
          { title: '发送状态', key: 'status', render: (_, row) => <Space orientation="vertical"><Tag color={row.status === 'sent' ? 'green' : row.status === 'failed' || row.status === 'uncertain' ? 'orange' : 'default'}>{statusLabels[row.status] || row.status}</Tag>{row.error_message && <small>{row.error_message}</small>}{row.next_attempt_at && <small>下次尝试：{timeText(row.next_attempt_at)}</small>}</Space> },
          { title: '尝试次数', dataIndex: 'attempts' },
          { title: '发送时间（北京）', dataIndex: 'sent_at', render: timeText },
          { title: '操作', key: 'action', render: (_, row) => ['failed', 'uncertain'].includes(row.status) && <Button size="small" disabled={!preferences.enabled || row.recipient !== preferences.recipient} onClick={() => Modal.confirm({ title: '重试这封邮件？', content: row.status === 'uncertain' ? '请先核对收件箱。如果已收到，再次投递会产生重复邮件。' : '将按原报告内容重新投递到此收件邮箱。', okText: '确认重试', cancelText: '取消', onOk: () => retry(row) })}>重试</Button> },
        ]} />
      </section>
    </>}
  </div>
}
