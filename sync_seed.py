# -*- coding: utf-8 -*-
"""P1 数据持久化：把线上/本地后台的数据拉回来，覆盖 seed/pers/。

免费层 SQLite 重启就清空，所以流程是：
    后台录完数据 → 本脚本拉 seed 包 → git add/commit/push → Render 重新部署时自动灌回

用法：
    python sync_seed.py http://127.0.0.1:8000
    python sync_seed.py https://poets-journey.onrender.com
    python sync_seed.py https://poets-journey.onrender.com --keep-extra   # 不删 seed 里多出来的旧文件

拉完会打印人物数/节点数，并提示下一步该 git 做什么。
"""
import argparse
import io
import json
import os
import sys
import urllib.request
import zipfile

BASE = os.path.dirname(os.path.abspath(__file__))
SEED = os.path.join(BASE, "seed")
PERS = os.path.join(SEED, "pers")


def fetch_zip(url):
    req = urllib.request.Request(url.rstrip("/") + "/api/export/seed.zip",
                                 headers={"User-Agent": "poets-sync-seed"})
    with urllib.request.urlopen(req, timeout=120) as r:
        return r.read()


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("base", help="后端地址，如 https://poets-journey.onrender.com")
    ap.add_argument("--keep-extra", action="store_true", help="不删除 seed/pers 里多出来的旧 json")
    args = ap.parse_args()

    print("拉取 %s/api/export/seed.zip ..." % args.base.rstrip("/"))
    data = fetch_zip(args.base)
    z = zipfile.ZipFile(io.BytesIO(data))
    names = [n for n in z.namelist() if n.endswith(".json")]
    if not names:
        sys.exit("压缩包里没有 json，先确认后台能不能正常导出")

    os.makedirs(PERS, exist_ok=True)
    if not args.keep_extra:
        for old in os.listdir(PERS):
            if old.endswith(".json"):
                os.remove(os.path.join(PERS, old))

    persons = 0
    nodes = 0
    for n in names:
        body = z.read(n)
        if n == "persons_meta.json":
            target = os.path.join(SEED, "persons_meta.json")
        else:
            target = os.path.join(PERS, n)
        with open(target, "wb") as f:
            f.write(body)
        if n != "persons_meta.json":
            doc = json.loads(body.decode("utf-8"))
            persons += 1
            nodes += len(doc.get("nodes") or [])

    print("已覆盖 seed/：%d 人 / %d 节点" % (persons, nodes))
    print("\n下一步（在仓库目录里）：")
    print("    git add -A")
    print('    git commit -m "data: 后台录的数据回写 seed"')
    print("    git push")
    print("推送后 Render 会自动重新部署，启动时按 seed 重新灌库。")


if __name__ == "__main__":
    main()
