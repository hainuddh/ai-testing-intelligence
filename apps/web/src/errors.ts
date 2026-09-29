const messages = new Map<string, string>([
  ['Source name already exists', '信源名称已存在，请修改名称后重试。'],
  ['Source not found', '信源不存在，请刷新后重试。'],
  ['Endpoint not found', '采集端点不存在，请刷新后重试。'],
  ['Required source fields cannot be null', '信源必填项不能为空，请检查后重试。'],
  ['WeChat and Weibo sources only support RSS/Atom endpoints', '微信和微博信源仅支持 RSS / Atom 采集端点。'],
  ['Manual submission is only supported for WeChat and Weibo sources', '仅微信和微博信源支持人工录入内容。'],
  ['Content with this URL or title already exists', '相同链接或标题的内容已存在，请勿重复录入。'],
  ['Content item not found', '内容不存在，请刷新后重试。'],
  ['One or more content items were not found', '部分内容已不存在，请刷新后重新选择。'],
  ['One or more content items do not exist', '部分内容已不存在，请刷新后重新选择。'],
  ['One or more content items are unavailable for export', '部分内容无法导出，请刷新后重新选择。'],
  ['start_at must be before or equal to end_at', '开始日期不能晚于结束日期。'],
  ['User not found', '用户不存在，请刷新后重试。'],
  ['Username already exists', '用户名已存在，请更换用户名。'],
  ['Username, role, and active status cannot be null', '用户名、角色和账号状态不能为空。'],
  ['Cannot deactivate your current account', '不能停用当前登录账号。'],
  ['Cannot delete your current account', '不能删除当前登录账号。'],
  ['Cannot remove the last active admin', '必须保留至少一个启用的管理员账号。'],
  ['Cannot delete the last active admin', '不能删除最后一个启用的管理员账号。'],
  ['Cannot delete a user that owns sources', '该用户仍拥有信源，暂时无法删除。'],
  ['Insufficient permissions', '权限不足，请联系管理员确认账号权限。'],
  ['Invalid or missing authentication credentials', '登录状态已失效，请重新登录。'],
  ['Failed to fetch', '网络连接异常，请检查网络后重试。'],
  ['NetworkError when attempting to fetch resource.', '网络连接异常，请检查网络后重试。'],
  ['Load failed', '网络连接异常，请检查网络后重试。'],
])

export function userFacingError(detail: unknown): string {
  if (Array.isArray(detail)) return '提交的数据不符合要求，请检查填写内容后重试。'
  if (typeof detail === 'string') {
    const translated = messages.get(detail)
    if (translated) return translated
    if (/[\u4e00-\u9fff]/.test(detail)) return detail
  }
  return '操作失败，请稍后重试。'
}
