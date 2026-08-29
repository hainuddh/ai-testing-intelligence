import { render, screen, waitFor } from '@testing-library/react'
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

describe('technology intelligence workflow', () => {
  beforeEach(() => {
    vi.restoreAllMocks()
  })

  it('submits login credentials and stores the returned token', async () => {
    const user = userEvent.setup()
    const fetchMock = vi.spyOn(globalThis, 'fetch').mockResolvedValueOnce(
      new Response(JSON.stringify({ access_token: 'radar-token' }), {
        status: 200,
        headers: { 'Content-Type': 'application/json' },
      }),
    ).mockResolvedValueOnce(
      new Response(JSON.stringify({ items: [], total: 0 }), {
        status: 200,
        headers: { 'Content-Type': 'application/json' },
      }),
    )

    render(<App />)
    await user.type(screen.getByLabelText('用户名'), 'analyst')
    await user.type(screen.getByLabelText('密码'), 'secret-pass')
    await user.click(screen.getByRole('button', { name: '进入雷达' }))

    await waitFor(() => expect(localStorage.getItem('access_token')).toBe('radar-token'))
    expect(fetchMock).toHaveBeenNthCalledWith(1, '/api/v1/auth/login', expect.objectContaining({
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ username: 'analyst', password: 'secret-pass' }),
    }))
  })

  it('loads and displays sources when already authenticated', async () => {
    localStorage.setItem('access_token', 'existing-token')
    const fetchMock = vi.spyOn(globalThis, 'fetch').mockResolvedValueOnce(
      new Response(JSON.stringify({ items: [source], total: 1 }), {
        status: 200,
        headers: { 'Content-Type': 'application/json' },
      }),
    )

    render(<App />)

    expect(await screen.findByText('OpenAI Research')).toBeInTheDocument()
    expect(screen.getByText('Frontier AI research and releases')).toBeInTheDocument()
    expect(fetchMock).toHaveBeenCalledWith('/api/v1/sources', {
      headers: { Authorization: 'Bearer existing-token' },
    })
  })

  it('posts a new source from the creation form', async () => {
    localStorage.setItem('access_token', 'existing-token')
    const user = userEvent.setup()
    const fetchMock = vi.spyOn(globalThis, 'fetch')
      .mockResolvedValueOnce(new Response(JSON.stringify({ items: [], total: 0 }), { status: 200 }))
      .mockResolvedValueOnce(new Response(JSON.stringify(source), { status: 201 }))

    render(<App />)
    await screen.findByText('暂无信源，建立第一个监听点。')
    await user.click(screen.getByRole('button', { name: '新增信源' }))
    await user.type(screen.getByLabelText('信源名称'), 'OpenAI Research')
    await user.click(screen.getByLabelText('信源类型'))
    await user.click(await screen.findByText('网站'))
    await user.type(screen.getByLabelText('主页地址'), 'https://openai.com/research')
    await user.type(screen.getByLabelText('描述'), 'Frontier AI research and releases')
    await user.type(screen.getByLabelText('语言'), 'en')
    await user.click(screen.getByLabelText('可信等级'))
    await user.click(await screen.findByText('最高可信'))
    await user.type(screen.getByLabelText('关注主题'), 'foundation-models')
    await user.click(screen.getByRole('button', { name: '建立监听' }))

    await waitFor(() => expect(fetchMock).toHaveBeenCalledTimes(2))
    expect(fetchMock).toHaveBeenNthCalledWith(2, '/api/v1/sources', {
      method: 'POST',
      headers: {
        Authorization: 'Bearer existing-token',
        'Content-Type': 'application/json',
      },
      body: JSON.stringify({
        name: 'OpenAI Research',
        source_type: 'website',
        homepage_url: 'https://openai.com/research',
        description: 'Frontier AI research and releases',
        languages: ['en'],
        trust_level: 5,
        topics: ['foundation-models'],
      }),
    })
    expect(await screen.findByText('OpenAI Research')).toBeInTheDocument()
  })
})
