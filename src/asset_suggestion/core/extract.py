"""阶段一：归一化 —— 把一句人话变成结构化要素。

对应方案 §6.3。**本阶段只做要素抽取，不猜资产。**

要素字段
    regions     命中的行政区划（编码列表）
    orientation 方位词
    floors      楼层
    buildings   楼栋/单元
    appearance  外观特征
    references  参照物 → 资产类别
    landmark    参照物所属的"地标名词"（如"大厅"）

Extractor 协议可替换：RuleExtractor（默认，离线可跑）与 LLMExtractor（接本地模型）。
LLM 只做**补充**，规则命中优先——避免模型幻觉引入不存在的要素。
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field, asdict
from typing import Dict, List, Optional, Protocol

from . import llm
from .districts import CATEGORY_LEXICON

# ---------------------------------------------------------------- 词典

ORIENTATION_LEXICON = [
    "东侧", "东边", "东面", "西侧", "西边", "西面", "南侧", "南边", "南面",
    "北侧", "北边", "北面", "背面", "后面", "前面", "门口", "靠门口", "里面",
    "外面", "中间", "旁边", "左侧", "右边", "上侧", "下侧", "尽头", "转角",
]

APPEARANCE_LEXICON = [
    "划痕", "掉漆", "生锈", "锈蚀", "破损", "裂了", "裂开", "旧了", "旧的",
    "新的", "不锈钢", "铁的", "塑料", "红色", "绿色", "蓝色", "黄色", "白色",
    "黑色", "贴纸", "标签", "歪了", "倒了", "缺角", "松动", "玻璃", "破损",
    "漏水", "不亮", "不转", "异响", "堵塞", "发黑", "积灰",
]

BUILDING_RE = re.compile(r"([0-9一二三四五六七八九十]+)\s*(号楼|栋|单元|座|门)")
FLOOR_RE = re.compile(r"(地下|负?[0-9一二三四五六七八九十]+)\s*[层楼]")
FLOOR_WORDS = {
    "一层": 1, "二楼": 2, "三楼": 3, "四楼": 4, "五楼": 5,
    "一层": 1, "负一层": -1, "地下": -1, "地下一层": -1,
}


@dataclass
class Elements:
    regions: List[str] = field(default_factory=list)
    region_words: List[str] = field(default_factory=list)
    orientation: List[str] = field(default_factory=list)
    floors: List[str] = field(default_factory=list)
    buildings: List[str] = field(default_factory=list)
    appearance: List[str] = field(default_factory=list)
    references: List[str] = field(default_factory=list)   # 资产类别
    reference_words: List[str] = field(default_factory=list)
    landmark: Optional[str] = None
    source: str = "rule"

    def to_dict(self) -> dict:
        return asdict(self)

    def summary(self) -> str:
        """给人看的一句话摘要（也会进入建议单的事实引用）。"""
        bits = []
        if self.region_words:
            bits.append("区域=" + "/".join(self.region_words))
        if self.references:
            bits.append("类别=" + "/".join(self.references))
        if self.orientation:
            bits.append("方位=" + "/".join(self.orientation))
        if self.floors:
            bits.append("楼层=" + "/".join(self.floors))
        if self.buildings:
            bits.append("楼栋=" + "/".join(self.buildings))
        if self.appearance:
            bits.append("外观=" + "/".join(self.appearance))
        return "；".join(bits) if bits else "无可识别要素"


class Extractor(Protocol):
    def extract(self, raw_text: str, division_aliases: List[tuple]) -> Elements: ...


# ---------------------------------------------------------------- 规则基线

class RuleExtractor:
    """规则基线：离线可跑、无外部依赖、可解释。"""

    name = "rule"

    def extract(self, raw_text: str, division_aliases: List[tuple]) -> Elements:
        text = raw_text or ""
        el = Elements(source="rule")

        # 行政区划线索：按别名长度优先匹配，避免"城东"被"东区"抢先
        hits = []
        for code, name, aliases in division_aliases:
            for a in [name] + list(aliases):
                if a and a in text:
                    hits.append((len(a), a, code))
        hits.sort(reverse=True)
        seen_code = set()
        for _, word, code in hits:
            if code not in seen_code:
                seen_code.add(code)
                el.regions.append(code)
                el.region_words.append(word)

        # 方位
        el.orientation = [w for w in ORIENTATION_LEXICON if w in text]

        # 楼层
        for m in FLOOR_RE.finditer(text):
            token = m.group(0).strip()
            if token not in el.floors:
                el.floors.append(token)
        for w, v in FLOOR_WORDS.items():
            if w in text and w not in el.floors:
                el.floors.append(w)

        # 楼栋
        for m in BUILDING_RE.finditer(text):
            token = m.group(0).strip()
            if token not in el.buildings:
                el.buildings.append(token)

        # 外观
        el.appearance = [w for w in APPEARANCE_LEXICON if w in text]

        # 参照物 → 类别（长词优先："雨水箅子" 要先于 "井盖"）
        ref_hits = []
        for cat, words in CATEGORY_LEXICON:
            for w in words:
                if w in text:
                    ref_hits.append((len(w), w, cat))
        ref_hits.sort(reverse=True)
        used_words = set()
        for _, w, cat in ref_hits:
            if w in used_words:
                continue
            used_words.add(w)
            if cat not in el.references:
                el.references.append(cat)
            el.reference_words.append(w)

        # 地标名词：参照物前面最靠近的名词性片段（取"大厅""站台"这类）
        el.landmark = _guess_landmark(text, el.reference_words)
        return el


def _guess_landmark(text: str, reference_words: List[str]) -> Optional[str]:
    """从参照物左侧回溯一个"地标名词"，如「大厅东侧饮水机」→「大厅」。"""
    landmark_lex = [
        "大厅", "大堂", "走廊", "楼道", "站台", "站台口", "门口", "广场",
        "花园", "小区", "园区", "食堂", "体育馆", "图书馆", "医院", "学校",
        "市场", "商场", "地下车库", "车库", "天桥", "路口", "巷口",
    ]
    for ref in reference_words:
        idx = text.find(ref)
        if idx < 0:
            continue
        left = text[:idx]
        best = None
        for lm in landmark_lex:
            if lm in left:
                if best is None or left.rindex(lm) > left.rindex(best):
                    best = lm
        if best:
            return best
    return None


# ---------------------------------------------------------------- LLM 增强

class LLMExtractor:
    """规则 + 本地模型补充。

    模型只补充「外观特征」与「地标名词」两类规则容易漏的要素；
    其余字段一律以规则为准。模型不可用时静默退回规则基线，不中断业务。
    """

    name = "rule+llm"

    def __init__(self, base: RuleExtractor | None = None):
        self.base = base or RuleExtractor()

    def extract(self, raw_text: str, division_aliases: List[tuple]) -> Elements:
        el = self.base.extract(raw_text, division_aliases)
        if not llm.available():
            return el
        prompt = (
            "从下面的报修描述中抽取结构化要素。只输出 JSON，不要解释。\n"
            '字段: {"appearance": [外观特征词], "landmark": "地标名词或null", "orientation": [方位词]}\n'
            "要求：只抽取原文中确实出现的词，不要臆造；无法确定就给空数组或 null。\n"
            f"描述：{raw_text}"
        )
        try:
            data = llm.extract_json(
                prompt,
                system="你是市政设施报修要素抽取器，只做字面抽取，不做推断。",
            )
        except llm.LLMUnavailable:
            return el
        if not isinstance(data, dict):
            return el
        for k in ("appearance", "orientation"):
            v = data.get(k)
            if isinstance(v, list):
                for w in v:
                    if isinstance(w, str) and w and w not in el.appearance:
                        el.appearance.append(w)
        lm = data.get("landmark")
        if isinstance(lm, str) and lm and not el.landmark:
            el.landmark = lm
        el.source = "rule+llm"
        return el


def default_extractor() -> Extractor:
    return LLMExtractor() if llm.available() else RuleExtractor()
