import { useEffect, useRef, useState } from 'react'
import { DeleteOutlined, EditOutlined, MoreOutlined, PlusOutlined, UndoOutlined } from '@ant-design/icons'
import { Alert, Button, Dropdown, Input } from 'antd'

type TopicDraft = { id: number; original: string | null; value: string; removed: boolean }

export function useTopicDraft(topics: readonly string[], open: boolean, saving: boolean) {
  const [draft, setDraft] = useState<TopicDraft[]>([])
  const [input, setInput] = useState('')
  const [editingId, setEditingId] = useState<number | null>(null)
  const [editValue, setEditValue] = useState('')
  const [issue, setIssue] = useState('')
  const nextId = useRef(0)
  useEffect(() => {
    if (open) {
      setDraft(topics.map((topic, id) => ({ id, original: topic, value: topic, removed: false })))
      nextId.current = topics.length
      setInput('')
      setEditingId(null)
      setEditValue('')
      setIssue('')
    }
  }, [open, topics])

  const active = draft.filter((topic) => !topic.removed)
  const added = active.filter((topic) => topic.original === null).length
  const modified = active.filter((topic) => topic.original !== null && topic.original !== topic.value).length
  const removed = draft.filter((topic) => topic.removed && topic.original !== null).length
  const hasChanges = added + modified + removed > 0

  const updateTopic = (id: number, change: Partial<Pick<TopicDraft, 'value' | 'removed'>>) => {
    setDraft((rows) => rows.map((row) => row.id === id ? { ...row, ...change } : row))
    setIssue('')
  }
  const addTopic = () => {
    if (saving || editingId !== null) return
    const value = input.trim()
    if (!value) return
    const existing = draft.find((topic) => topic.value === value)
    if (existing && !existing.removed) {
      setIssue('该主题已存在，请使用其他主题。')
      return
    }
    if (existing) updateTopic(existing.id, { removed: false })
    else {
      const id = nextId.current++
      setDraft((rows) => [...rows, { id, original: null, value, removed: false }])
    }
    setInput('')
    setIssue('')
  }
  const confirmEdit = () => {
    if (saving || editingId === null) return
    const value = editValue.trim()
    if (!value) {
      setIssue('主题不能为空，请填写主题后确认修改。')
      return
    }
    if (draft.some((topic) => topic.id !== editingId && topic.value === value)) {
      setIssue('该主题已存在，请使用其他主题。')
      return
    }
    updateTopic(editingId, { value })
    setEditingId(null)
  }

  return {
    draft, input, editingId, editValue, issue, saving, added, modified, removed, hasChanges,
    values: active.map((topic) => topic.value), blocked: editingId !== null || Boolean(input.trim()),
    setInput, setEditValue, setIssue, updateTopic, addTopic, confirmEdit,
    startEdit: (topic: TopicDraft) => { setEditingId(topic.id); setEditValue(topic.value); setIssue('') },
    cancelEdit: () => { setEditingId(null); setIssue('') },
  }
}

type TopicEditorState = ReturnType<typeof useTopicDraft>

export function TopicChangeSummary({ editor, required = false }: { editor: TopicEditorState; required?: boolean }) {
  return <>
    <div role="status" aria-live="polite" className="topics-changes">新增 {editor.added} · 修改 {editor.modified} · 移除 {editor.removed}</div>
    <p>{editor.editingId !== null ? '请先确认修改或取消编辑。' : editor.input.trim() ? '请先点击添加主题，或清空输入后保存。' : required && !editor.values.length ? '请至少添加一个关注主题。' : editor.hasChanges ? '保存后生效，可在保存前撤销移除。' : '关注主题尚无更改。'}</p>
  </>
}

export function TopicEditor({ editor, inputId, emptyText, clearedText }: {
  editor: TopicEditorState; inputId: string; emptyText: string; clearedText?: string
}) {
  const { draft, input, editingId, editValue, issue, saving, values, setInput, setEditValue, setIssue, updateTopic, addTopic, confirmEdit, startEdit, cancelEdit } = editor
  return <>
    <div className="topics-add">
      <label htmlFor={inputId}>新增关注主题</label>
      <div className="topics-add-controls">
        <Input id={inputId} placeholder="如 testing、AI testing、rag" value={input} disabled={saving || editingId !== null}
          onChange={(event) => { setInput(event.target.value); setIssue('') }}
          onPressEnter={(event) => { if (!event.nativeEvent.isComposing) { event.preventDefault(); addTopic() } }} />
        <Button icon={<PlusOutlined />} disabled={saving || editingId !== null || !input.trim()} onClick={addTopic}>添加主题</Button>
      </div>
      <p>每次添加一个主题，支持空格；也可以按回车确认添加。</p>
    </div>
    {issue && <Alert type="error" showIcon title={issue} className="topics-error" />}
    <div className="topics-heading"><strong>关注方向</strong><span>{values.length} 个主题</span></div>
    {!draft.length && <p className="topics-empty">{emptyText}</p>}
    <ul className="topics-list" aria-label="关注主题列表">
      {draft.map((topic) => <li key={topic.id} className={`topic-row${topic.removed ? ' is-removed' : ''}`}>
        {editingId === topic.id ? <div className="topic-edit">
          <Input aria-label="编辑关注主题" autoFocus value={editValue} disabled={saving}
            onChange={(event) => { setEditValue(event.target.value); setIssue('') }}
            onPressEnter={(event) => { if (!event.nativeEvent.isComposing) { event.preventDefault(); confirmEdit() } }} />
          <div className="topic-edit-actions">
            <Button disabled={saving} onClick={cancelEdit}>取消编辑</Button>
            <Button type="primary" disabled={saving} onClick={confirmEdit}>确认修改</Button>
          </div>
        </div> : <>
          <div className="topic-label"><strong>{topic.value}</strong>
            <span>{topic.removed ? '待移除' : topic.original === null ? '新增待保存' : topic.original !== topic.value ? '修改待保存' : '已保存'}</span>
          </div>
          {topic.removed ? <Button icon={<UndoOutlined />} disabled={saving || editingId !== null}
            aria-label={`撤销移除 ${topic.value}`} onClick={() => updateTopic(topic.id, { removed: false })}>撤销</Button>
            : <div className="topic-actions">
              <Button type="text" icon={<EditOutlined />} aria-label={`编辑主题 ${topic.value}`} disabled={saving || editingId !== null} onClick={() => startEdit(topic)}>编辑</Button>
              <Dropdown trigger={['click']} disabled={saving || editingId !== null} destroyOnHidden
                menu={{ items: [{ key: 'remove', label: '移除主题', icon: <DeleteOutlined />, danger: true }], onClick: () => updateTopic(topic.id, { removed: true }) }}>
                <Button type="text" icon={<MoreOutlined />} aria-label={`更多操作 ${topic.value}`} disabled={saving || editingId !== null} />
              </Dropdown>
            </div>}
        </>}
      </li>)}
    </ul>
    {draft.length > 0 && !values.length && clearedText && <p className="topics-empty">{clearedText}</p>}
  </>
}
