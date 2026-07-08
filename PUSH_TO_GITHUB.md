# 推到 GitHub + 启用 Actions cron — 5 分钟

## 1) 建 GitHub repo

- 打开 https://github.com/new
- **Repo name**: `flights` (或你喜欢的名字)
- **Public**（Public 才能用 GitHub Actions cron 免 Pro 费）或 **Private + GitHub Pro**
- 不要勾 README / .gitignore / license（本地有）
- 创建

## 2) 推代码

```bash
cd /Users/owendu/Minimax/flights
# 替换 <you> 为你的 GitHub username
git remote add origin git@github.com:<you>/flights.git
git branch -M main
git push -u origin main
```

如果用 HTTPS：
```bash
git remote add origin https://github.com/<you>/flights.git
```

## 3) 启用 Actions 写权限

- GitHub repo → **Settings** → **Actions** → **General**
- **Workflow permissions**: 选 **"Read and write permissions"**
- **Allow GitHub Actions to create and approve pull requests**: ✅
- Save

## 4) 触发第一次跑（手动）

- GitHub repo → **Actions** tab
- 左边选 **Scrape Ctrip flights**
- 右边 **Run workflow** → **Run workflow** 按钮
- 等 ~16-25 分钟跑完

## 5) 验证

跑完后：
- 看 Actions 日志最后几行，应该有 "DONE. rows: cal=... flights=..."
- 拉取最新 SQLite：`git pull` （workflow 已经 commit 数据）
- 在 SQLite 查 `flights_count` 应该有 6 routes × 8 dates = ~48 个 snapshot

## 6) 调整 cron 频率

`.github/workflows/scrape.yml` 里的 cron 表达式：

```yaml
on:
  schedule:
    - cron: '0 18 * * *'   # UTC 18:00 = BJT 02:00 (next day)
```

改成：
- 每天 2 次：`0 10 * * *` + `0 22 * * *` (UTC) = BJT 18:00 + 06:00
- 每天 1 次但更早：`0 14 * * *` (UTC) = BJT 22:00 (前一晚)

## 注意事项

- **首次跑**会 `playwright install --with-deps chromium` 下载浏览器，~5-10 分钟
- **TUN VPN 不影响 GitHub Actions**（Actions runner 在 GitHub 自己的服务器，跟你本机 IP 无关）
- **数据 commit 到 repo**：默认 commit SQLite 文件。如果觉得太频繁，可以改成只 upload artifact，不 commit
- **费用**：公开仓库 100% 免费
