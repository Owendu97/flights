# 机票历史价参考

> FastAPI + SQLite 真后台 + GitHub Actions 7×24 cron 抓携程价格日历。

## 项目结构

```
flights/
├── backend/
│   ├── __init__.py
│   ├── app.py            # FastAPI 入口 + 静态文件 mount
│   ├── db.py             # SQLite schema (meta_routes, price_calendar, flight_snapshot, scrape_log)
│   ├── seed.py           # 把 6routes_v4.json 一次性导入
│   ├── routes.py         # /api/health, /api/routes, /api/snapshot
│   ├── scraper.py        # Playwright 抓取（GitHub Actions cron 主程序）
│   ├── extract.js        # 携程 SPA DOM 提取
│   ├── requirements.txt  # playwright>=1.40
│   ├── data.sqlite3      # 数据库（commit 到 repo，cron 读写）
│   └── static/
│       └── index.html    # 前端 (v3 mockup，套 Linear 设计)
├── data/
│   └── 6routes_v4.json   # 真实抓取数据（seed 用）
├── .github/
│   └── workflows/
│       └── scrape.yml    # GitHub Actions 定时抓取
├── PROJECT_BRIEF.md      # 项目档案（产品需求/数据模型/6 条线实测）
├── SCRAPE_DEBUG.md       # 抓取调试笔记（TUN VPN 出口 IP 诊断）
├── PLAN.md               # 早期 MVP 规划
├── SCRAPE_TEST_*.md      # V1-V3 抓取测试报告
├── README.md             # ← 你正在看的
└── mockup.html           # v3 mockup 单文件（早期版本，server-less demo 用）
```

## 启动

```bash
# 1) 安装依赖
pip3 install --user fastapi 'uvicorn[standard]' playwright

# 2) 安装 Chromium for Playwright
python3 -m playwright install --with-deps chromium

# 3) (可选) 导入初始真实数据
python3 backend/seed.py --replace

# 4) 起后端
python3 -m uvicorn backend.app:app --app-dir /Users/owendu/Minimax/flights --host 0.0.0.0 --port 8765
```

打开 http://127.0.0.1:8765/ 看前端。

## API

| 端点 | 方法 | 说明 |
|---|---|---|
| `/` | GET | 服务前端 index.html |
| `/api/health` | GET | 服务健康 + DB 行数 |
| `/api/routes` | GET | 列出已 seeded 的 (orig, dest) 组合 |
| `/api/snapshot` | GET | `?orig=PEK&dest=PVG&date=2026-08-10` → 7 天日历 + 当日航班 |

## 数据模型

```sql
meta_routes(route PK, orig, dest, note, depdate, is_real, updated_at)

price_calendar(id, route, flight_date, depdate_anchor, days_before_departure,
               price, is_lowest, raw_text,
               UNIQUE(route, flight_date, depdate_anchor))

flight_snapshot(id, route, flight_date, flight_no, airline, aircraft,
                dep_airport, arr_airport, dep_time, arr_time,
                stops, time_bucket, price, discount_rate, inferred_full_price,
                is_main_flight, is_shared, actual_operator, bucket_rank,
                UNIQUE(route, flight_date, flight_no))

scrape_log(id, route, depdate_anchor, started_at, finished_at,
           flights_count, calendar_count, status)
```

### 5 时段抓取规则（业务层）

- **每时段 1 个最便宜航班**
- **特便宜时段（比次便宜便宜 ≥30%）**：给 2 个最便宜（rank=1, 2）
- 共享航班（`is_shared=1`）不入库
- 数据存储逻辑见 `backend/scraper.py:write_route()`

## 7×24 抓取（GitHub Actions cron）

`.github/workflows/scrape.yml` 每天 UTC 18:00 跑一次：

1. checkout repo
2. setup Python + `pip install -r backend/requirements.txt`
3. `playwright install --with-deps chromium`
4. `python -m backend.scraper --lookahead 7 --delay 2`
5. commit `backend/data.sqlite3` 回 repo
6. upload artifact (90 天留存)

### 启用 GitHub Actions cron

```bash
# 1) 在 GitHub 上建一个空 repo（公开免费 Actions，私有要 Pro）
# 2) 推代码
cd /Users/owendu/Minimax/flights
git init
git add .
git commit -m "init: flights mvp with scraper + actions cron"
git remote add origin git@github.com:<you>/<repo>.git
git branch -M main
git push -u origin main

# 3) 在 GitHub repo → Settings → Actions → General → Workflow permissions
#    选 "Read and write permissions" (commit 需要)
# 4) Actions 会在 UTC 18:00 自动跑；也可手动 trigger
```

## 已知局限

- **TUN 模式下 VPN 出口是国外 IP**（96.44.158.61 US HostPapa），携程反爬可能间歇触发；scraper 写了 cookie warmup + 15s hydration + 2-3 retry 缓解
- **extract.js 偶尔漏抓 1 个航班**（SPA 渲染时序问题），不影响业务规则
- **数据库行数会随时间增长**（每天 6 routes × 8 dates = 48 new snapshots，~10KB/天），1 年后约 4MB
- **calendar 数据天然共享**：7 天日历 = 7 个 depdate_anchor × 同一日历 → 用 UNIQUE(route, flight_date, depdate_anchor) 防止重复

## License

MIT（项目档案许可）
