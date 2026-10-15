"""为同步 SDK 提供可取消、包含读取响应体在内的总时限。

后台线程内运行异步 I/O；取消关闭在途连接，不在 Qt 主线程关闭套接字。
不改动 SDK 的认证、序列化或应用层重试逻辑，也不记录请求内容。
"""
from __future__ import annotations

import asyncio
import threading
from time import monotonic

import httpx

TEST_TIMEOUT = 30
MODELS_TIMEOUT = 15
GENERATE_TIMEOUT = 240


class Cancelled(Exception):
    pass


class RequestControl:
    def __init__(self):
        self.begin(GENERATE_TIMEOUT)

    def begin(self, seconds, cancelled=None):
        self.cancelled = cancelled if cancelled is not None else threading.Event()
        self.deadline = monotonic() + seconds

    def cancel(self):
        self.cancelled.set()

    def check(self, request=None):
        if self.cancelled.is_set():
            raise Cancelled()
        if monotonic() >= self.deadline:
            raise httpx.ReadTimeout("请求已超过总等待时限", request=request)


class CancellableTransport(httpx.BaseTransport):
    def __init__(self, control: RequestControl, *, async_transport=None):
        self.control = control
        self.async_transport = async_transport

    def handle_request(self, request):
        async def exchange():
            # AsyncClient 自行读取环境代理；外层同步 Client 禁用代理以避免绕过此传输。
            async with httpx.AsyncClient(transport=self.async_transport,
                                        timeout=httpx.Timeout(120, connect=10)) as client:
                async with client.stream(request.method, request.url, headers=request.headers,
                                         content=request.content) as response:
                    body = await response.aread()
                    # aread 已解压，不能把压缩头复制给外层再解压一次。
                    headers = [(key, value) for key, value in response.headers.multi_items()
                               if key.lower() not in {"content-encoding", "content-length", "transfer-encoding"}]
                    return httpx.Response(response.status_code, headers=headers,
                                          content=body, request=request)

        async def bounded():
            self.control.check(request)
            task = asyncio.create_task(exchange())
            try:
                while True:
                    self.control.check(request)
                    done, _ = await asyncio.wait({task}, timeout=min(.05, max(.001, self.control.deadline - monotonic())))
                    if done:
                        self.control.check(request)
                        return task.result()
            finally:
                if not task.done():
                    task.cancel()
                await asyncio.gather(task, return_exceptions=True)

        return asyncio.run(bounded())
