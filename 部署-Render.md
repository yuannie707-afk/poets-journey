# 上线到 Render（免费层）+ UptimeRobot 保活

目标：拿到一个**永久公网地址**，别人不用你开机也能打开；免费层 15 分钟无访问会睡，用保活解决。

---

## 第 0 步：确认代码能跑（本机已验过）

```bat
cd /d D:\20261011超图校\后端-fastapi
D:\supermapiobjectenvgpu2026\conda\python.exe -m uvicorn app:app --port 8000
```

打开 http://127.0.0.1:8000/ → 能看到地图、155 个点、AI 能答，再往下做。

---

## 第 1 步：推到 GitHub

1. 浏览器打开 github.com → 右上 **+ → New repository**
   - 名字：`poets-journey`
   - **不要**勾 “Add a README file”（否则 push 会冲突）
   - Create repository
2. 复制它给的地址，然后：

```bat
cd /d D:\20261011超图校\后端-fastapi
git remote add origin https://github.com/你的账号/poets-journey.git
git branch -M main
git push -u origin main
```

如果提示要登录：GitHub 已不支持密码，用 **Personal Access Token** 当密码（Settings → Developer settings → Tokens (classic) → Generate new token，勾 `repo`）。

推完刷新仓库页面，应能看到 `app.py / db.py / render.yaml / static/ / seed/`。

---

## 第 2 步：Render 建服务（约 3 分钟）

1. 打开 [render.com](https://render.com) → **Get Started** → 用 **GitHub 账号**登录并授权；
2. 控制台 → **New +** → **Web Service** → 选 `poets-journey` 仓库 → Connect；
3. 它应该自动读到 `render.yaml`（Region=Singapore、Plan=Free、Start Command 都对）。**如果没读到**，手填：
   - Runtime：`Python 3`
   - Build Command：`pip install -r requirements.txt`
   - Start Command：`uvicorn app:app --host 0.0.0.0 --port $PORT`
   - Instance Type：`Free`
4. 往下找到 **Environment Variables** → Add：
   - Key：`DEEPSEEK_API_KEY`
   - Value：你的 `sk-...`（**只填这里，别写进代码**）
5. 点 **Create Web Service**，等 2–3 分钟，日志出现 `Uvicorn running on http://0.0.0.0:...` 就成功了；
6. 页面顶部拿到地址：`https://poets-journey-xxxx.onrender.com`。

打开这个地址 → 应该直接是地图页。

---

## 第 3 步：UptimeRobot 保活（免费，5 分钟一次）

不保活的话，15 分钟没人访问就休眠，下一个人要等 30–60 秒。

1. 打开 [uptimerobot.com](https://uptimerobot.com) → 注册（免费）；
2. **Add New Monitor**：
   - Monitor Type：`HTTP(s)`
   - Friendly Name：`Poets Journey`
   - URL：`https://poets-journey-xxxx.onrender.com/api/health`
   - Monitoring Interval：**5 minutes**（免费版可选）
3. Create Monitor。

之后它每 5 分钟访问一次 `/api/health`，服务就不会休眠。Render 免费额度 750 小时/月，24×31 ≈ 744 小时，刚好卡在额度内。

> 小技巧：把监控的 “Alert Contacts” 留空也行，它的作用是“有人访问”，不是真要报警。

---

## 第 4 步：验收（我帮你看也行）

```bat
curl https://你的地址/api/health
:: 期望：{"ok":true,"persons":12,"nodes":155,...}

curl https://你的地址/api/geojson
:: 期望 155 个 Feature

:: 连续点 7 次 AI，第 7 次应返回 429（限流生效）
```

---

## 常见问题

| 现象 | 原因 | 处理 |
| --- | --- | --- |
| 部署失败，日志 `No module named fastapi` | 没走 requirements | 确认 Build Command 是 `pip install -r requirements.txt` |
| 打开是 502 / Bad Gateway | 没绑 `$PORT` | Start Command 必须是 `... --port $PORT` |
| 页面白屏、控制台 `/api/geojson` 404 | 静态目录没找到 | 确认仓库里有 `static/index.html` |
| 数据变回 12 人 | 免费层重启，SQLite 重置 | 正常，启动会自动从 `seed/pers` 重灌；想保留新数据要加 Disk |
| 第一次访问慢 40 秒 | 已休眠 | 等 UptimeRobot 生效后就快 |
