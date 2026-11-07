# -*- coding: utf-8 -*-
"""诗路重现 · FastAPI + SQLite 后端

接口与 Flask 版完全一致（前端不用改）：
    /api/health  /api/persons  /api/persons/{id}  /api/nodes/{id}
    /api/import  /api/export  /api/geojson  /api/ask  /api/ask/quota
    /api/config/status  /api/config/key

管理后台（P0）：/admin.html
    写操作（POST/PUT/DELETE/import）需带 X-Admin-Token，口令来自环境变量 ADMIN_TOKEN；
    只读 GET 不校验，公开页面照常访问。

本地跑：  uvicorn app:app --reload --port 8000
线上跑：  uvicorn app:app --host 0.0.0.0 --port $PORT
"""
import hmac
import io
import json
import math
import os
import re
import ssl
import threading
import time
import urllib.error
import urllib.request
import zipfile

from fastapi import Body, Depends, FastAPI, File, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse, JSONResponse, Response
from fastapi.staticfiles import StaticFiles

import db

BASE = os.path.dirname(os.path.abspath(__file__))


def first_existing(*paths):
    for p in paths:
        if p and os.path.isdir(p):
            return os.path.abspath(p)
    return None


STATIC_DIR = first_existing(os.environ.get("STATIC_DIR"), os.path.join(BASE, "static"),
                            os.path.join(BASE, "..", "数据"))
SEED_DIR = first_existing(os.path.join(BASE, "seed", "pers"),
                          os.path.join(BASE, "..", "数据", "4.对齐后", "pers"))
META_FILE = (os.path.join(BASE, "seed", "persons_meta.json")
             if os.path.isfile(os.path.join(BASE, "seed", "persons_meta.json"))
             else os.path.join(BASE, "..", "数据", "人物简介.json"))

app = FastAPI(title="诗路重现 API", version="1.0")


# --------------------------------------------------------------------------- #
# 启动：建库 + 首次灌数据
# --------------------------------------------------------------------------- #
def load_meta():
    if not os.path.isfile(META_FILE):
        return {}
    with open(META_FILE, "r", encoding="utf-8-sig") as f:
        items = json.load(f)
    return {p["name"]: p for p in items if p.get("name")}


def seed():
    persons, nodes = db.stats()
    if persons:
        return
    if not SEED_DIR:
        return
    meta = load_meta()
    n = 0
    for name in sorted(os.listdir(SEED_DIR)):
        if not name.endswith(".json"):
            continue
        with open(os.path.join(SEED_DIR, name), "r", encoding="utf-8-sig") as f:
            doc = json.load(f)
        m = meta.get(doc.get("name"), {})
        doc.update({k: m[k] for k in ("life", "color", "birth", "death") if m.get(k) is not None})
        db.upsert_person(doc, overwrite=True)
        n += 1
    print("[seed] 已导入 %d 个人物" % n)


db.init_db()
seed()


# --------------------------------------------------------------------------- #
# 限流 / 缓存
# --------------------------------------------------------------------------- #
ASK_LIMIT_PER_MIN = int(os.environ.get("ASK_LIMIT_PER_MIN", "6"))
ASK_LIMIT_PER_DAY = int(os.environ.get("ASK_LIMIT_PER_DAY", "120"))
ASK_GLOBAL_PER_MIN = int(os.environ.get("ASK_GLOBAL_PER_MIN", "60"))
ASK_GLOBAL_PER_DAY = int(os.environ.get("ASK_GLOBAL_PER_DAY", "2000"))
ASK_CACHE_TTL = 600

_lock = threading.Lock()
_minute, _day, _cache = {}, {}, {}
_g_minute, _g_day = [], [None, 0]


def client_ip(request: Request):
    ra = request.client.host if request.client else "?"
    if ra not in ("127.0.0.1", "::1", "testclient"):
        return ra
    cf = (request.headers.get("CF-Connecting-IP") or "").strip()
    if cf:
        return cf
    fwd = request.headers.get("X-Forwarded-For", "")
    if fwd:
        return fwd.split(",")[-1].strip()
    return ra


def quota_check(ip):
    now, today = time.time(), time.strftime("%Y-%m-%d")
    with _lock:
        while _g_minute and now - _g_minute[0] > 60:
            _g_minute.pop(0)
        if len(_g_minute) >= ASK_GLOBAL_PER_MIN:
            return False, "整站提问太频繁了，请稍等半分钟", 0
        if _g_day[0] != today:
            _g_day[0], _g_day[1] = today, 0
        if _g_day[1] >= ASK_GLOBAL_PER_DAY:
            return False, "整站今天的免费额度用完了，明天再来", 0
        _g_minute.append(now)
        _g_day[1] += 1

        if ip in ("127.0.0.1", "::1", "testclient"):
            return True, "", -1
        dq = _minute.setdefault(ip, [])
        while dq and now - dq[0] > 60:
            dq.pop(0)
        if len(dq) >= ASK_LIMIT_PER_MIN:
            return False, "提问太快了，请 %d 秒后再试" % (int(60 - (now - dq[0])) + 1), 0
        rec = _day.get(ip)
        if not rec or rec[0] != today:
            rec = [today, 0]
            _day[ip] = rec
        if rec[1] >= ASK_LIMIT_PER_DAY:
            return False, "今天的免费提问已用完（%d 次/天），明天再来" % ASK_LIMIT_PER_DAY, 0
        dq.append(now)
        rec[1] += 1
        return True, "", ASK_LIMIT_PER_DAY - rec[1]


def quota_left(ip):
    if ip in ("127.0.0.1", "::1", "testclient"):
        return -1
    rec = _day.get(ip)
    today = time.strftime("%Y-%m-%d")
    if not rec or rec[0] != today:
        return ASK_LIMIT_PER_DAY
    return max(0, ASK_LIMIT_PER_DAY - rec[1])


# --------------------------------------------------------------------------- #
# 管理口令：所有写操作（POST/PUT/DELETE/import）都要带 X-Admin-Token
# --------------------------------------------------------------------------- #
ADMIN_TOKEN = (os.environ.get("ADMIN_TOKEN") or "").strip()
LOCAL_IPS = ("127.0.0.1", "::1", "testclient")


def is_local(request: Request):
    return client_ip(request) in LOCAL_IPS


def admin_guard(request: Request):
    """写操作校验。

    - 配了 ADMIN_TOKEN：请求头 X-Admin-Token 必须一致，否则 401；
    - 没配 ADMIN_TOKEN：只放行本机（本地开发），线上直接 503，防止裸奔。
    只读接口（GET）不校验，公开页面照常访问。
    """
    if not ADMIN_TOKEN:
        if is_local(request):
            return
        raise HTTPException(503, "服务端没有配置 ADMIN_TOKEN，写操作已关闭")
    got = (request.headers.get("X-Admin-Token") or "").strip()
    if not got:
        raise HTTPException(401, "需要管理口令")
    if not hmac.compare_digest(got, ADMIN_TOKEN):
        raise HTTPException(401, "口令不正确")
    return


# --------------------------------------------------------------------------- #
# 基础接口
# --------------------------------------------------------------------------- #
@app.api_route("/api/health", methods=["GET", "HEAD"])
def health():
    p, n = db.stats()
    return {"ok": True, "persons": p, "nodes": n, "db": db.DB_PATH,
            "static": STATIC_DIR, "engine": "fastapi+sqlite"}


@app.get("/api/persons")
def api_persons(order: str = "name"):
    items = db.list_persons(order)
    return {"total": len(items), "list": items}


@app.get("/api/persons/{pid}")
def api_person(pid: str):
    doc = db.get_person(pid)
    if not doc:
        raise HTTPException(404, "人物不存在：%s" % pid)
    return doc


@app.post("/api/persons", status_code=201)
def api_person_create(payload: dict = Body(...), _admin=Depends(admin_guard)):
    if not payload.get("id") or not payload.get("name"):
        raise HTTPException(400, "缺少字段 id 或 name")
    if db.get_person(payload["id"], with_nodes=False):
        raise HTTPException(409, "id 已存在：%s" % payload["id"])
    db.upsert_person(payload, overwrite=True)
    return db.get_person(payload["id"])


@app.put("/api/persons/{pid}")
def api_person_update(pid: str, payload: dict = Body(...), _admin=Depends(admin_guard)):
    if not db.get_person(pid, with_nodes=False):
        raise HTTPException(404, "人物不存在：%s" % pid)
    return db.update_person(pid, payload)


@app.delete("/api/persons/{pid}")
def api_person_delete(pid: str, _admin=Depends(admin_guard)):
    if not db.delete_person(pid):
        raise HTTPException(404, "人物不存在：%s" % pid)
    return {"deleted": pid}


@app.get("/api/persons/{pid}/nodes")
def api_nodes(pid: str):
    doc = db.get_person(pid)
    if not doc:
        raise HTTPException(404, "人物不存在：%s" % pid)
    return {"total": len(doc["nodes"]), "list": doc["nodes"]}


@app.post("/api/persons/{pid}/nodes", status_code=201)
def api_node_create(pid: str, payload: dict = Body(...), _admin=Depends(admin_guard)):
    if not db.get_person(pid, with_nodes=False):
        raise HTTPException(404, "人物不存在：%s" % pid)
    if payload.get("year") is None:
        raise HTTPException(400, "缺少字段 year")
    return db.add_node(pid, payload)


@app.put("/api/nodes/{node_id}")
def api_node_update(node_id: str, payload: dict = Body(...), _admin=Depends(admin_guard)):
    if not db.find_node(node_id):
        raise HTTPException(404, "节点不存在：%s" % node_id)
    return db.update_node(node_id, payload)


@app.delete("/api/nodes/{node_id}")
def api_node_delete(node_id: str, _admin=Depends(admin_guard)):
    if not db.delete_node(node_id):
        raise HTTPException(404, "节点不存在：%s" % node_id)
    return {"deleted": node_id}


@app.post("/api/import")
def api_import(payload: dict = Body(...), _admin=Depends(admin_guard)):
    strategy = payload.get("strategy", "skip")
    if strategy not in ("skip", "overwrite"):
        raise HTTPException(400, "strategy 只能是 skip 或 overwrite")
    docs = payload.get("documents")
    src = payload.get("path") or SEED_DIR
    if not docs:
        if not src or not os.path.isdir(src):
            raise HTTPException(400, "目录不存在：%s" % src)
        docs = []
        for name in sorted(os.listdir(src)):
            if name.endswith(".json"):
                with open(os.path.join(src, name), "r", encoding="utf-8-sig") as f:
                    docs.append(json.load(f))
    added, skipped, overwritten, errors = [], [], [], []
    meta = load_meta()
    for d in docs:
        try:
            if not d.get("id"):
                errors.append({"doc": d.get("name", "?"), "reason": "缺少 id"})
                continue
            m = meta.get(d.get("name"), {})
            d.update({k: m[k] for k in ("life", "color", "birth", "death") if m.get(k) is not None})
            res = db.upsert_person(d, overwrite=(strategy == "overwrite"))
            {"added": added, "skipped": skipped, "overwritten": overwritten}[res].append(d["id"])
        except Exception as exc:                      # noqa: BLE001
            errors.append({"doc": d.get("id", "?"), "reason": str(exc)})
    return {"strategy": strategy, "source": src, "added": added, "skipped": skipped,
            "overwritten": overwritten, "errors": errors, "total": len(docs)}


@app.get("/api/export")
def api_export():
    return {"persons": db.all_persons_full()}


MAX_UPLOAD = 5 * 1024 * 1024


def normalize_docs(data):
    """把上传的 JSON 归一成人物数组：数组 / {persons:[]} / {documents:[]} / 单个对象。"""
    if isinstance(data, list):
        return [d for d in data if isinstance(d, dict)]
    if isinstance(data, dict):
        for key in ("persons", "documents"):
            if isinstance(data.get(key), list):
                return [d for d in data[key] if isinstance(d, dict)]
        if data.get("id"):
            return [data]
    return []


@app.post("/api/upload")
async def api_upload(file: UploadFile = File(...), _admin=Depends(admin_guard)):
    """收一个 .json 文件，解析后原样返回给后台预览；真正写库仍走 /api/import。"""
    raw = await file.read()
    if len(raw) > MAX_UPLOAD:
        raise HTTPException(413, "文件太大（上限 5MB）")
    try:
        data = json.loads(raw.decode("utf-8-sig"))
    except Exception:                                # noqa: BLE001
        raise HTTPException(400, "不是合法的 UTF-8 JSON")
    docs = normalize_docs(data)
    if not docs:
        raise HTTPException(400, "JSON 里没有人物，需要 [{id,name,nodes:[]}] 或单个对象")
    errors, nodes = [], 0
    for d in docs:
        if not d.get("id") or not d.get("name"):
            errors.append({"doc": str(d.get("id") or d.get("name") or "?")[:40],
                           "reason": "缺少 id 或 name"})
            continue
        nodes += len([n for n in (d.get("nodes") or []) if isinstance(n, dict)])
    return {"filename": file.filename, "persons": len(docs), "nodes": nodes,
            "documents": docs, "errors": errors}


@app.get("/api/export/seed.zip")
def api_export_seed_zip():
    """导出 seed 包：一人一个 json（文件名=人物 id）+ persons_meta.json，解压即可替换 seed/。"""
    persons = db.all_persons_full()
    meta = []
    for p in persons:
        meta.append({"id": p["id"], "name": p["name"], "dynasty": p.get("dynasty", ""),
                     "birth": p.get("birth"), "death": p.get("death"),
                     "life": p.get("life", ""), "bio": p.get("bio", ""),
                     "color": p.get("color", ""), "count": len(p.get("nodes") or [])})
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        for p in persons:
            z.writestr("%s.json" % p["id"], json.dumps(p, ensure_ascii=False, indent=1))
        z.writestr("persons_meta.json", json.dumps(meta, ensure_ascii=False, indent=1))
    return Response(content=buf.getvalue(), media_type="application/zip",
                    headers={"Content-Disposition": 'attachment; filename="seed_pers.zip"'})


@app.get("/api/admin/status")
def api_admin_status(request: Request):
    """后台用：要不要口令、当前是不是本机。只读，不含口令本身。"""
    return {"protected": bool(ADMIN_TOKEN),
            "mode": "token" if ADMIN_TOKEN else "local",
            "local": is_local(request)}


@app.post("/api/admin/verify")
def api_admin_verify(request: Request):
    """后台「验证口令」用：口令错直接 401，对就 200，不写任何数据。"""
    admin_guard(request)
    return {"ok": True}


@app.get("/api/geojson")
def api_geojson(lang: str = "cn"):
    feats = []
    for doc in db.all_persons_full():
        for nd in doc["nodes"]:
            if nd.get("lng") is None or nd.get("lat") is None:
                continue
            if lang == "en":
                props = {
                    "id": nd["id"], "person": doc["name"], "personId": doc["id"],
                    "year": nd.get("year"), "yearText": nd.get("yearText", ""),
                    "place": nd.get("place", ""), "approx": bool(nd.get("approx")),
                    "event": nd.get("event", ""), "poem": nd.get("poem", ""),
                }
            else:
                props = {
                    "人物": doc["name"], "id": nd["id"], "年份": nd.get("year"),
                    "年号": nd.get("yearText", ""), "地名": nd.get("place", ""),
                    "年份存疑": "是" if nd.get("approx") else "",
                    "事件": nd.get("event", ""), "诗作": nd.get("poem", ""),
                }
            feats.append({"type": "Feature",
                          "geometry": {"type": "Point", "coordinates": [nd["lng"], nd["lat"]]},
                          "properties": props})
    return {"type": "FeatureCollection", "features": feats}


# --------------------------------------------------------------------------- #
# 统计（P2）：城市数 / 省份数 / 轨迹里程 / 年份分布 / 诗作数
# --------------------------------------------------------------------------- #
PROVINCES = ["北京", "天津", "上海", "重庆", "河北", "山西", "辽宁", "吉林", "黑龙江",
             "江苏", "浙江", "安徽", "福建", "江西", "山东", "河南", "湖北", "湖南",
             "广东", "广西", "海南", "四川", "贵州", "云南", "陕西", "甘肃", "青海",
             "宁夏", "新疆", "西藏", "内蒙古"]


def load_city_province():
    for path in (os.path.join(BASE, "seed", "city_province.json"),
                 os.path.join(BASE, "..", "数据", "4.对齐后", "city_province.json")):
        if os.path.isfile(path):
            try:
                with open(path, "r", encoding="utf-8-sig") as f:
                    return {str(k): str(v) for k, v in json.load(f).items()}
            except Exception:                        # noqa: BLE001
                pass
    return {}


CITY_PROVINCE = load_city_province()


def place_city(place):
    """'江油（今四川绵阳）' -> '江油'；'绍兴市' -> '绍兴市'。"""
    return re.sub(r"[（(][^）)]*[）)]", "", place or "").strip()


def province_of(place):
    """先看 place 里的省份字样（含括号内的“今XX”），再查 city_province.json。"""
    p = place or ""
    m = re.search(r"[（(]([^）)]*)[）)]", p)
    for text in ((m.group(1) if m else ""), p):
        for prov in PROVINCES:
            if prov in text:
                return prov
    city = place_city(p)
    for key in (city, re.sub(r"[市县区府州镇]$", "", city)):
        if key and key in CITY_PROVINCE:
            return CITY_PROVINCE[key]
    return None


def haversine(a, b):
    r = 6371.0088
    lng1, lat1, lng2, lat2 = a[0], a[1], b[0], b[1]
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp, dl = p2 - p1, math.radians(lng2 - lng1)
    h = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * r * math.asin(math.sqrt(h))


def person_stats(doc):
    nodes = [n for n in (doc.get("nodes") or []) if isinstance(n, dict)]
    places = []
    for n in nodes:
        if n.get("place"):
            places.append(n["place"])
    provs = {p for p in (province_of(p) for p in places) if p}
    pts = [(n["lng"], n["lat"]) for n in nodes
           if n.get("lng") is not None and n.get("lat") is not None]
    dist = sum(haversine(a, b) for a, b in zip(pts, pts[1:]))
    decades = {}
    for n in nodes:
        y = n.get("year")
        if isinstance(y, int):
            decades[(y // 10) * 10] = decades.get((y // 10) * 10, 0) + 1
    years = [n["year"] for n in nodes if isinstance(n.get("year"), int)]
    return {
        "nodes": len(nodes),
        "cities": len(set(places)),
        "provinces": len(provs),
        "distance_km": round(dist, 1),
        "poems": sum(1 for n in nodes if (n.get("poem") or "").strip()),
        "yearFrom": min(years) if years else None,
        "yearTo": max(years) if years else None,
        "decades": [{"decade": d, "count": decades[d]} for d in sorted(decades)],
    }


@app.get("/api/stats")
def api_stats(person: str = ""):
    if person:
        doc = db.get_person(person)
        if not doc:                                  # 也允许直接传姓名
            for row in db.list_persons("name"):
                if row["name"] == person:
                    doc = db.get_person(row["id"])
                    break
        if not doc:
            raise HTTPException(404, "人物不存在：%s" % person)
        out = person_stats(doc)
        out.update({"person": doc["id"], "name": doc["name"], "persons": 1})
        return out
    docs = db.all_persons_full()
    merged = {"nodes": 0, "cities": 0, "provinces": 0, "distance_km": 0.0, "poems": 0,
              "yearFrom": None, "yearTo": None, "decades": {}, "persons": len(docs)}
    places, provs, rows = set(), set(), []
    for d in docs:
        s = person_stats(d)
        places.update(n["place"] for n in (d.get("nodes") or []) if isinstance(n, dict) and n.get("place"))
        provs.update(p for p in (province_of(n["place"]) for n in (d.get("nodes") or [])
                                 if isinstance(n, dict) and n.get("place")) if p)
        merged["nodes"] += s["nodes"]
        merged["distance_km"] += s["distance_km"]
        merged["poems"] += s["poems"]
        for k in ("yearFrom", "yearTo"):
            if s[k] is not None:
                merged[k] = s[k] if merged[k] is None else (
                    min(merged[k], s[k]) if k == "yearFrom" else max(merged[k], s[k]))
        for item in s["decades"]:
            merged["decades"][item["decade"]] = merged["decades"].get(item["decade"], 0) + item["count"]
        rows.append({"id": d["id"], "name": d["name"], "color": d.get("color", ""), **s})
    merged["cities"] = len(places)
    merged["provinces"] = len(provs)
    merged["distance_km"] = round(merged["distance_km"], 1)
    merged["decades"] = [{"decade": k, "count": merged["decades"][k]} for k in sorted(merged["decades"])]
    merged["byPerson"] = rows
    return merged


# --------------------------------------------------------------------------- #
# AI 问答
# --------------------------------------------------------------------------- #
DEEPSEEK_URL = "https://api.deepseek.com/chat/completions"
DEEPSEEK_MODEL = os.environ.get("DEEPSEEK_MODEL", "deepseek-chat")
SYSTEM_PROMPT = (
    "你是「诗路重现」项目的讲解助手，语气平实、像给同学讲题。"
    "只能依据下面提供的资料回答：资料里没有的内容，直接说“这部分资料里没有”，不要编造年份、地名或诗文。"
    "回答用中文，控制在 300 字以内；涉及年份时写明年号，例如“开元十二年（724）”。"
)


def ssl_context():
    try:
        import certifi
        return ssl.create_default_context(cafile=certifi.where())
    except Exception:                                # noqa: BLE001
        try:
            return ssl.create_default_context()
        except Exception:                            # noqa: BLE001
            return None


def api_key():
    key = (os.environ.get("DEEPSEEK_API_KEY") or "").strip()
    if key:
        return key, "环境变量 DEEPSEEK_API_KEY"
    for name in ("config.local.json", "config.json"):
        path = os.path.join(BASE, name)
        if os.path.isfile(path):
            try:
                with open(path, "r", encoding="utf-8") as f:
                    k = (json.load(f).get("deepseek_api_key") or "").strip()
                if k:
                    return k, name
            except Exception:                        # noqa: BLE001
                pass
    return "", ""


def build_context(pid):
    lines = []
    if pid:
        doc = db.get_person(pid)
        if doc:
            lines.append("【当前诗人】%s（%s，%s）" % (doc["name"], doc["dynasty"] or "朝代不详",
                                                  doc["life"] or "生卒不详"))
            if doc["bio"]:
                lines.append("简介：%s" % doc["bio"])
            lines.append("行迹共 %d 个节点（按年份排序）：" % len(doc["nodes"]))
            for n in doc["nodes"]:
                seg = "%s（%s）%s：%s" % (n.get("yearText") or "", n.get("year") or 0,
                                         n.get("place") or "", n.get("event") or "")
                if n.get("approx"):
                    seg += "［年份存疑］"
                if n.get("poem"):
                    seg += " ｜相关诗文：%s" % n["poem"]
                lines.append("- " + seg)
    if not lines:
        docs = db.list_persons("name")
        total = sum(d["count"] for d in docs)
        lines.append("【本项目收录 %d 位诗人，行迹共 %d 个节点】" % (len(docs), total))
        for d in docs:
            lines.append("- %s（%s，%s），%d 个节点" % (d["name"], d["dynasty"] or "",
                                                   d["life"] or "", d["count"]))
        lines.append("用户没有指定诗人，如需具体行迹请让他先选一位。")
    return "\n".join(lines)


@app.get("/api/ask/quota")
def ask_quota(request: Request):
    ip = client_ip(request)
    return {"remaining": quota_left(ip), "perMinute": ASK_LIMIT_PER_MIN,
            "perDay": ASK_LIMIT_PER_DAY,
            "localhost": ip in ("127.0.0.1", "::1", "testclient")}


@app.post("/api/ask")
def ask(payload: dict = Body(...), request: Request = None):
    question = (payload.get("question") or "").strip()
    if not question:
        raise HTTPException(400, "缺少 question")
    person = (payload.get("person") or "").strip() or None
    ip = client_ip(request)

    is_mock = bool(payload.get("mock"))
    key, source = "", ""
    if not is_mock:
        key, source = api_key()
        if not key:
            return JSONResponse(status_code=503, content={
                "error": "未配置 DeepSeek API Key",
                "hint": "在部署平台的环境变量里设置 DEEPSEEK_API_KEY 即可（本机也可写 config.local.json）。"})

    ok, msg, left = quota_check(ip)
    if not ok:
        return JSONResponse(status_code=429, content={"error": msg, "remaining": 0})

    ck = "%s|%s" % (person or "-", "".join(question.split()).lower())
    with _lock:
        hit = _cache.get(ck)
        if hit and hit[0] > time.time():
            out = dict(hit[1])
            out["cached"] = True
            out["remaining"] = left
            return out
    context = build_context(person)

    if is_mock:
        answer = "【离线模式·未调用模型】\n%s\n\n资料共 %d 行。" % (
            context.splitlines()[0], len(context.splitlines()))
        with _lock:
            _cache[ck] = (time.time() + ASK_CACHE_TTL, {"answer": answer, "mock": True})
        return {"answer": answer, "mock": True, "remaining": left}

    body = {
        "model": DEEPSEEK_MODEL,
        "messages": [{"role": "system", "content": SYSTEM_PROMPT},
                     {"role": "user", "content": "%s\n\n【用户问题】%s" % (context, question)}],
        "temperature": 0.3, "max_tokens": 800, "stream": False,
    }
    req = urllib.request.Request(
        DEEPSEEK_URL, data=json.dumps(body, ensure_ascii=False).encode("utf-8"),
        headers={"Content-Type": "application/json", "Authorization": "Bearer " + key}, method="POST")
    try:
        kwargs = {"timeout": 60}
        ctx = ssl_context()
        if ctx is not None:
            kwargs["context"] = ctx
        with urllib.request.urlopen(req, **kwargs) as resp:
            data = json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", "replace")[:400]
        return JSONResponse(status_code=502, content={"error": "DeepSeek 接口返回 %d" % exc.code,
                                                      "detail": detail})
    except urllib.error.URLError as exc:
        return JSONResponse(status_code=504, content={"error": "连不上 DeepSeek：%s" % exc.reason})
    except Exception as exc:                          # noqa: BLE001
        return JSONResponse(status_code=502, content={"error": "调用失败：%s" % exc})

    try:
        answer = data["choices"][0]["message"]["content"]
    except (KeyError, IndexError):
        return JSONResponse(status_code=502, content={"error": "DeepSeek 返回格式异常"})

    out = {"answer": answer, "model": data.get("model", DEEPSEEK_MODEL),
           "usage": data.get("usage"), "person": person, "keySource": source}
    with _lock:
        _cache[ck] = (time.time() + ASK_CACHE_TTL, out)
    out = dict(out)
    out["remaining"] = left
    return out


@app.get("/api/config/status")
def config_status():
    key, source = api_key()
    return {"configured": bool(key), "source": source}


@app.post("/api/config/key")
def config_key(payload: dict = Body(...), request: Request = None):
    ip = client_ip(request)
    if ip not in ("127.0.0.1", "::1", "testclient"):
        raise HTTPException(403, "只允许本机配置 Key；线上请用环境变量 DEEPSEEK_API_KEY")
    key = (payload.get("key") or "").strip()
    if not key.startswith("sk-") or len(key) < 20:
        raise HTTPException(400, "Key 格式不对")
    with open(os.path.join(BASE, "config.local.json"), "w", encoding="utf-8") as f:
        json.dump({"deepseek_api_key": key}, f, ensure_ascii=False, indent=1)
    return {"saved": True, "configured": True}


# --------------------------------------------------------------------------- #
# 静态页面（放最后，别挡住 /api）
# --------------------------------------------------------------------------- #
if STATIC_DIR:
    @app.get("/")
    def index():
        return FileResponse(os.path.join(STATIC_DIR, "index.html"))

    app.mount("/", StaticFiles(directory=STATIC_DIR, html=True), name="static")
