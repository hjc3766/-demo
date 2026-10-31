"""本地模型服务适配器（专网内，数据不出域）。

设计对应方案 §4.1：生产用 vLLM（Apache-2.0），验证期用 Ollama（MIT）。
两者都提供 OpenAI 兼容的 /v1/chat/completions 接口，这里用标准库 urllib 实现，
不引入额外依赖。

关键约束（方案 §6.6 铁律）：**LLM 只重排与解释，不生成新事实。**
因此本模块提供的能力被严格限制为：
  * 要素抽取（结构化条件）
  * 候选重排打分（必须在给定候选集合内）
任何试图引入候选集合之外资产的输出，在 rank.py 中会被丢弃。
"""
from __future__ import annotations

import json
import urllib.error
import urllib.request
from typing import Any, Optional

from .config import CONFIG


class LLMUnavailable(RuntimeError):
    """模型服务不可用。调用方必须降级到规则基线，不得中断业务。"""


def _post_json(url: str, payload: dict, timeout: int) -> dict:
    data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    req = urllib.request.Request(
        url, data=data,
        headers={"Content-Type": "application/json", "Authorization": "Bearer local"},
    )
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read().decode("utf-8"))


def chat(prompt: str, system: str = "", timeout: Optional[int] = None) -> str:
    """调用本地 OpenAI 兼容端点，返回纯文本。"""
    base = (CONFIG.llm_base_url or "").rstrip("/")
    if not base:
        raise LLMUnavailable("未配置 MVP_LLM_BASE_URL")
    url = base + "/v1/chat/completions"
    messages = []
    if system:
        messages.append({"role": "system", "content": system})
    messages.append({"role": "user", "content": prompt})
    payload = {
        "model": CONFIG.llm_model,
        "messages": messages,
        "temperature": 0,
        "stream": False,
    }
    try:
        data = _post_json(url, payload, timeout or CONFIG.llm_timeout_s)
    except urllib.error.URLError as e:
        raise LLMUnavailable(f"模型服务不可达: {e}") from e
    except urllib.error.HTTPError as e:
        raise LLMUnavailable(f"模型服务返回 {e.code}") from e
    except (TimeoutError, OSError) as e:
        raise LLMUnavailable(f"模型服务连接失败: {e}") from e
    try:
        return data["choices"][0]["message"]["content"]
    except (KeyError, IndexError) as e:
        raise LLMUnavailable("模型返回结构不符合预期") from e


def extract_json(prompt: str, system: str = "", timeout: Optional[int] = None) -> Any:
    """要求模型返回 JSON，解析失败时抛 LLMUnavailable。"""
    raw = chat(prompt, system, timeout)
    txt = raw.strip()
    if txt.startswith("```"):
        txt = txt.split("```")[1] if "```" in txt else txt
        if txt.lower().startswith("json"):
            txt = txt[4:]
    txt = txt.strip()
    try:
        return json.loads(txt)
    except json.JSONDecodeError as e:
        raise LLMUnavailable(f"模型未返回合法 JSON: {e}") from e


def available() -> bool:
    return bool(CONFIG.llm_enabled and CONFIG.llm_base_url)
