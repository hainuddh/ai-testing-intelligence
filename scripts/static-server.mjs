import { createServer, request as httpRequest } from 'node:http'
import { readFile, stat } from 'node:fs/promises'
import { extname, join, normalize, resolve, sep } from 'node:path'

const root = resolve(process.argv[2] || '.')
const port = Number(process.argv[3] || process.env.DEPLOY_RUN_PORT || 5000)
const apiPort = Number(process.env.API_PORT || 8000)

const types = {
  '.html': 'text/html; charset=utf-8',
  '.js': 'text/javascript; charset=utf-8',
  '.mjs': 'text/javascript; charset=utf-8',
  '.css': 'text/css; charset=utf-8',
  '.json': 'application/json; charset=utf-8',
  '.map': 'application/json',
  '.svg': 'image/svg+xml',
  '.png': 'image/png',
  '.jpg': 'image/jpeg',
  '.jpeg': 'image/jpeg',
  '.gif': 'image/gif',
  '.webp': 'image/webp',
  '.ico': 'image/x-icon',
  '.woff': 'font/woff',
  '.woff2': 'font/woff2',
  '.txt': 'text/plain; charset=utf-8',
  '.webmanifest': 'application/manifest+json',
}

function proxyApi(req, res) {
  const upstream = httpRequest(
    {
      host: '127.0.0.1',
      port: apiPort,
      path: req.url,
      method: req.method,
      headers: { ...req.headers, host: `127.0.0.1:${apiPort}` },
    },
    (up) => {
      const headers = { ...up.headers }
      delete headers['transfer-encoding']
      delete headers.connection
      delete headers['keep-alive']
      res.writeHead(up.statusCode || 502, headers)
      up.pipe(res)
    },
  )
  upstream.on('error', () => {
    if (!res.headersSent) {
      res.writeHead(503, { 'Content-Type': 'application/json; charset=utf-8' })
    }
    res.end(JSON.stringify({ detail: '后端服务未就绪，请稍后重试' }))
  })
  req.pipe(upstream)
}

createServer(async (req, res) => {
  const pathname = new URL(req.url, 'http://localhost').pathname
  if (pathname === '/api' || pathname.startsWith('/api/')) {
    proxyApi(req, res)
    return
  }
  try {
    let path = decodeURIComponent(pathname)
    if (path.endsWith('/')) path += 'index.html'
    const safe = normalize(path).replace(/^([.][.][/\\])+/, '')
    const filePath = join(root, safe)
    if (filePath !== root && !filePath.startsWith(root + sep)) {
      res.writeHead(403).end()
      return
    }
    let target = filePath
    let body
    try {
      const info = await stat(target)
      if (info.isDirectory()) target = join(target, 'index.html')
      body = await readFile(target)
    } catch {
      if (extname(safe)) {
        res.writeHead(404, { 'Content-Type': 'text/plain; charset=utf-8' }).end('Not Found')
        return
      }
      target = join(root, 'index.html')
      body = await readFile(target)
    }
    res.writeHead(200, {
      'Content-Type': types[extname(target)] || 'application/octet-stream',
      'Cache-Control': extname(target) === '.html' ? 'no-cache' : 'public, max-age=3600',
    }).end(body)
  } catch {
    res.writeHead(500).end()
  }
}).listen(port, '0.0.0.0')
