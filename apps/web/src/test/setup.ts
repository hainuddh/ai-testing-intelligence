import '@testing-library/jest-dom/vitest'
import { afterEach } from 'vitest'
import { act, cleanup } from '@testing-library/react'
import { Modal } from 'antd'

afterEach(async () => {
  await act(async () => {
    Modal.destroyAll()
    cleanup()
  })
  localStorage.clear()
})

Object.defineProperty(window, 'matchMedia', {
  writable: true,
  value: (query: string) => ({
    matches: false,
    media: query,
    onchange: null,
    addListener: () => undefined,
    removeListener: () => undefined,
    addEventListener: () => undefined,
    removeEventListener: () => undefined,
    dispatchEvent: () => false,
  }),
})

class ResizeObserverMock {
  observe() {}
  unobserve() {}
  disconnect() {}
}

globalThis.ResizeObserver = ResizeObserverMock
