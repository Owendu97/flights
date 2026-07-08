# 抓取失败诊断与下一步

> 2026-07-08 调试过程记录。每条 claim 都有对应证据。

## 核心结论

**scraper 在 TUN VPN 模式下出口 IP 是 `96.44.158.61` (US Seattle, HostPapa)，
携程见到国外 IP 触发 whaleguard block。**

## 已验证的事实

### 1. 出口 IP（同一台机器、不同协议，三路都验证）

| 探测方式 | IP | 来源 |
|---|---|---|
| `curl ifconfig.me` | 96.44.158.61 | HostPapa, US/Seattle |
| Playwright Chromium → ipify | 96.44.158.61 | HostPapa, US/Seattle |
| Playwright Chromium → ipinfo | org=AS36352 HostPapa, country=US, city=Seattle | 数据中心 IP |

**TUN 模式让所有出站流量走 VPN 出口**——这是 VPN 配置文件里 `tun` 设备的特性。

### 2. 携程反爬 (whaleguard) 行为

- 没 warmup 时 body = `"whaleguard block"` (17 字符)
- 有 warmup + cookie copy + 15s wait 时 body = 1607 字符，正常数据
- 之前抓 6routes_v4.json 用的 mavis MCP playwright 也跑在同一 host，
  理论上 IP 也一样——所以"反爬弱"的旧记忆可能不准确

### 3. 现在 scraper 状态

- `python3 -m backend.scraper --routes PEK-PVG --anchor-start 2026-08-08`
- 单点：cal=7, flights=3 ✅ (cookie copy + 15s wait 路径走通了)
- 多 route × 多 depdate 没跑过

## 三种"之前能抓"假设

| 假设 | 验证方法 | 概率 |
|---|---|---|
| A. 之前 VPN 没开（ISP 国内 IP） | 问 user：抓 6routes_v4.json 时 VPN 状态 | 高 |
| B. 携程反爬规则最近升级 | 看 6routes_v4.json 抓取时间（v4 2026-07-08）vs 反爬升级时间 | 中 |
| C. mavis MCP playwright 用了 stealth 注入 | 翻 mavis 源码 / 跑一次对比 | 中 |

## 5 时段航班抓取规则（待 user 确认）

> user 原话：「每一个时段至少要有一个最低航班价格，如果某个时间段特别低可以爬取该时段最便宜的两个航班信息。不是把最低的 5 个价格爬取后分到对应时间段」

两种理解：
- **A. scraper 不变**：extract.js 把所有航班都爬下来（每时段 5-10 个），
  前端 mockup 的 computeRoute() 取每时段最便宜的展示。
  → 后端数据库存储完整原始数据，前端自由分析。
- **B. scraper 改造**：爬的时候按"每时段 1 个最便宜 + 特便宜时段 2 个"裁剪
  → 数据库小一点，但失去后续分析价值

我倾向 **A**（保留原始数据 + 业务规则放前端），但等 user 拍板。

## 下一步候选（按优先级）

### 0. 必做：等 user 回 5 时段规则 + IP 假设确认

### 1. 短期方案：scraper 在 user 机器上跑（接受 TUN VPN 的 IP）

- 现状：cookie warmup + 15s wait 能拿 7+3，**能跑**
- 风险：长期跑 7×24 同一 IP 触发 IP 信誉 ban
- 优势：免服务器 / 免代理费用

### 2. 短期方案 B：scraper 关 VPN 试一次

```bash
# 关 VPN
scutil --nc list  # 找 VPN service
# disconnect
python3 -m backend.scraper --routes PEK-PVG --anchor-start 2026-08-08
```

如果 ISP 出口是国内家庭宽带 IP，应该能直接过 whaleguard。
**这会验证假设 A**。

### 3. 中期方案：scraper 走 mavis MCP playwright

mavis daemon 自带的 playwright 可能有 stealth 注入，比本地 headless-shell 更
难被识别。改动：scraper 用 mavis 跑 + 后台把抓取结果存到 SQLite。

需要 mavis-team mode 改 scraper。

### 4. 长期方案：scraper 部署到 mavis 用户的另一台国内 IP 机器

- Mac mini 24×7 跑（家里）
- 阿里云轻量（~60/月，国内 IP）
- 老笔记本 + 内网穿透

## 关键文件

- `/Users/owendu/Minimax/flights/backend/scraper.py` — 抓取主程序
- `/Users/owendu/Minimax/flights/backend/extract.js` — 携程 DOM 提取
- `/Users/owendu/Minimax/flights/.github/workflows/scrape.yml` — CI cron
- `/Users/owendu/Minimax/flights/PROJECT_BRIEF.md` — 项目档案

## 状态摘要

- scraper.py：能跑（单点验证 7+3）
- extract.js：OK，未改
- 6routes_v4.json 真实数据：已 seeded 到 SQLite
- 后端 FastAPI：跑通，3 个 endpoint
- 前端 mockup：跑通，3 个状态截图
- GitHub Actions workflow：写完，没在 CI 跑过

**核心不确定**：6routes_v4.json 当时是怎么抓到的、什么网络状态。
**这影响整个抓取策略**。
