"""端到端冒烟：建议 → 人工动作 → 交接 → 确认 → 链路重建 → 指标 → 授权裁剪。

不依赖 pytest，直接用标准库跑；任何断言失败都会抛异常。
"""
import json
import sys
import urllib.error
import urllib.request

BASE = sys.argv[1] if len(sys.argv) > 1 else "http://127.0.0.1:8763"


def call(path, body=None, method=None):
    url = BASE + path
    data = None
    headers = {}
    if body is not None:
        data = json.dumps(body).encode("utf-8")
        headers["Content-Type"] = "application/json"
    req = urllib.request.Request(url, data=data, headers=headers,
                                 method=method or ("POST" if body is not None else "GET"))
    with urllib.request.urlopen(req, timeout=15) as r:
        return json.loads(r.read().decode("utf-8"))


def check(name, cond, detail=""):
    status = "OK  " if cond else "FAIL"
    print(f"[{status}] {name}" + (f"  → {detail}" if detail else ""))
    if not cond:
        raise SystemExit(f"断言失败：{name} {detail}")


# 1) 健康与状态
st = call("/api/status")
check("哈希链完整", st["哈希链"]["ok"], st["哈希链"]["detail"])
check("三个连接器心跳正常", all(c["healthy"] for c in st["连接器"]),
      str([c["status"] for c in st["连接器"]]))
check("制度性未决项默认保守", st["制度性未决项"]["自动删除"] is False
      and st["制度性未决项"]["双人确认"] is False
      and st["制度性未决项"]["交接约定时限_分钟"] is None,
      json.dumps(st["制度性未决项"], ensure_ascii=False))

# 2) 生成建议
biz = "WO-202600059"
s = call("/api/suggest", {"business_no": biz, "actor_id": "CO-0001"})
check("建议已生成", bool(s.get("suggestion_id")), s.get("suggestion_id", ""))
check("result_type 合法", s["result_type"] in ("suggest", "must_human", "insufficient_evidence"),
      s["result_type"])
check("带资料版本", bool(s.get("source_snapshot_version")), s.get("source_snapshot_version", ""))
check("带事实引用", bool(s.get("fact_refs")), str(len(s.get("fact_refs") or [])) + " 条")
check("候选不超过 Top-3", len(s["suggestion_result"]["candidates"]) <= 3)
check("每个候选带依据", all(c["reasons"] for c in s["suggestion_result"]["candidates"]))

# 3) 人工动作（追加写入，不覆盖）
a = call("/api/action", {"suggestion_id": s["suggestion_id"], "actor_id": "CO-0001",
                         "action": "accept", "note": "核对一致，采纳"})
check("人工动作留痕成功", bool(a.get("action_id")), a.get("action_id", ""))

# 4) 交接五要件 → 确认闭环
h = call("/api/handover", {"business_no": biz, "actor_id": "CO-0001",
                           "receiver": "维修二组-张工",
                           "unresolved_items": ["型号与编号", "资产曾发生迁移且编号未变，须核对是否已失效"],
                           "current_version": s["source_snapshot_version"],
                           "processing_status": "待派工"})
check("交接已发起", bool(h.get("business_no")), json.dumps(h, ensure_ascii=False)[:120])
hs = call("/api/handover/" + biz)
check("已通知但未闭环", hs.get("notified_at") is not None
      and hs.get("receiver_confirmed_at") is None, json.dumps(hs, ensure_ascii=False)[:160])
call("/api/handover/confirm", {"business_no": biz, "actor_id": "CO-0001"})
hs2 = call("/api/handover/" + biz)
check("确认后闭环", hs2.get("receiver_confirmed_at") is not None)

# 5) 链路重建（f45）
rb = call("/api/audit/" + biz)
check("可按业务编号重建", rb.get("record_count", 0) > 0,
      str(rb.get("record_count")) + " 条记录")
check("重建返回链与节点映射", "chain" in rb and "node_path" in rb)

# 5b) 走完派工→维修→验收→台账，验证六节点全覆盖
for act in ("dispatch", "repair", "acceptance", "ledger"):
    call("/api/action", {"suggestion_id": s["suggestion_id"], "actor_id": "CO-0001",
                         "action": act, "note": f"冒烟测试：{act}"})
rb2 = call("/api/audit/" + biz)
check("六节点全部覆盖", len(rb2.get("nodes_covered") or []) == 6,
      "已覆盖 " + str(rb2.get("nodes_covered")) + " / 缺失 " + str(rb2.get("nodes_missing")))
check("哈希链仍完整", rb2.get("chain", {}).get("ok") is True
      if isinstance(rb2.get("chain"), dict) else True)

# 6) 指标
m = call("/api/metrics")
check("分母为去重业务编号数", m["分母_去重业务编号数"] > 0, str(m["分母_去重业务编号数"]))
check("交接确认率已产出", m["交接确认率_%"] is not None, str(m["交接确认率_%"]))
check("可重建率 100", m["可重建率_%"] == 100.0, str(m["可重建率_%"]))
check("哈希链校验通过", m["哈希链校验通过"] is True)

# 7) 授权裁剪：社区服务代表看不到坐标/别名/迁移记录
cs = call("/api/suggestion/" + s["suggestion_id"] + "?actor_id=CS-0007")
c0 = (cs["suggestion_result"]["candidates"] or [{}])[0]
hidden = [f for f in ("lng", "lat", "alias_text", "description", "migration_version",
                      "source_version", "location_anchor") if f in c0]
check("越权字段已被服务端裁剪", not hidden, "泄露字段: " + str(hidden))
co = call("/api/suggestion/" + s["suggestion_id"] + "?actor_id=CO-0001")
c1 = (co["suggestion_result"]["candidates"] or [{}])[0]
check("维修协调员可见完整字段", "lng" in c1 and "alias_text" in c1)

print("\n全部冒烟项通过。")
