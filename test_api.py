# -*- coding: utf-8 -*-
"""FastAPI 版自测：与 Flask 版对齐（CRUD / 判重 / GeoJSON / AI）"""
import json
import sys
import urllib.error
import urllib.request

BASE = sys.argv[1] if len(sys.argv) > 1 else "http://127.0.0.1:8100"
TOKEN = sys.argv[2] if len(sys.argv) > 2 else ""          # 服务端配了 ADMIN_TOKEN 时传第二个参数
PASS = FAIL = 0


def call(method, path, body=None):
    data = json.dumps(body).encode("utf-8") if body is not None else None
    headers = {"Content-Type": "application/json"}
    if TOKEN:
        headers["X-Admin-Token"] = TOKEN
    req = urllib.request.Request(BASE + path, data=data, method=method, headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=90) as r:
            return r.status, json.loads(r.read().decode("utf-8") or "{}")
    except urllib.error.HTTPError as e:
        raw = e.read().decode("utf-8") or "{}"
        try:
            return e.code, json.loads(raw)
        except json.JSONDecodeError:
            return e.code, {"raw": raw[:200]}


def check(name, cond, extra=""):
    global PASS, FAIL
    if cond:
        PASS += 1
        print("  [PASS] %s" % name)
    else:
        FAIL += 1
        print("  [FAIL] %s  %s" % (name, extra))


s, j = call("GET", "/api/health")
check("健康检查 20 人 246 节点", s == 200 and j["persons"] == 20 and j["nodes"] == 246, j)
s, j = call("GET", "/api/geojson")
check("GeoJSON 246 点", s == 200 and len(j["features"]) == 246, len(j.get("features", [])))
check("中文键", "人物" in j["features"][0]["properties"])
s, j = call("GET", "/api/persons/libai")
check("李白 19 节点", s == 200 and len(j["nodes"]) == 19, len(j.get("nodes", [])))
check("带生活/配色", bool(j.get("life")) and bool(j.get("color")), (j.get("life"), j.get("color")))

s, j = call("GET", "/api/stats")
check("统计：20 人 246 节点", s == 200 and j.get("persons") == 20 and j.get("nodes") == 246, (s, j.get("persons")))
check("统计：城市/省份/里程", s == 200 and j.get("cities", 0) >= 100 and j.get("provinces", 0) >= 15
      and j.get("distance_km", 0) > 10000, (j.get("cities"), j.get("provinces"), j.get("distance_km")))
s, j = call("GET", "/api/stats?person=libai")
check("李白统计", s == 200 and j.get("name") == "李白" and j.get("nodes") == 19 and j.get("poems", 0) > 0,
      (j.get("name"), j.get("nodes"), j.get("poems")))
s, j = call("GET", "/api/stats?person=nobody")
check("统计不存在人物 404", s == 404, s)

s, j = call("POST", "/api/persons", {"id": "t1", "name": "测试", "dynasty": "唐"})
check("新建 201", s == 201, (s, j))
s, j = call("POST", "/api/persons", {"id": "t1", "name": "测试"})
check("重复 409", s == 409, (s, j))
s, j = call("PUT", "/api/persons/t1", {"bio": "改过"})
check("改人物", s == 200 and j.get("bio") == "改过", j.get("bio"))
s, j = call("POST", "/api/persons/t1/nodes", {"year": 800, "yearText": "贞元十六年",
                                              "place": "长安", "event": "测试", "lng": 108.9, "lat": 34.3})
check("加节点", s == 201 and j.get("id") == "t1-01", (s, j))
nid = j.get("id")
s, j = call("PUT", "/api/nodes/%s" % nid, {"event": "改过节点", "approx": True})
check("改节点", s == 200 and j.get("event") == "改过节点" and j.get("approx") is True, j)
s, j = call("DELETE", "/api/nodes/%s" % nid)
check("删节点", s == 200, j)
s, j = call("DELETE", "/api/persons/t1")
check("删人物", s == 200, j)

s, j = call("POST", "/api/import", {"strategy": "skip"})
check("skip 全跳过", s == 200 and len(j.get("skipped", [])) == 20, j.get("skipped"))
s, j = call("POST", "/api/import", {"strategy": "overwrite"})
check("overwrite 全覆盖", s == 200 and len(j.get("overwritten", [])) == 20, j.get("overwritten"))

s, j = call("GET", "/api/ask/quota")
check("额度接口", s == 200 and "remaining" in j, j)
s, j = call("POST", "/api/ask", {"question": ""})
check("空问题 400", s == 400, (s, j))
s, j = call("POST", "/api/ask", {"question": "李白去过哪", "person": "libai", "mock": True})
check("mock 可用", s == 200 and "李白" in j.get("answer", ""), (j.get("answer") or "")[:40])

print("\n通过 %d，失败 %d" % (PASS, FAIL))
sys.exit(0 if FAIL == 0 else 1)
