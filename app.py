# -*- coding: utf-8 -*-
"""诗路重现 · FastAPI + SQLite 后端

接口与 Flask 版完全一致（前端不用改）：
    /api/health  /api/persons  /api/persons/{id}  /api/nodes/{id}
    /api/import  /api/export  /api/geojson  /api/ask  /api/ask/quota
    /api/config/status  /api/config/key

本地跑：  uvicorn app:app --reload --port 8000
线上跑：  uvicorn app:app --host 0.0.0.0 --port $PORT
"""
import json
import os
import ssl
import threading
import time
import urllib.error
import urllib.request

from fastapi import Body, FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, JSONResponse
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
# 基础接口
# --------------------------------------------------------------------------- #
@app.get("/api/health")
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
def api_person_create(payload: dict = Body(...)):
    if not payload.get("id") or not payload.get("name"):
        raise HTTPException(400, "缺少字段 id 或 name")
    if db.get_person(payload["id"], with_nodes=False):
        raise HTTPException(409, "id 已存在：%s" % payload["id"])
    db.upsert_person(payload, overwrite=True)
    return db.get_person(payload["id"])


@app.put("/api/persons/{pid}")
def api_person_update(pid: str, payload: dict = Body(...)):
    if not db.get_person(pid, with_nodes=False):
        raise HTTPException(404, "人物不存在：%s" % pid)
    return db.update_person(pid, payload)


@app.delete("/api/persons/{pid}")
def api_person_delete(pid: str):
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
def api_node_create(pid: str, payload: dict = Body(...)):
    if not db.get_person(pid, with_nodes=False):
        raise HTTPException(404, "人物不存在：%s" % pid)
    if payload.get("year") is None:
        raise HTTPException(400, "缺少字段 year")
    return db.add_node(pid, payload)


@app.put("/api/nodes/{node_id}")
def api_node_update(node_id: str, payload: dict = Body(...)):
    if not db.find_node(node_id):
        raise HTTPException(404, "节点不存在：%s" % node_id)
    return db.update_node(node_id, payload)


@app.delete("/api/nodes/{node_id}")
def api_node_delete(node_id: str):
    if not db.delete_node(node_id):
        raise HTTPException(404, "节点不存在：%s" % node_id)
    return {"deleted": node_id}


@app.post("/api/import")
def api_import(payload: dict = Body(...)):
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
