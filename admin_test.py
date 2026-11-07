# -*- coding: utf-8 -*-
"""后台（P0）自测：口令、CRUD、导入、导出 seed 包。用法：
    python admin_test.py http://127.0.0.1:8010 [口令]
"""
import io
import json
import sys
import urllib.error
import urllib.request
import zipfile

BASE = sys.argv[1] if len(sys.argv) > 1 else "http://127.0.0.1:8010"
TOKEN = sys.argv[2] if len(sys.argv) > 2 else ""
PASS = FAIL = 0


def call(method, path, body=None, token=None, raw=False):
    data = json.dumps(body).encode("utf-8") if body is not None else None
    headers = {"Content-Type": "application/json"}
    if token:
        headers["X-Admin-Token"] = token
    req = urllib.request.Request(BASE + path, data=data, method=method, headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=60) as r:
            payload = r.read()
            if raw:
                return r.status, payload, dict(r.headers)
            return r.status, json.loads(payload.decode("utf-8") or "{}")
    except urllib.error.HTTPError as e:
        payload = e.read().decode("utf-8") or "{}"
        try:
            return e.code, json.loads(payload)
        except json.JSONDecodeError:
            return e.code, {"raw": payload[:200]}


def upload(path, filename, raw_bytes, token=None):
    """手搓 multipart，免第三方库。"""
    boundary = "----poetsboundary7f3a"
    body = b"".join([
        ("--%s\r\n" % boundary).encode(),
        ('Content-Disposition: form-data; name="file"; filename="%s"\r\n' % filename).encode(),
        b"Content-Type: application/json\r\n\r\n",
        raw_bytes,
        ("\r\n--%s--\r\n" % boundary).encode(),
    ])
    headers = {"Content-Type": "multipart/form-data; boundary=%s" % boundary}
    if token:
        headers["X-Admin-Token"] = token
    req = urllib.request.Request(BASE + path, data=body, method="POST", headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=60) as r:
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


print("== 目标 %s（口令：%s）" % (BASE, "有" if TOKEN else "无"))
s, body, headers = call("GET", "/admin.html", raw=True)
html = body.decode("utf-8", "replace")
check("后台页面 200", s == 200, s)
check("页面含『数据后台』", "数据后台" in html)
check("页面带 X-Admin-Token", "X-Admin-Token" in html)

s, j = call("GET", "/api/admin/status")
check("status 200", s == 200 and "protected" in j, j)
protected = bool(j.get("protected"))
print("       protected=%s mode=%s" % (j.get("protected"), j.get("mode")))
check("口令状态与预期一致", protected == bool(TOKEN), (protected, TOKEN))

s, j = call("POST", "/api/admin/verify", {}, token=TOKEN)
if TOKEN:
    check("正确口令 verify 200", s == 200 and j.get("ok") is True, (s, j))
    s, j = call("POST", "/api/admin/verify", {}, token="wrong-token")
    check("错误口令 401", s == 401, (s, j))
    s, j = call("POST", "/api/persons", {"id": "adm1", "name": "无口令"})
    check("无口令写操作 401", s == 401, (s, j))
else:
    check("本机免口令 verify 200", s == 200 and j.get("ok") is True, (s, j))

# --- 正常 CRUD（带正确口令 / 或本机）---
s, j = call("POST", "/api/persons", {"id": "adm1", "name": "后台测试", "dynasty": "唐",
                                    "bio": "自测用", "life": "700—760"}, token=TOKEN)
check("建人物 201", s == 201, (s, j))
s, j = call("PUT", "/api/persons/adm1", {"color": "#123456", "bio": "改过"}, token=TOKEN)
check("改人物", s == 200 and j.get("color") == "#123456" and j.get("bio") == "改过", j)
s, n1 = call("POST", "/api/persons/adm1/nodes", {"year": 730, "yearText": "开元十八年", "place": "长安",
                                                "event": "测试节点一", "lng": 108.9, "lat": 34.3}, token=TOKEN)
check("加节点1 201", s == 201 and n1.get("id") == "adm1-01", (s, n1))
s, n2 = call("POST", "/api/persons/adm1/nodes", {"year": 745, "place": "洛阳", "event": "测试节点二",
                                                "approx": True}, token=TOKEN)
check("加节点2 201", s == 201 and n2.get("id") == "adm1-02", (s, n2))
s, j = call("PUT", "/api/nodes/adm1-02", {"event": "节点二改过", "lng": 112.4, "lat": 34.6}, token=TOKEN)
check("改节点", s == 200 and j.get("event") == "节点二改过" and j.get("lng") == 112.4, j)
s, j = call("GET", "/api/persons/adm1")
check("按年排序", s == 200 and [n["id"] for n in j["nodes"]] == ["adm1-01", "adm1-02"], [n["id"] for n in j.get("nodes", [])])
s, j = call("GET", "/api/geojson")
check("新节点进 geojson", any(f["properties"].get("id") == "adm1-01" for f in j["features"]))

# --- 导入 ---
docs = [{"id": "adm2", "name": "导入测试", "dynasty": "宋",
         "nodes": [{"id": "adm2-01", "year": 1080, "place": "黄州", "event": "导入的节点", "lng": 114.9, "lat": 30.4}]}]
s, j = call("POST", "/api/import", {"strategy": "skip", "documents": docs}, token=TOKEN)
check("导入 skip 新增", s == 200 and j["added"] == ["adm2"], (s, j))
s, j = call("POST", "/api/import", {"strategy": "skip", "documents": docs}, token=TOKEN)
check("导入 skip 跳过已有", s == 200 and j["skipped"] == ["adm2"], (s, j))
s, j = call("POST", "/api/import", {"strategy": "overwrite", "documents": docs}, token=TOKEN)
check("导入 overwrite 覆盖", s == 200 and j["overwritten"] == ["adm2"], (s, j))

# --- 上传解析（POST /api/upload，只解析不写库）---
raw_doc = json.dumps([{"id": "up1", "name": "上传测试", "dynasty": "唐", "nodes": [
    {"id": "up1-01", "year": 900, "place": "长安", "event": "上传的节点",
     "lng": 108.9, "lat": 34.3}]}], ensure_ascii=False).encode("utf-8")
s, j = upload("/api/upload", "up.json", raw_doc, token=TOKEN)
check("上传 200", s == 200, (s, j))
check("上传解析 1 人 1 节点", j.get("persons") == 1 and j.get("nodes") == 1, j)
check("上传不改库", call("GET", "/api/persons/up1", token=TOKEN)[0] == 404)
s, j = upload("/api/upload", "bad.json", b"not-a-json", token=TOKEN)
check("上传坏 JSON 400", s == 400, (s, j))

# --- 导出 ---
s, j = call("GET", "/api/export")
check("export 含测试人物", s == 200 and any(p["id"] == "adm2" for p in j["persons"]))
s, body, headers = call("GET", "/api/export/seed.zip", raw=True)
check("seed.zip 200", s == 200, s)
check("seed.zip 是压缩包", headers.get("content-type", "").startswith("application/zip"), headers.get("content-type"))
z = zipfile.ZipFile(io.BytesIO(body))
names = z.namelist()
check("seed.zip 含 persons_meta.json", "persons_meta.json" in names, names[:3])
check("seed.zip 一人一文件", "adm2.json" in names and len(names) >= 22, len(names))
doc = json.loads(z.read("adm2.json").decode("utf-8"))
check("seed 文件结构对", doc.get("id") == "adm2" and len(doc.get("nodes") or []) == 1, doc.keys())

# --- 删掉测试数据 ---
s, j = call("DELETE", "/api/nodes/adm1-02", token=TOKEN)
check("删节点", s == 200, j)
s, j = call("DELETE", "/api/persons/adm1", token=TOKEN)
check("删人物", s == 200, j)
s, j = call("DELETE", "/api/persons/adm2", token=TOKEN)
check("删导入人物", s == 200, j)
s, j = call("GET", "/api/health")
check("回到 20 人", j.get("persons") == 20, j)

print("\n通过 %d，失败 %d" % (PASS, FAIL))
sys.exit(0 if FAIL == 0 else 1)
