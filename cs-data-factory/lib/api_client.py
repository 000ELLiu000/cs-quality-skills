#!/usr/bin/env python3
"""
lib/api_client.py
-----------------
兼容 Chat Completions 的模型 API 调用封装。
供 scripts/run_batch_api.py（生产侧）和 judge/scripts/run_judge.py（质检侧）共用。

端点：环境变量 CS_FACTORY_API_ENDPOINT，必须是完整的 Chat Completions URL
认证：Bearer <AppId>
无第三方依赖，只用标准库 urllib。

【AppId 安全说明】
  AppId 支持两种传入方式（优先级从高到低）：
    1. 命令行参数 --appid（仅在当前进程中有效，不落地磁盘）
    2. 环境变量 CS_FACTORY_APPID（推荐用于本地开发 / CI/CD 环境）
  不建议将 AppId 硬编码在脚本中或提交到代码仓库。
  设置方式：
    Windows PowerShell: $env:CS_FACTORY_APPID = "your-appid"
    macOS/Linux:        export CS_FACTORY_APPID="your-appid"
    .env 文件（本地）:  CS_FACTORY_APPID=your-appid（需 python-dotenv 加载）
"""

import json
import os
import urllib.request
import urllib.error


def resolve_appid(appid_arg: str | None = None) -> str:
    """
    解析 AppId，按优先级：
      1. 函数参数 appid_arg（命令行 --appid）
      2. 环境变量 CS_FACTORY_APPID
    若两者都为空，抛出 ValueError。
    """
    if appid_arg:
        return appid_arg
    env_appid = os.environ.get("CS_FACTORY_APPID", "").strip()
    if env_appid:
        return env_appid
    raise ValueError(
        "未找到 AppId。请通过以下方式之一提供：\n"
        "  1. 命令行参数：--appid <your-appid>\n"
        "  2. 环境变量：export CS_FACTORY_APPID=<your-appid>"
    )

def resolve_endpoint() -> str:
    endpoint = os.environ.get("CS_FACTORY_API_ENDPOINT", "").strip()
    if not endpoint:
        raise ValueError("请设置 CS_FACTORY_API_ENDPOINT 为完整的 Chat Completions URL")
    from urllib.parse import urlsplit
    parsed = urlsplit(endpoint)
    if parsed.scheme not in ("https", "http") or not parsed.hostname or parsed.username or parsed.password:
        raise ValueError("CS_FACTORY_API_ENDPOINT 必须是无内嵌凭据的 HTTP(S) URL")
    return endpoint


def call_api(
    prompt: str,
    appid: str,
    model: str,
    temperature: float = 0.7,
    max_tokens: int = 4096,
    thinking: bool = False,
    timeout: int = 120,
) -> str:
    """
    调用 兼容 Chat Completions 的模型 API，返回模型输出文本。

    参数：
        prompt      : 完整的用户 prompt（单轮对话）
        appid       : 模型 API Key，作为 Bearer token 使用
        model       : 模型名称，如 "your-model"
        temperature : 温度，生产侧建议 0.7，质检侧建议 0.0
        max_tokens  : 最大输出 token 数
        thinking    : 是否开启 thinking 模式（chat_template_kwargs.thinking）
        timeout     : 单次请求超时秒数，超时后自动重试 1 次

    返回：
        模型输出的原始文本（stripped）

    异常：
        RuntimeError：HTTP 错误或连接失败（包含错误详情）
    """
    payload: dict = {
        "model": model,
        "messages": [{"role": "user", "content": prompt}],
        "temperature": temperature,
        "max_tokens": max_tokens,
        "stream": False,
    }
    if thinking:
        payload["chat_template_kwargs"] = {"thinking": True}

    headers = {
        "Content-Type": "application/json",
        "Authorization": f"Bearer {appid}",
    }
    data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    req = urllib.request.Request(
        resolve_endpoint(), data=data, headers=headers, method="POST"
    )

    # 最多重试 1 次（应对偶发超时）
    last_error = None
    for attempt in range(2):
        try:
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                body = json.loads(resp.read().decode("utf-8"))
            return body["choices"][0]["message"]["content"].strip()
        except urllib.error.HTTPError as e:
            detail = e.read().decode("utf-8", errors="replace").replace(appid, "[REDACTED]")[:400]
            last_error = RuntimeError(f"HTTP {e.code}: {detail}")
            break  # HTTP 错误不重试（如 401/403/429），直接抛出
        except TimeoutError as e:
            last_error = RuntimeError(f"请求超时（{timeout}s）：{e}")
            if attempt == 0:
                continue  # 超时重试一次
        except Exception as e:
            last_error = RuntimeError(f"请求失败：{str(e).replace(appid, '[REDACTED]')}")
            break

    raise last_error
