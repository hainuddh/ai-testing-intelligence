#!/usr/bin/env python3.11
"""AI Testing Intelligence HTTPS reverse proxy."""
import ssl
import asyncio
import os
import time
import logging
import socket

# ============ 配置 ============
BACKEND_HOST = os.environ.get("BACKEND_HOST", "127.0.0.1")
BACKEND_PORT = int(os.environ.get("BACKEND_PORT", "8080"))
LISTEN_PORT = int(os.environ.get("LISTEN_PORT", "443"))
CERT_FILE = os.environ.get("CERT_FILE", "/etc/letsencrypt/live/api.ddhlf.xyz/fullchain.pem")
KEY_FILE = os.environ.get("KEY_FILE", "/etc/letsencrypt/live/api.ddhlf.xyz/privkey.pem")

# ============ 日志 ============
LOG_FILE = os.environ.get(
    "LOG_FILE", "/home/admin/ai-testing-intelligence/deploy/https_proxy.log"
)
logging.basicConfig(
    format="[%(asctime)s] %(message)s",
    datefmt="%H:%M:%S",
    level=logging.INFO,
    handlers=[
        logging.FileHandler(LOG_FILE, mode="a"),
        logging.StreamHandler(),
    ],
)
log = logging.getLogger(__name__)


async def handle_client(reader: asyncio.StreamReader, writer: asyncio.StreamWriter):
    """处理单个客户端连接"""
    addr = writer.get_extra_info("peername")
    log.info(f"[新连接] {addr}")

    try:
        # 读取请求（带超时）
        request = await asyncio.wait_for(reader.read(65536), timeout=10.0)
        if not request:
            log.info(f"[断开] {addr} - 无请求数据")
            return

        # 连接后端
        backend_reader, backend_writer = await asyncio.wait_for(
            asyncio.open_connection(BACKEND_HOST, BACKEND_PORT), timeout=3.0
        )

        # 转发请求
        backend_writer.write(request)
        await backend_writer.drain()

        # 接收响应并回传客户端
        while True:
            try:
                response = await asyncio.wait_for(backend_reader.read(65536), timeout=30.0)
                if not response:
                    break
                writer.write(response)
                await writer.drain()
            except asyncio.TimeoutError:
                break

        backend_writer.close()
        try:
            await backend_writer.wait_closed()
        except Exception:
            pass

        log.info(f"[完成] {addr}")

    except asyncio.TimeoutError:
        log.info(f"[超时] {addr}")
    except Exception as e:
        log.info(f"[错误] {addr}: {e}")
    finally:
        try:
            writer.close()
            await writer.wait_closed()
        except Exception:
            pass


async def health_check():
    """后台健康检查"""
    while True:
        await asyncio.sleep(10)
        try:
            r, w = await asyncio.wait_for(
                asyncio.open_connection(BACKEND_HOST, BACKEND_PORT), timeout=2.0
            )
            w.close()
            await w.wait_closed()
            log.info("[健康] 后端正常")
        except Exception:
            log.warning(f"[告警] 后端 {BACKEND_HOST}:{BACKEND_PORT} 不可达")


async def main():
    ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    ctx.minimum_version = ssl.TLSVersion.TLSv1_2
    ctx.set_ciphers("DEFAULT:@SECLEVEL=1")
    ctx.load_cert_chain(CERT_FILE, KEY_FILE)
    log.info("SSL 证书加载成功")

    # 创建带 SO_REUSEADDR 的 socket
    srv_sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    srv_sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    srv_sock.bind(("0.0.0.0", LISTEN_PORT))
    srv_sock.listen(128)

    server = await asyncio.start_server(
        handle_client,
        sock=srv_sock,
        ssl=ctx,
        reuse_address=True,
    )
    log.info(f"监听 0.0.0.0:{LISTEN_PORT} -> {BACKEND_HOST}:{BACKEND_PORT}")

    # 启动健康检查
    asyncio.create_task(health_check())

    async with server:
        await server.serve_forever()


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        log.info("用户中断")
