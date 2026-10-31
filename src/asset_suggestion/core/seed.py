"""云州模拟数据生成（合成场景，仅用于链路验证）。

对应方案的试点范围：两个街区试点、资产定位准确率 98% / 凭证完整率 100%。

规模按发现报告基线缩放：1.4 万件设施、每日 90 张工单（f12/f51）。
MVP 默认生成较小规模以便秒级回放，`--assets` 可放大到 14000。

**重要声明**：本数据是合成的。它能验证"链路是否跑得通、闸门是否正确触发、
留痕是否完整"，**不能**用来主张真实场景下的定位准确率——真实准确率必须
由阶段 2 历史回放用真实工单测得（方案 §12.1）。
"""
from __future__ import annotations

import datetime as _dt
import json
import random
from typing import Dict, List, Tuple

from .config import CONFIG
from .db import jdump
from .districts import CATEGORY_LEXICON, division_seed_rows
from ..utils.geo import point_in_polygon

LANDMARKS = [
    "大厅", "大堂", "走廊", "楼道", "站台", "广场", "花园", "食堂",
    "体育馆", "图书馆", "医院", "学校", "市场", "商场", "地下车库",
    "天桥", "路口", "巷口", "会所", "岗亭",
]

ORIENTATIONS = ["东侧", "西侧", "南侧", "北侧", "背面", "前面", "门口", "里面", "旁边"]

DEFECTS = [
    "漏水", "不亮", "破损", "异响", "堵塞", "倒了一半", "缺了一块",
    "锈得厉害", "掉漆", "关不上", "歪了", "不转了", "裂了", "发黑",
]

# 街区 → 社区（试点两个街区：城东区、滨江/老城片区由试点开关控制）
PILOT_STREETS = ["YZ320102", "YZ320104"]   # 城东区、老城区


def _now():
    return _dt.datetime.now(_dt.timezone.utc).isoformat(timespec="seconds")


def _rand_point_in(polygon, rng) -> Tuple[float, float]:
    xs = [p[0] for p in polygon]
    ys = [p[1] for p in polygon]
    for _ in range(80):
        x = rng.uniform(min(xs), max(xs))
        y = rng.uniform(min(ys), max(ys))
        if point_in_polygon(x, y, polygon):
            return round(x, 6), round(y, 6)
    return round(sum(xs) / len(xs), 6), round(sum(ys) / len(ys), 6)


def seed(conn, assets: int = None, workorders: int = None,
         degraded: bool = False, seed_value: int = 20261029) -> dict:
    assets = assets if assets is not None else CONFIG.seed_assets
    workorders = workorders if workorders is not None else CONFIG.seed_workorders
    rng = random.Random(seed_value)

    # ---- 行政区划 ----
    conn.executemany(
        "INSERT OR REPLACE INTO admin_division (code,name,level,parent_code,lat,lng,polygon,aliases,source)"
        " VALUES (:code,:name,:level,:parent_code,:lat,:lng,:polygon,:aliases,:source)",
        division_seed_rows())
    conn.commit()

    divs = {r["code"]: dict(r) for r in conn.execute("SELECT * FROM admin_division")}
    communities = [c for c in divs.values() if c["level"] == 3]
    pilot_communities = [c for c in communities
                         if any(c["code"].startswith(s) for s in PILOT_STREETS)]
    use = pilot_communities or communities

    # ---- 资产 ----
    cat_list = [c for c, _ in CATEGORY_LEXICON]
    rows: List[dict] = []
    version_rows: List[tuple] = []
    per = max(1, assets // len(use))
    idx = 0
    for community in use:
        poly = json.loads(community["polygon"])
        for i in range(per):
            cat = cat_list[idx % len(cat_list)]
            words = dict(CATEGORY_LEXICON)[cat]
            landmark = rng.choice(LANDMARKS)
            orient = rng.choice(ORIENTATIONS)
            alias = f"{landmark}{orient}{rng.choice(words)}"
            lng, lat = _rand_point_in(poly, rng)
            code = f"WG-{cat[:2].upper()}-{idx:05d}"
            pk = f"PK-{cat[:2].upper()}-{idx:05d}"
            coord_sys = rng.choices(["WGS84", "GCJ02", "BD09"], weights=[88, 8, 4])[0]
            src_version = f"sv-{rng.randint(100000, 999999)}"
            row = {
                "asset_pk": pk, "asset_code": code, "asset_category": cat,
                "alias_text": jdump([alias] + ([f"{landmark}的{rng.choice(words)}"] if rng.random() < 0.5 else [])),
                "description": f"{cat}，位于{community['name']}，{landmark}{orient}",
                "lng": lng, "lat": lat, "coord_sys": coord_sys,
                "location_anchor": community["code"],
                "migration_version": None, "prev_lng": None, "prev_lat": None,
                "prev_asset_code": None, "source_version": src_version,
                "ambiguity_flag": 0, "ambiguity_reason": "[]",
                "stale_flag": 0, "fetched_at": _now(), "last_moved_at": None,
            }
            r = rng.random()
            if r < 0.06:      # 迁移且有版本号、编号未变 → G1
                row["migration_version"] = f"mv-{rng.randint(1, 40)}"
                row["prev_lng"] = round(lng + rng.uniform(-0.004, 0.004), 6)
                row["prev_lat"] = round(lat + rng.uniform(-0.003, 0.003), 6)
                row["prev_asset_code"] = code
                row["last_moved_at"] = _now()
            elif r < 0.11:    # 迁移但无版本号 → 歧义（无版本迁移）
                row["prev_lng"] = round(lng + rng.uniform(-0.003, 0.003), 6)
                row["prev_lat"] = round(lat + rng.uniform(-0.002, 0.002), 6)
                row["prev_asset_code"] = code
            if rng.random() < 0.02:   # 资料缺少依据 → G2
                row["source_version"] = None
            if rng.random() < 0.015:  # 坐标系不明 → 歧义
                row["coord_sys"] = "UNKNOWN-LOCAL"
            rows.append(row)
            version_rows.append((pk, src_version, lng, lat, coord_sys, _now()))
            idx += 1

    # 重复编号（同 asset_code 出现多条 asset_pk）→ 歧义
    by_code: Dict[str, List[dict]] = {}
    for r in rows:
        by_code.setdefault(r["asset_code"], []).append(r)
    dup_count = 0
    for code, group in by_code.items():
        if len(group) > 1 and rng.random() < 0.6:
            for r in group:
                reasons = set(json.loads(r["ambiguity_reason"]))
                reasons.add("重复编号")
                r["ambiguity_reason"] = jdump(sorted(reasons))
                r["ambiguity_flag"] = 1
            dup_count += len(group)

    # 另造一组"同 asset_code 不同 asset_pk"的确定性样本，保证该歧义一定存在
    if rows:
        base = rows[0]
        clone = dict(base)
        clone["asset_pk"] = base["asset_pk"] + "-DUP"
        clone["alias_text"] = jdump([f"{base['alias_text'] and json.loads(base['alias_text'])[0]}"])
        clone["ambiguity_flag"] = 1
        clone["ambiguity_reason"] = jdump(["重复编号"])
        clone["source_version"] = f"sv-{rng.randint(100000, 999999)}"
        rows.append(clone)
        version_rows.append((clone["asset_pk"], clone["source_version"],
                             clone["lng"], clone["lat"], clone["coord_sys"], _now()))
        dup_count += 1

    # 无版本迁移 / 坐标系不明的歧义标记
    for r in rows:
        reasons = set(json.loads(r["ambiguity_reason"]))
        if r["prev_lng"] is not None and not r["migration_version"]:
            reasons.add("无版本迁移")
        if r["coord_sys"] not in ("WGS84", "GCJ02", "BD09"):
            reasons.add("坐标系不明")
        if reasons:
            r["ambiguity_reason"] = jdump(sorted(reasons))
            r["ambiguity_flag"] = 1

    conn.executemany(
        "INSERT OR REPLACE INTO asset_snapshot (asset_pk,asset_code,asset_category,alias_text,"
        "description,lng,lat,coord_sys,location_anchor,migration_version,prev_lng,prev_lat,"
        "prev_asset_code,source_version,ambiguity_flag,ambiguity_reason,stale_flag,fetched_at,last_moved_at)"
        " VALUES (:asset_pk,:asset_code,:asset_category,:alias_text,:description,:lng,:lat,:coord_sys,"
        ":location_anchor,:migration_version,:prev_lng,:prev_lat,:prev_asset_code,:source_version,"
        ":ambiguity_flag,:ambiguity_reason,:stale_flag,:fetched_at,:last_moved_at)", rows)
    conn.executemany(
        "INSERT OR REPLACE INTO asset_version_snapshot (asset_pk,source_version,lng,lat,coord_sys,fetched_at)"
        " VALUES (?,?,?,?,?,?)", version_rows)
    conn.commit()

    # ---- G3 样本：同一资产两个版本且坐标不同（均匀撒开，避免集中在少数类别）----
    g3_count = 0
    if rows:
        stride = max(1, len(rows) // 15)
        for r in rows[::stride][:15]:
            conn.execute(
                "INSERT OR REPLACE INTO asset_version_snapshot (asset_pk,source_version,lng,lat,coord_sys,fetched_at)"
                " VALUES (?,?,?,?,?,?)",
                (r["asset_pk"], f"sv-conflict-{g3_count}",
                 round(r["lng"] + 0.002, 6), round(r["lat"] + 0.0015, 6),
                 r["coord_sys"], _now()))
            g3_count += 1
    conn.commit()

    # ---- 热线受理 + 工单 ----
    # 难度分层决定"原文里有多少线索"，直接决定阶段二行政锚定能否命中：
    #   A 全线索（地名+地标）  30%
    #   B 只有地标            25%
    #   C 地标残缺            23%
    #   D 只有地名+类别       12%
    #   E 地名记错/说错        10%  ← 锚定到别的单元，正确资产只能靠语义通道补回
    intakes, wos = [], []
    for i in range(workorders):
        truth = rows[rng.randrange(len(rows))]
        alias_list = json.loads(truth["alias_text"])
        alias = alias_list[0]
        community = divs.get(truth["location_anchor"], {})
        difficulty = rng.random()
        if difficulty < 0.30:
            # 群众既说地名也说地标——最常见也最容易的一档
            text = f"{community.get('name','')}{alias}{rng.choice(DEFECTS)}了"
        elif difficulty < 0.55:
            # 只说地标，不带地名
            text = f"{alias}{rng.choice(DEFECTS)}了"
        elif difficulty < 0.78:
            # 丢掉方位或地标，只留部分信息
            parts = alias
            if len(parts) > 4 and rng.random() < 0.6:
                parts = parts[2:] if rng.random() < 0.5 else parts[:-2]
            text = f"{parts}好像{rng.choice(DEFECTS)}了"
        elif difficulty < 0.90:
            # 只说类别与片区
            text = f"{community.get('name','')}那边有个{rng.choice(DEFECTS)}的{truth['asset_category']}"
        else:
            # 地名说错：锚定到别的行政单元，正确资产不在锚定单元内，
            # 只能靠"语义通道"补回——这正是方案 §6.5 两通道取并集的理由。
            others = [c for c in use if c["code"] != truth["location_anchor"]]
            wrong = rng.choice(others) if others else community
            text = f"{wrong.get('name','')}{alias}{rng.choice(DEFECTS)}了"
        biz = f"WO-2026{i:05d}"
        received = _now()
        intakes.append({
            "intake_id": f"IN-2026{i:05d}", "business_no": biz, "raw_text": text,
            "photos": jdump([]), "received_at": received,
        })
        wos.append({
            "business_no": biz, "status": "待派工", "category": truth["asset_category"],
            "assignee": None, "dept": "维修二组",
            "updated_at": received,
            "community_done_at": None, "repair_done_at": None, "ledger_done_at": None,
            "truth_asset_pk": truth["asset_pk"],
        })
    conn.executemany(
        "INSERT OR REPLACE INTO hotline_intake (intake_id,business_no,raw_text,photos,received_at)"
        " VALUES (:intake_id,:business_no,:raw_text,:photos,:received_at)", intakes)
    conn.executemany(
        "INSERT OR REPLACE INTO workorder_snapshot (business_no,status,category,assignee,dept,"
        "updated_at,community_done_at,repair_done_at,ledger_done_at,truth_asset_pk)"
        " VALUES (:business_no,:status,:category,:assignee,:dept,:updated_at,:community_done_at,"
        ":repair_done_at,:ledger_done_at,:truth_asset_pk)", wos)
    conn.commit()

    # ---- 连接器心跳 ----
    hb = _now()
    for name in ["GIS资产库", "维修工单", "社区热线"]:
        conn.execute(
            "INSERT OR REPLACE INTO connector_state (connector,last_heartbeat,status) VALUES (?,?,?)",
            (name, hb, "up"))
    if degraded:
        old = (_dt.datetime.now(_dt.timezone.utc) - _dt.timedelta(minutes=40)).isoformat(timespec="seconds")
        conn.execute("UPDATE connector_state SET last_heartbeat=?, status='down' WHERE connector='GIS资产库'",
                     (old,))
    conn.commit()

    # ---- 规则版本（f24）----
    conn.execute(
        "INSERT OR REPLACE INTO rule_version (rule_name,rule_version,effective_from,effective_to,scope,payload)"
        " VALUES (?,?,?,?,?,?)",
        ("match_pipeline", "MVP-R1", "2026-01-01", None, "all",
         jdump({"recall_radius_m": CONFIG.recall_radius_m, "top_k": CONFIG.top_k,
                "confidence_delta": CONFIG.confidence_delta})))
    conn.commit()

    stats = {
        "assets": len(rows),
        "workorders": len(intakes),
        "ambiguity_assets": sum(1 for r in rows if r["ambiguity_flag"]),
        "duplicate_code_rows": dup_count,
        "g1_assets": sum(1 for r in rows if r["migration_version"] and r["prev_asset_code"] == r["asset_code"]),
        "g2_assets": sum(1 for r in rows if not r["source_version"]),
        "g3_assets": g3_count,
        "degraded": degraded,
    }
    return stats
