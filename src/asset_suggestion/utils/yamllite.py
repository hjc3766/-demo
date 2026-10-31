"""通用小工具：YAML 子集解析（零依赖）。

**为什么不用 PyYAML**：本项目坚持零第三方运行时依赖（见 requirements.txt）。
`config/default.yaml` 是**非机密默认配置**，只需要一个很小的 YAML 子集：

    注释（#）、缩进嵌套映射、块状列表（- item）、流式列表（[a, b]）、
    标量（int / float / bool / null / 引号字符串 / 裸字符串）

超出这个子集的写法（锚点、多文档、复杂键、块标量）**不支持**，
遇到时抛 `YamlError` 而不是悄悄解析错——配置解析错比配置缺失更危险。

本模块**不依赖项目内任何其他模块**。
"""
from __future__ import annotations

import re
from typing import Any, List, Tuple


class YamlError(ValueError):
    """配置文件无法按子集规则解析。"""


_INT_RE = re.compile(r"^[+-]?\d+$")
_FLOAT_RE = re.compile(r"^[+-]?(\d+\.\d*|\.\d+|\d+)([eE][+-]?\d+)?$")


def _strip_comment(line: str) -> str:
    """去掉行内注释，但不动引号里的 #。"""
    out, quote = [], None
    for i, ch in enumerate(line):
        if quote:
            out.append(ch)
            if ch == quote:
                quote = None
            continue
        if ch in "\"'":
            quote = ch
            out.append(ch)
            continue
        if ch == "#" and (i == 0 or line[i - 1] in " \t"):
            break
        out.append(ch)
    return "".join(out).rstrip()


def _split_flow(inner: str) -> List[str]:
    """切分流式列表内容，忽略括号内的逗号。"""
    parts, buf, quote, depth = [], [], None, 0
    for ch in inner:
        if quote:
            buf.append(ch)
            if ch == quote:
                quote = None
            continue
        if ch in "\"'":
            quote = ch
            buf.append(ch)
            continue
        if ch in "[{":
            depth += 1
        elif ch in "]}":
            depth -= 1
        if ch == "," and depth == 0:
            parts.append("".join(buf).strip())
            buf = []
            continue
        buf.append(ch)
    tail = "".join(buf).strip()
    if tail:
        parts.append(tail)
    return parts


def _scalar(raw: str) -> Any:
    s = raw.strip()
    if not s:
        return None
    if len(s) >= 2 and s[0] == s[-1] and s[0] in "\"'":
        return s[1:-1]
    low = s.lower()
    if low in ("true", "yes", "on"):
        return True
    if low in ("false", "no", "off"):
        return False
    if low in ("null", "~"):
        return None
    if s.startswith("[") and s.endswith("]"):
        return [_scalar(x) for x in _split_flow(s[1:-1])]
    if _INT_RE.match(s):
        return int(s)
    if _FLOAT_RE.match(s):
        return float(s)
    return s


def _tokenize(text: str) -> List[Tuple[int, str]]:
    lines = []
    for no, raw in enumerate(text.splitlines(), 1):
        if "\t" in raw[: len(raw) - len(raw.lstrip())]:
            raise YamlError(f"第 {no} 行缩进含制表符，YAML 子集只允许空格缩进")
        stripped = _strip_comment(raw)
        if not stripped.strip():
            continue
        indent = len(stripped) - len(stripped.lstrip(" "))
        lines.append((indent, stripped.strip(), no))
    return lines


def _parse_block(lines, i, indent):
    """解析一个缩进块，返回 (值, 下一行索引)。"""
    if i >= len(lines):
        return None, i
    if lines[i][1].startswith("- "):
        return _parse_list(lines, i, indent)
    if lines[i][1] == "-":
        return _parse_list(lines, i, indent)
    return _parse_map(lines, i, indent)


def _parse_map(lines, i, indent):
    out = {}
    while i < len(lines):
        ind, content, no = lines[i]
        if ind < indent:
            break
        if ind > indent:
            raise YamlError(f"第 {no} 行缩进异常：期望 {indent}，实际 {ind}")
        if content.startswith("- "):
            break
        if ":" not in content:
            raise YamlError(f"第 {no} 行不是合法的映射项：{content!r}")
        key, _, rest = content.partition(":")
        key = key.strip()
        rest = rest.strip()
        if not key:
            raise YamlError(f"第 {no} 行缺少键名")
        if rest:
            out[key] = _scalar(rest)
            i += 1
            continue
        # 值在下一层缩进里
        if i + 1 < len(lines) and lines[i + 1][0] > ind:
            value, i = _parse_block(lines, i + 1, lines[i + 1][0])
            out[key] = value
        else:
            out[key] = None
            i += 1
    return out, i


def _parse_list(lines, i, indent):
    out = []
    while i < len(lines):
        ind, content, no = lines[i]
        if ind < indent:
            break
        if ind > indent:
            raise YamlError(f"第 {no} 行缩进异常：期望 {indent}，实际 {ind}")
        if not (content == "-" or content.startswith("- ")):
            break
        item = content[1:].strip()
        if item:
            out.append(_scalar(item))
            i += 1
            continue
        if i + 1 < len(lines) and lines[i + 1][0] > ind:
            value, i = _parse_block(lines, i + 1, lines[i + 1][0])
            out.append(value)
        else:
            out.append(None)
            i += 1
    return out, i


def load_yaml(text: str) -> Any:
    """解析 YAML 子集，返回 dict / list / 标量。"""
    lines = _tokenize(text)
    if not lines:
        return {}
    value, i = _parse_block(lines, 0, lines[0][0])
    if i != len(lines):
        raise YamlError(f"第 {lines[i][2]} 行之后的内容无法解析")
    return value


def load_yaml_file(path: str) -> Any:
    with open(path, "r", encoding="utf-8") as f:
        return load_yaml(f.read())
