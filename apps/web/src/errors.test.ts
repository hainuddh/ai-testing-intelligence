import { describe, expect, it } from 'vitest'
import { userFacingError } from './errors'

describe('Chinese error messages', () => {
  it.each([
    ['Source name already exists', '信源名称已存在，请修改名称后重试。'],
    ['Endpoint not found', '采集端点不存在，请刷新后重试。'],
    ['Content with this URL or title already exists', '相同链接或标题的内容已存在，请勿重复录入。'],
    ['Cannot deactivate your current account', '不能停用当前登录账号。'],
    ['Cannot delete the last active admin', '不能删除最后一个启用的管理员账号。'],
    ['Insufficient permissions', '权限不足，请联系管理员确认账号权限。'],
    ['Failed to fetch', '网络连接异常，请检查网络后重试。'],
    ['Internal Server Error', '操作失败，请稍后重试。'],
    ['用户名 viewer1 已存在，请更换用户名', '用户名 viewer1 已存在，请更换用户名'],
    [[{ loc: ['body', 'url'], msg: 'Input should be a valid URL' }], '提交的数据不符合要求，请检查填写内容后重试。'],
  ])('translates %j', (detail, expected) => {
    expect(userFacingError(detail)).toBe(expected)
  })
})
