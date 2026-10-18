import { useEffect, useState } from 'react'
import { Alert, Button, Form, Input, InputNumber, Modal, Select, Space, Spin, Switch, Table, Tag } from 'antd'

type Request = <T>(path: string, options?: RequestInit) => Promise<T>
type PreferenceForm = { enabled: boolean; value_threshold: number; send_time: string }
type Preferences = PreferenceForm & { smtp_configured: boolean }
type RecipientForm = { email: string; name: string; enabled: boolean }
type Recipient = RecipientForm & { id: number; started_at: string | null; created_at: string }
type Delivery = {
  id: number; recipient: string; business_date: string; status: string; item_count: number;
  attempts: number; created_at: string; sent_at: string | null;
  next_attempt_at: string | null; error_message: string | null
}
type Batch = {
  id: number; business_date: string; status: string; prepare_at: string; cutoff_at: string;
  send_at: string; sources_total: number; sources_completed: number; sources_skipped?: number;
  analysis_total: number; analysis_pending: number; github_status: string;
  error_message: string | null; next_attempt_at: string | null
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
  const [recipientForm] = Form.useForm<RecipientForm>()
  const [preferences, setPreferences] = useState<Preferences | null>(null)
  const [recipients, setRecipients] = useState<Recipient[]>([])
  const [editing, setEditing] = useState<Recipient | null | undefined>(undefined)
  const [recipientSaving, setRecipientSaving] = useState(false)
  const [recipientBusy, setRecipientBusy] = useState<number | null>(null)
  const [recipientError, setRecipientError] = useState('')
  const [recipientNotice, setRecipientNotice] = useState('')
  const [recipientQuery, setRecipientQuery] = useState('')
  const [historyRecipient, setHistoryRecipient] = useState<string | undefined>()
  const [deliveries, setDeliveries] = useState<Delivery[]>([])
  const [batches, setBatches] = useState<Batch[]>([])
  const [progressRefresh, setProgressRefresh] = useState(0)
  const [total, setTotal] = useState(0)
  const [page, setPage] = useState(1)
  const [loading, setLoading] = useState(true)
  const [historyLoading, setHistoryLoading] = useState(false)
  const [saving, setSaving] = useState(false)
  const [saved, setSaved] = useState(false)
  const [error, setError] = useState('')
  const sendTime = Form.useWatch('send_time', form) ?? preferences?.send_time ?? '09:00'
  const [sendHour, sendMinute] = sendTime.split(':').map(Number)
  const preparationTime = /^([01]\d|2[0-3]):[0-5]\d$/.test(sendTime)
    ? `${sendHour < 2 ? '前一天' : '每天'}${String((sendHour + 22) % 24).padStart(2, '0')}:${String(sendMinute).padStart(2, '0')}`
    : '请选择有效的发送时间'
  const cutoffMinute = (sendHour * 60 + sendMinute - 10 + 1440) % 1440
  const cutoffTime = /^([01]\d|2[0-3]):[0-5]\d$/.test(sendTime)
    ? `${sendHour * 60 + sendMinute < 10 ? '前一天' : '每天'}${String(Math.floor(cutoffMinute / 60)).padStart(2, '0')}:${String(cutoffMinute % 60).padStart(2, '0')}`
    : '请选择有效的发送时间'

  const loadHistory = async (nextPage = page, recipient = historyRecipient) => {
    setHistoryLoading(true)
    try {
      const filter = recipient ? `&recipient=${encodeURIComponent(recipient)}` : ''
      const data = await request<{ items: Delivery[]; total: number }>(`/api/v1/email-digest/deliveries?offset=${(nextPage - 1) * 20}&limit=20${filter}`)
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
      request<Recipient[]>('/api/v1/email-digest/recipients'),
      request<{ items: Delivery[]; total: number }>('/api/v1/email-digest/deliveries?offset=0&limit=20'),
    ]).then(([prefs, mailboxes, history]) => {
      if (!active) return
      setPreferences(prefs)
      form.setFieldsValue(prefs)
      setRecipients(mailboxes)
      setDeliveries(history.items)
      setTotal(history.total)
    }).catch((err: unknown) => {
      if (active) setError(err instanceof Error ? err.message : '读取邮件设置失败')
    }).finally(() => { if (active) setLoading(false) })
    return () => { active = false }
  }, [])

  useEffect(() => {
    let active = true
    const refresh = async () => {
      try {
        const data = await request<{ items: Batch[] }>('/api/v1/email-digest/batches')
        if (active) setBatches(data.items ?? [])
      } catch (err) {
        if (active) setError(err instanceof Error ? err.message : '读取准备进度失败')
      }
    }
    void refresh()
    const timer = setInterval(() => { void refresh() }, 30000)
    return () => { active = false; clearInterval(timer) }
  }, [progressRefresh])

  useEffect(() => {
    if (editing === undefined) return
    recipientForm.resetFields()
    recipientForm.setFieldsValue(editing ?? { email: '', name: '', enabled: true })
    setRecipientError('')
  }, [editing])

  const save = async (values: PreferenceForm) => {
    if (values.enabled && !recipients.some(row => row.enabled)) {
      setError('启用邮件推送前请添加并启用至少一个收件邮箱')
      return
    }
    setSaving(true)
    setSaved(false)
    setError('')
    try {
      const payload = { enabled: values.enabled, value_threshold: values.value_threshold, send_time: values.send_time }
      const data = await request<Preferences>('/api/v1/email-digest/preferences', { method: 'PUT', body: JSON.stringify(payload) })
      setPreferences(data)
      form.setFieldsValue(data)
      setSaved(true)
    } catch (err) {
      setError(err instanceof Error ? err.message : '保存邮件设置失败')
    } finally { setSaving(false) }
  }

  const saveMailbox = async (values: RecipientForm) => {
    setRecipientSaving(true)
    setRecipientError('')
    try {
      const data = await request<Recipient>(editing ? `/api/v1/email-digest/recipients/${editing.id}` : '/api/v1/email-digest/recipients', {
        method: editing ? 'PATCH' : 'POST',
        body: JSON.stringify({ email: values.email.trim(), name: values.name.trim(), enabled: values.enabled }),
      })
      setRecipients(rows => editing ? rows.map(row => row.id === data.id ? data : row) : [...rows, data])
      setEditing(undefined)
      setRecipientNotice('收件邮箱已保存')
    } catch (err) {
      setRecipientError(err instanceof Error ? err.message : '保存收件邮箱失败')
    } finally { setRecipientSaving(false) }
  }

  const toggleMailbox = async (row: Recipient, enabled: boolean) => {
    setRecipientBusy(row.id)
    setError('')
    try {
      const data = await request<Recipient>(`/api/v1/email-digest/recipients/${row.id}`, { method: 'PATCH', body: JSON.stringify({ enabled }) })
      setRecipients(rows => rows.map(item => item.id === row.id ? data : item))
      setRecipientNotice(enabled ? '收件邮箱已启用' : '收件邮箱已暂停')
    } catch (err) {
      setError(err instanceof Error ? err.message : '修改收件邮箱状态失败')
    } finally { setRecipientBusy(null) }
  }

  const removeMailbox = (row: Recipient) => Modal.confirm({
    title: '删除收件邮箱？', content: `删除${row.email}后停止向该地址发送邮件，已有发送记录仍会保留。`,
    okText: '确认删除', okButtonProps: { danger: true }, cancelText: '取消',
    onOk: async () => {
      setRecipientBusy(row.id)
      setError('')
      try {
        await request(`/api/v1/email-digest/recipients/${row.id}`, { method: 'DELETE' })
        setRecipients(rows => rows.filter(item => item.id !== row.id))
        setRecipientNotice('收件邮箱已删除')
      } catch (err) {
        setError(err instanceof Error ? err.message : '删除收件邮箱失败')
        throw err
      } finally { setRecipientBusy(null) }
    },
  })

  const retry = async (delivery: Delivery) => {
    setError('')
    try {
      await request(`/api/v1/email-digest/deliveries/${delivery.id}/retry`, { method: 'POST' })
      await loadHistory()
    } catch (err) { setError(err instanceof Error ? err.message : '重试失败') }
  }

  return <div className="email-digest-view">
    <div className="page-intro"><div><span className="eyebrow">DAILY INTELLIGENCE / EMAIL</span><h1>每日情报邮件</h1><p>每天汇总高价值内容情报和GitHub项目研究日报，分别送达已启用的收件邮箱。</p></div></div>
    {error && <Alert type="error" title={error} showIcon />}
    {saved && <Alert type="success" title="邮件设置已保存" showIcon />}
    {recipientNotice && <Alert type="success" title={recipientNotice} showIcon closable onClose={() => setRecipientNotice('')} />}
    {loading ? <div className="loading-state"><Spin /><span>正在读取邮件设置...</span></div> : preferences && <>
      {!preferences.smtp_configured && <Alert type="warning" showIcon title="尚未配置发件服务，保存设置后仍需由部署管理员配置发件服务才能投递。" />}
      <div className="email-settings-grid">
        <section className="email-settings-panel" aria-label="邮件设置">
          <Form form={form} layout="vertical" onFinish={save}>
            <Form.Item name="enabled" label="启用每日邮件" valuePropName="checked"><Switch aria-label="启用每日邮件" /></Form.Item>
            <Form.Item name="value_threshold" label="价值分超过" rules={[{ required: true, message: '请填写价值分门槛' }]} extra="用于内容情报，严格超过此分数；默认70分即71分及以上。GitHub日报按研究结果发送。"><InputNumber min={0} max={100} precision={0} /></Form.Item>
            <Form.Item name="send_time" label="每日发送时间" rules={[{ required: true, message: '请选择发送时间' }]} extra={`准备时间：${preparationTime}；截稿时间：${cutoffTime}（北京时间）。已开始的当天安排保持不变，时间修改从下一批生效。`}><Input type="time" /></Form.Item>
            <Button type="primary" htmlType="submit" loading={saving}>保存邮件设置</Button>
          </Form>
        </section>
        <aside className="email-settings-panel email-rules"><h2>收到什么</h2><p>内容情报选择主雷达中严格超过配置门槛的新内容，已发布的GitHub日报按研究结果发送。两类内容分别按价值分排序。正文包含评分、摘要和落地建议，完整研究报告随信附上。</p><h2>何时收到</h2><p>持续采集内容，按发送时间提前2小时补齐当天采集和研究，发送前10分钟截稿。采集、内容分析和GitHub研究完成后保存本次邮件，到配置时间由独立投递服务发送。完整优先：本批任务未完成时延迟发送并继续重试，页面显示进度和原因。任一类有新内容就发送，只有两类都为空才跳过。截稿后新内容留到下一天，本批已开始但晚完成的任务仍计入当天。实际送达时间还受邮件服务影响。新邮箱从启用时开始接收，每封合计最多100条。</p><h2>发送记录</h2><p>每个邮箱每个日报日期最多投递一封，分别去重和重试，收件人只看到自己的地址。暂停后恢复会保留原来的订阅窗口。待确认的邮件需要核对收件箱，再决定是否重试。</p></aside>
      </div>
      <section className="email-settings-panel" aria-label="每日准备进度">
        <div className="email-history-heading"><h2>每日准备进度</h2><Button onClick={() => setProgressRefresh(value => value + 1)}>刷新准备进度</Button></div>
        {batches.length === 0 ? <p>等待下一次准备时间；完整报告准备好后按计划发送。</p> : batches.map(batch => <div key={batch.id}>
          <Space wrap><strong>{batch.business_date}</strong><Tag color={batch.status === 'delayed' ? 'orange' : batch.status === 'ready' ? 'green' : 'blue'}>{batch.status === 'delayed' ? '延迟准备' : batch.status === 'ready' ? '准备完成' : '准备中'}</Tag>
            <span>采集{batch.sources_completed}/{batch.sources_total}{Boolean(batch.sources_skipped) && `（已跳过${batch.sources_skipped}）`} · 分析{batch.analysis_total - batch.analysis_pending}/{batch.analysis_total} · GitHub研究{batch.github_status === 'completed' ? '已完成' : batch.github_status === 'failed' ? '等待重试' : '准备中'}</span></Space>
          <p>开始：{timeText(batch.prepare_at)} · 截稿：{timeText(batch.cutoff_at)} · 计划发送：{timeText(batch.send_at)}</p>
          {batch.error_message && <p>{batch.error_message}</p>}
          {batch.next_attempt_at && <p>下次重试：{timeText(batch.next_attempt_at)}</p>}
        </div>)}
      </section>
      <section className="email-recipients" aria-label="收件邮箱管理">
        <div className="email-history-heading"><div><h2>收件邮箱管理</h2><p>{recipients.length}个邮箱，{recipients.filter(row => row.enabled).length}个已启用</p></div><Space wrap><Input aria-label="搜索收件邮箱" placeholder="搜索邮箱或名称" value={recipientQuery} onChange={event => setRecipientQuery(event.target.value)} allowClear /><Button type="primary" onClick={() => setEditing(null)}>新增收件邮箱</Button></Space></div>
        <Table<Recipient> rowKey="id" dataSource={recipients.filter(row => `${row.email} ${row.name}`.toLowerCase().includes(recipientQuery.trim().toLowerCase()))} scroll={{ x: 700 }} pagination={{ pageSize: 10, showSizeChanger: false }} locale={{ emptyText: '还没有收件邮箱，请先添加' }} columns={[
          { title: '邮箱地址', dataIndex: 'email' },
          { title: '名称或备注', dataIndex: 'name', render: name => name || '—' },
          { title: '启用状态', key: 'enabled', render: (_, row) => <Switch aria-label={`启用${row.email}`} checked={row.enabled} loading={recipientBusy === row.id} disabled={recipientBusy !== null} checkedChildren="启用" unCheckedChildren="暂停" onChange={enabled => void toggleMailbox(row, enabled)} /> },
          { title: '开始接收（北京）', dataIndex: 'started_at', render: timeText },
          { title: '操作', key: 'action', render: (_, row) => <Space><Button size="small" aria-label={`编辑${row.email}`} disabled={recipientBusy !== null} onClick={() => setEditing(row)}>编辑</Button><Button size="small" onClick={() => { setHistoryRecipient(row.email); void loadHistory(1, row.email) }}>查看记录</Button><Button size="small" danger aria-label={`删除${row.email}`} disabled={recipientBusy !== null} onClick={() => removeMailbox(row)}>删除</Button></Space> },
        ]} />
      </section>
      <Modal title={editing ? '编辑收件邮箱' : '新增收件邮箱'} open={editing !== undefined} onCancel={() => { if (!recipientSaving) setEditing(undefined) }} onOk={() => recipientForm.submit()} okText="保存邮箱" cancelText="取消" confirmLoading={recipientSaving} cancelButtonProps={{ disabled: recipientSaving }} destroyOnHidden>
        {recipientError && <Alert type="error" title={recipientError} showIcon />}
        <Form form={recipientForm} layout="vertical" onFinish={saveMailbox} preserve={false}>
          <Form.Item name="email" label="邮箱地址" rules={[{ required: true, type: 'email', transform: value => value?.trim(), message: '请输入有效的邮箱地址' }]}><Input type="email" maxLength={254} placeholder="reader@example.com" /></Form.Item>
          <Form.Item name="name" label="名称或备注"><Input maxLength={100} placeholder="例如：测试组、研发负责人" /></Form.Item>
          <Form.Item name="enabled" label="启用此邮箱" valuePropName="checked"><Switch /></Form.Item>
        </Form>
      </Modal>
      <section className="email-history"><div className="email-history-heading"><h2>发送记录</h2><Space wrap><Select id="email-history-recipient" aria-label="筛选收件邮箱" placeholder="全部收件邮箱" value={historyRecipient} allowClear showSearch optionFilterProp="label" style={{ minWidth: 230 }} options={[...new Set([...recipients.map(row => row.email), ...deliveries.map(row => row.recipient), ...(historyRecipient ? [historyRecipient] : [])])].map(email => ({ value: email, label: email }))} onChange={email => { setHistoryRecipient(email); void loadHistory(1, email ?? '') }} /><Button onClick={() => void loadHistory()} loading={historyLoading}>刷新发送记录</Button></Space></div>
        <Table<Delivery> rowKey="id" dataSource={deliveries} loading={historyLoading} scroll={{ x: 850 }} pagination={{ current: page, total, pageSize: 20, showSizeChanger: false, onChange: next => void loadHistory(next) }} columns={[
          { title: '日报日期', dataIndex: 'business_date' },
          { title: '收件邮箱', dataIndex: 'recipient' },
          { title: '情报数', dataIndex: 'item_count' },
          { title: '发送状态', key: 'status', render: (_, row) => <Space orientation="vertical"><Tag color={row.status === 'sent' ? 'green' : row.status === 'failed' || row.status === 'uncertain' ? 'orange' : 'default'}>{statusLabels[row.status] || row.status}</Tag>{row.error_message && <small>{row.error_message}</small>}{row.next_attempt_at && <small>{row.status === 'pending' && row.attempts === 0 ? '计划发送' : '下次尝试'}：{timeText(row.next_attempt_at)}</small>}</Space> },
          { title: '尝试次数', dataIndex: 'attempts' },
          { title: '发送时间（北京）', dataIndex: 'sent_at', render: timeText },
          { title: '操作', key: 'action', render: (_, row) => ['failed', 'uncertain'].includes(row.status) && <Button size="small" aria-label="重试" disabled={!preferences.enabled || !recipients.some(recipient => recipient.enabled && recipient.email === row.recipient.toLowerCase())} onClick={() => Modal.confirm({ title: '重试这封邮件？', content: row.status === 'uncertain' ? '请先核对收件箱。如果已收到，再次投递会产生重复邮件。' : '将按原报告内容重新投递到此收件邮箱。', okText: '确认重试', cancelText: '取消', onOk: () => retry(row) })}>重试</Button> },
        ]} />
      </section>
    </>}
  </div>
}
