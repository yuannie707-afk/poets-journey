# 诗路重现 · FastAPI + SQLite 后端

按选型表重写的一版后端：**FastAPI + SQLite**，接口与旧 Flask 版**完全一致**，前端一行都不用改。

## 本地跑

```bat
:: 双击或用命令行
D:\supermapiobjectenvgpu2026\conda\python.exe -m uvicorn app:app --host 127.0.0.1 --port 8000
```

打开 http://127.0.0.1:8000/ 就是网页（`static/index.html`）。

首次启动会把 `seed/pers/*.json` 灌进 `data/poets.db`，控制台打印 `[seed] 已导入 12 个人物`。

## 目录

```
后端-fastapi/
├─ app.py             接口（人物/节点 CRUD、导入、GeoJSON、AI 代理）
├─ db.py              SQLite 存取
├─ requirements.txt
├─ render.yaml        Render 一键部署配置
├─ Procfile           Railway / Heroku 用
├─ static/index.html  前端页面（同源托管，免 CORS）
├─ seed/pers/*.json   12 个人物的种子数据
└─ data/poets.db      运行时生成（已在 .gitignore）
```

## 接口（与 Flask 版对齐）

| 方法 | 路径 |
| --- | --- |
| GET | `/api/health` `/api/persons` `/api/persons/{id}` `/api/persons/{id}/nodes` |
| POST | `/api/persons` `/api/persons/{id}/nodes` `/api/import` |
| PUT | `/api/persons/{id}` `/api/nodes/{id}` |
| DELETE | `/api/persons/{id}` `/api/nodes/{id}` |
| GET | `/api/geojson?lang=cn` `/api/export` `/api/ask/quota` |
| POST | `/api/ask`（DeepSeek 代理） |

导入判重：`{"strategy":"skip"|"overwrite"}`，按 id 判断。

## 部署到 Render（免费层）

### 1. 传代码到 GitHub

```bat
cd /d D:\20261011超图校\后端-fastapi
git init
git add .
git commit -m "poets backend: fastapi + sqlite"
:: 先在 github.com 新建一个空仓库，比如 poets-journey
git remote add origin https://github.com/你的账号/poets-journey.git
git branch -M main
git push -u origin main
```

### 2. Render 建服务

1. 登录 [render.com](https://render.com)（用 GitHub 账号登录）；
2. **New → Web Service** → 选刚才的仓库；
3. Runtime 选 **Python**，Render 会自动读 `render.yaml`；
   若手动填：
   - Build Command：`pip install -r requirements.txt`
   - Start Command：`uvicorn app:app --host 0.0.0.0 --port $PORT`
4. **Environment → 添加** `DEEPSEEK_API_KEY` = 你的 key（**不要**写进代码）；
5. 点 Deploy，等 2–3 分钟，得到 `https://poets-journey-xxxx.onrender.com`。

这个地址就是**永久公网网址**，别人直接打开就能用，不用你电脑开着。

### 3. 免费层的两个坑

| 坑 | 说明 | 对策 |
| --- | --- | --- |
| 15 分钟无访问会休眠 | 再次打开要等 30–60 秒冷启动 | 演示前先自己点一下“唤醒”；或升级付费 |
| SQLite 文件不持久 | 重新部署 / 重启会重置 | 启动时会自动重新灌 `seed/pers`，所以数据不丢；你自己通过接口新增的数据会丢。要持久就加 Render Disk（挂 `/data`，把 `DB_PATH` 指过去）或换 Postgres |

### 4. 用 Railway 也行

```bat
npm i -g @railway/cli
railway login
railway init
railway up
```

环境变量同上；Railway 会读 `Procfile`。

## 自测

```bat
python test_api.py http://127.0.0.1:8000
```

17 项：健康检查、155 点、李白 19 节点、增改删人物/节点、409 判重、skip/overwrite、额度、空问题 400、mock 问答。
