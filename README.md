# 诗路重现 · FastAPI + SQLite 后端

**20 位诗人 / 246 个节点**，FastAPI + SQLite，静态页同源托管。

## 前端体验（P2）

| 功能 | 说明 |
| --- | --- |
| 统计面板 | 左侧「数据统计」：节点数、城市数（去重地名）、省份数、轨迹里程（相邻点 haversine 求和）、诗作数 + 按十年的分布条形；切诗人即时更新（`GET /api/stats`） |
| 点位分享 | 任意点弹窗里「复制这个点的链接」→ `?node=libai-05`，对方打开直接定位并弹出该点，时间轴同步高亮 |
| 移动端 | `@media (max-width:720px)`：控制条换行、时间轴变底部抽屉（点标题开合）、简介卡限高；390×844 下无横向滚动 |
| 配色 | 以接口 `color` 为准（后台改色即时生效），静态表只兜底 |

## 管理后台（P0）

打开 **http://127.0.0.1:8000/admin.html**（线上是 `https://你的域名/admin.html`），不用改代码、不用改 JSON：

| 能干什么 | 说明 |
| --- | --- |
| 人物 | 左侧列表可搜索（姓名/朝代），新增、编辑（朝代/生卒/简介/配色）、删除（二次确认） |
| 节点 | 选人物后按年份列出，可搜事件/地名/诗作，新增、编辑（年份/年号/地名/事件/诗作/经纬度/年份存疑）、删除 |
| 批量导入 | 拖入 `.json`（`[{id,name,...,nodes:[]}]` 或单个对象）→ 预览人数/节点数 → 选 `skip`/`overwrite` → 提交 |
| 导出 | `persons.json`（全部人物）或 `seed.zip`（一人一个文件 + persons_meta.json，解压即可替换 `seed/`） |

**口令保护**：写操作（POST/PUT/DELETE/import）都要带 `X-Admin-Token` 请求头。

- 线上：Render 环境变量里加 `ADMIN_TOKEN`（自己定一个强口令，别用 123456）；
- 本机：没配 `ADMIN_TOKEN` 时只放行 127.0.0.1，方便本地开发；
- 只读接口（GET）不校验，公开地图页照常访问；
- 后台第一次打开会弹口令框，验证通过后存在浏览器 `localStorage`，右上「口令」可改可清。

## 本地跑

```bat
:: 双击或用命令行
D:\supermapiobjectenvgpu2026\conda\python.exe -m uvicorn app:app --host 127.0.0.1 --port 8000
```

打开 http://127.0.0.1:8000/ 就是网页（`static/index.html`）。

首次启动会把 `seed/pers/*.json` 灌进 `data/poets.db`，控制台打印 `[seed] 已导入 20 个人物`。

## 数据持久化（P1，免费层必做）

免费层 SQLite 重启会清空，所以录完数据要回写 seed：

```bat
:: 1) 后台「导出 seed 包」，或直接跑这个脚本（会覆盖 seed/pers/）
python sync_seed.py https://poets-journey.onrender.com

:: 2) 提交 + 推送，Render 重新部署时按 seed 重新灌库
git add -A && git commit -m "data: 后台录的数据回写 seed" && git push
```

## 目录

```
后端-fastapi/
├─ app.py             接口（人物/节点 CRUD、导入/上传、导出、GeoJSON、统计、AI 代理、后台口令）
├─ db.py              SQLite 存取
├─ admin_test.py      后台自测（30 项）
├─ test_api.py        接口自测（21 项）
├─ sync_seed.py       P1：把线上数据拉回来覆盖 seed/pers
├─ requirements.txt
├─ render.yaml        Render 一键部署配置
├─ Procfile           Railway / Heroku 用
├─ static/index.html  前端页面（同源托管，免 CORS）
├─ static/admin.html  数据后台（P0）
├─ seed/pers/*.json   20 个人物的种子数据
├─ seed/city_province.json  城市→省份（统计面板算省份数用）
└─ data/poets.db      运行时生成（已在 .gitignore）
```

## 接口

| 方法 | 路径 |
| --- | --- |
| GET | `/api/health` `/api/persons` `/api/persons/{id}` `/api/persons/{id}/nodes` |
| POST | `/api/persons` `/api/persons/{id}/nodes` `/api/import` `/api/upload` |
| PUT | `/api/persons/{id}` `/api/nodes/{id}` |
| DELETE | `/api/persons/{id}` `/api/nodes/{id}` |
| GET | `/api/geojson?lang=cn` `/api/export` `/api/export/seed.zip` `/api/stats?person=` `/api/ask/quota` |
| GET | `/api/admin/status`（要不要口令，只读） |
| POST | `/api/ask`（DeepSeek 代理）、`/api/admin/verify`（验证口令） |

导入判重：`{"strategy":"skip"|"overwrite"}`，按 id 判断。写操作要带 `X-Admin-Token`；`/api/stats` 的 `person` 支持传 id 或姓名。

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
python admin_test.py http://127.0.0.1:8000 你的ADMIN_TOKEN
```

- `test_api.py`：21 项（健康检查 20 人 246 节点、GeoJSON 246 点、李白 19 节点、统计接口、增改删人物/节点、409 判重、skip/overwrite、额度、空问题 400、mock 问答）；
- `admin_test.py`：30 项（后台页面、`/api/admin/status`、口令 401/200、CRUD、导入三种策略、`/api/upload` 上传解析、`seed.zip` 结构）；带口令参数时按线上模式跑，不带则按本机免口令模式跑。
