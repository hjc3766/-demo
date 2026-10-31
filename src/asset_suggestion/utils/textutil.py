"""通用小工具：文本相似度。

MVP 用**字符 bigram Jaccard** 近似 pgvector 的向量近邻（方案 §4.1 / §6.5）。
生产环境应把这一层换成 pgvector 嵌入检索——接口保持可替换即可。

注意：中文没有天然词边界，字符 bigram 是一种低成本近似，比整串包含判定更稳，
但语义召回质量仍低于真向量模型。这是已知边界，不是缺陷。
"""
from __future__ import annotations

from typing import Set


def _bigrams(s: str) -> Set[str]:
    s = (s or "").strip()
    if len(s) < 2:
        return {s} if s else set()
    return {s[i:i + 2] for i in range(len(s) - 1)}


def text_similarity(a: str, b: str) -> float:
    """字面相似度（字符 bigram Jaccard）。作为 pgvector 的 MVP 近似。"""
    ba, bb = _bigrams(a), _bigrams(b)
    if not ba or not bb:
        return 0.0
    inter = len(ba & bb)
    return inter / len(ba | bb)
