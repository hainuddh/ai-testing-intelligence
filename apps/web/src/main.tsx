import { StrictMode } from 'react'
import { createRoot } from 'react-dom/client'
import { ConfigProvider, theme } from 'antd'
import App from './App'
import './styles.css'

createRoot(document.getElementById('root')!).render(
  <StrictMode>
    <ConfigProvider
      theme={{
        algorithm: theme.darkAlgorithm,
        token: {
          colorPrimary: '#8effb8',
          colorInfo: '#8effb8',
          colorBgBase: '#07110f',
          colorTextBase: '#effff4',
          colorBorder: '#24423a',
          borderRadius: 6,
          fontFamily: 'Inter, "Segoe UI", sans-serif',
        },
        components: {
          Button: { fontWeight: 700 },
          Input: { activeShadow: '0 0 0 2px rgba(142, 255, 184, 0.15)' },
        },
      }}
    >
      <App />
    </ConfigProvider>
  </StrictMode>,
)
