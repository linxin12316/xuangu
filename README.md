# 选股 + 资讯雷达 (xuangu)

**一体化 A 股决策辅助工具**：Windows 主力运行，盘前选股 + 盘中实时快讯推送 + 晚间复盘。采用财联社电报实时监控 30+ 板块关键词，命中即推微信。

> ⚠️ **严肃声明**
> - 本工具只是**量化筛选辅助**，不是"必涨预言"。胜率上限约 55%。
> - 推送的 3-5 只候选**仅供研究**，请自行判断、严格止损、控制仓位。
> - 投资有风险，亏损自负。任何宣称"必涨""稳赚"的工具都是骗子。

---

## 它做什么

**Windows Daemon** 在后台连续运行，自动按以下时间表执行任务：

| 时间（北京）| 间隔 | 任务 |
|---|---|---|
| 08:25-08:55 | 1次 | 📈 **盘前选股** — Top 5 候选 + 止损建议、连续上榜标记 |
| 08:55-09:25 | 3分钟 | 📰 **盘前资讯** — 集合竞价阶段快讯 |
| **09:25-11:30** | **2分钟** | **☀️ 盘中实时快讯推送** — 财联社关键词命中即推 |
| 11:30-13:00 | 5分钟 | 午间降频 |
| **13:00-15:00** | **2分钟** | **☀️ 盘中实时快讯推送** |
| 15:00-17:00 | 5分钟 | 盘后资讯推送 |
| 18:20-18:50 | 1次 | 🌙 **晚间复盘** — 当日总结 + 明日 Top 3 |
| 其他时段 | 10分钟 | 空闲探活 |

> 💡 **实时快讯**：财联社电报 → 关键词命中 → DeepSeek 点评 → 微信推送。盘中每 2 分钟轮询一次。

---

## 一次性配置（10 分钟）

### 步骤 1：注册 Server 酱（微信推送通道）

1. 打开 https://sct.ftqq.com
2. 微信扫码登录
3. 进入「SendKey」页面，复制你的 SendKey（形如 `SCT123456...`）
4. 加到仓库 Secret，name = `SCKEY`

### 步骤 2：注册 Tushare（数据源）

1. 打开 https://tushare.pro 注册并实名
2. 进入「个人主页」→「接口TOKEN」复制 token
3. 加到仓库 Secret，name = `TUSHARE_TOKEN`

> 免费 100 积分够用 K 线 + 北向 + 涨停 + 估值 4 项。
> 龙虎榜 / 财务 / 申万板块需 2000 积分（200 元/年），暂时给中性 2.5 分占位。

### 步骤 3：（可选）配置个人偏好

编辑仓库根目录的 `config.json`：

```json
{
  "blacklist": {
    "codes": ["688981", "300433"],
    "name_keywords": ["华大基因", "退"]
  },
  "max_per_industry": 2,
  "max_picks": 5
}
```

- `blacklist.codes`：永远不推的股票代码
- `blacklist.name_keywords`：股票名称包含这些字串就跳过
- `max_per_industry`：Top 5 中同一行业最多保留几只（默认 2）
- `max_picks`：每天推送几只（默认 5）

修改完直接 commit，下次定时跑就生效。

### 步骤 4：手动触发一次验证

1. 打开 https://github.com/linxin12316/xuangu/actions
2. 左侧点 **Xuangu Cron**
3. 右上角点 **Run workflow** → `task` 选 `pick`，勾选 `force` → 绿色按钮
4. 等待 3-5 分钟看是否绿勾
5. 微信收到一条「📈 选股报告」= 配置完成 ✅

---

## 选股算法

### 阶段 1：剔除地雷股
- ST / *ST / 退市股
- 当日停牌
- 总市值 < 30 亿
- 北交所、创业板、科创板（散户友好考量）
- `config.json` 黑名单

### 阶段 2：候选池
- 默认走「全市场涨幅×0.5 + 成交额×0.5 排序」取 Top 200
- 行业字段从 Tushare `stock_basic` 拉取（~5500 只全覆盖）
- 板块择强（Tushare 申万一级）需 2000 积分接口，免费版未启用

### 阶段 3：十维打分（满分 100）

| 维度 | 权重 | 含义 |
|---|---|---|
| 趋势 | 22 | MA5/10/20 多头排列 |
| 量能 | 18 | 近 5 日相对前 20 日的放量倍数 |
| 动量 | 12 | RSI(14) 在健康区间（50-70 满分，>80 归零防追高）|
| 资金 | 10 | 北向资金近 5 日持股变动（Tushare hk_hold） |
| 安全 | 8 | 距 60 日均线偏离度（防止追高） |
| 换手 | 5 | 1%~5% 健康活跃 → 满分 |
| 涨停 | 10 | 近期涨停次数 + 连板加成（Tushare limit_list_d） |
| 估值 | 10 | PE/PB 横截面阈值（Tushare daily_basic） |
| 龙虎榜 | 5 | 需 Tushare 2000 积分，免费版给中性 2.5 分占位 |
| 财务 | 5 | ROE+净利润同比，需 2000 积分，免费版给中性 2.5 分 |

### 阶段 4：去重 + 排序
- 同一行业最多保留 `max_per_industry` 只（默认 2），避免 Top 5 全是同板块扎堆
- 连续上榜 ≥3 天的股票总分扣 10 分（防止"审美疲劳"）
- 取最终分数最高的 `max_picks` 只 → 推送

### 阶段 5：止损建议
每只候选给出一个止损价 = `max(MA20, 现价 × 0.93)`，跌破立刻离场。

---

## 本地调试

```bash
# 安装依赖
pip3 install -r requirements.txt

# 离线干跑（用 mock 数据，不推送）
python3 -m src.main pick --dry-run

# 真实跑（需要 SCKEY + TUSHARE_TOKEN）
export SCKEY=SCTxxxxx
export TUSHARE_TOKEN=xxxxx
python3 -m src.main pick

# 回测
python3 -m src.backtest --dry-run --days 14

# 单元测试
python3 tests/test_scoring.py
python3 tests/test_backtest.py
```

---

## 本地调试

```bash
# 安装依赖
pip3 install -r requirements.txt

# 盘前选股（mock 模式）
python3 -m src.main pick --dry-run

# 资讯雷达（单次运行，不推送）
python3 -m src.radar_runner --dry-run

# daemon dry-run（跑一轮调度后退出）
python3 -m src.daemon --dry-run

# 回测
python3 -m src.backtest --dry-run --days 14

# 单元测试
python3 tests/test_scoring.py
python3 tests/test_backtest.py
```

---

## Windows 部署（推荐）

> **主推方案**：Windows Daemon 连续运行，盘中实时推送快讯。
> 旧版定时兜底（`setup_windows.ps1`）仍然保留作为备选。

### 前置条件

- Python 3.10+（[下载](https://www.python.org/downloads/)，安装时勾选 **Add Python to PATH**）
- 注册 [Server 酱](https://sct.ftqq.com) 获取 `SCKEY`
- 注册 [Tushare](https://tushare.pro) 获取 `TUSHARE_TOKEN`
- (可选) [DeepSeek](https://platform.deepseek.com/api_keys) API Key，用于快讯 LLM 点评

### 安装步骤

```powershell
cd C:\path\to\xuangu

# 1. 配置密钥
Copy-Item .env.example .env
# 用记事本编辑 .env，填入 SCKEY、TUSHARE_TOKEN
# 可选填入 DEEPSEEK_API_KEY

# 2. 安装 Python 依赖
pip install -r requirements.txt

# 3. 以管理员身份运行安装
powershell -ExecutionPolicy Bypass -File windows\setup_daemon.ps1
```

安装脚本会自动：
- ✅ 检查 Python 3 和依赖
- ✅ 检查 `.env` 密钥配置
- ✅ 注册 Windows 任务计划程序（用户登录时启动，开机自动启动）
- ✅ 可选 dry-run 测试

### Daemon 工作流程

```
┌──────────────────────────────────────────┐
│  Windows Daemon (连续运行)                 │
│                                          │
│  08:25 ──► 盘前选股 (Top 5) ──► 微信推送   │
│  09:25~11:30 ──► 盘中雷达 (每2分钟) ──► 推 │
│  13:00~15:00 ──► 盘中雷达 (每2分钟) ──► 推 │
│  18:20 ──► 晚间复盘 (Top 3) ──► 微信推送   │
│  其余时间 ──► 闲时探活 (每10分钟)           │
└──────────────────────────────────────────┘
```

### 手动控制

```powershell
# 启动（前台控制台，方便看日志）
.\windows\run_daemon.ps1

# 启动（后台无窗口）
.\windows\run_daemon.ps1 -NoWindow

# dry-run 测试
.\windows\run_daemon.ps1 -DryRun

# 单次资讯雷达（不推送）
python -m src.radar_runner --dry-run

# 单次盘前选股（mock）
python -m src.main pick --dry-run
```

### 日志

```powershell
# daemon 日志
Get-Content .\logs\daemon.log -Tail 30

# 选股推送记录
Get-Content .\picks\[日期].json

# 资讯雷达推送记录
Get-Content .\picks\[日期]-radar.jsonl
```

### 卸载

```powershell
powershell -ExecutionPolicy Bypass -File windows\setup_daemon.ps1 -Uninstall
```

或手动打开「任务计划程序」删除 `XuanguDaemon` 任务。

---

## Mac 部署

Mac 用户可以使用 launchd 兜底（仅 08:35/08:50 检查，无实时推送）：

```bash
cd /Users/jiuyueshenfeng/xuangu
cp .env.example .env
chmod 600 .env
# 编辑 .env 填入 SCKEY、TUSHARE_TOKEN

chmod +x scripts/run_pick_fallback.sh
cp launchd/com.xuangu.pick.plist ~/Library/LaunchAgents/
launchctl load ~/Library/LaunchAgents/com.xuangu.pick.plist

# 查看运行状态
launchctl list | grep xuangu
# 日志
tail -f logs/local-fallback.log
```

---

## 使用纪律（重要）

1. 同一板块**最多持有 2 只**，避免过度集中
2. 单只仓位**不超过总资金 20%**
3. **跌破止损价立即离场**，不抗单
4. 综合分 < 70 的候选**优先级降低**
5. 标 `🔁 连N日` 的股票要警惕已经涨过头
6. 大盘单日跌 > 1.5% 时**主动回避**当日候选

---

## FAQ

**Q: 为什么不直接选 1 只必涨的？**
A: 没人能做到。即使顶级量化基金次日胜率也仅 52-55%，集中买 1 只 ≈ 抛硬币。

**Q: 推送的候选我必须买吗？**
A: 不必。这是**研究清单**，不是买入清单。你应该结合自己对个股的认知再决定。

**Q: 工具哪天会失效？**
A: 当主流量化资金涌入这套规则时，超额收益会被吃掉。这套策略**简单、透明**，不指望永远赚钱。

**Q: 我能改算法吗？**
A: 可以。修改 `src/scoring.py` 中的权重和规则，commit 推送即可，下次 Action 自动用新版本。

**Q: 9 点前没收到推送？**
A: 检查 Windows Daemon 是否在运行：
   1. 打开「任务计划程序」→ 确认 `XuanguDaemon` 状态为"正在运行"
   2. 查看日志：`Get-Content .\logs\daemon.log -Tail 30`
   3. 确认 `.env` 里有 `SCKEY`
   4. 手动启动一次：`.\windows\run_daemon.ps1`

   如果 daemon 正常但没收到选股报告，单独验证：
   ```powershell
   python -m src.main pick --dry-run    # mock 看能否出报告
   python -m src.radar_runner --dry-run # mock 看雷达能否跑
   ```

---

## 数据源

- **Tushare** (https://tushare.pro)：K 线、北向、估值、涨停、行业映射 — 海外稳定
- **akshare**（兜底）：spot 快照、板块、复盘大盘 — 海外限流但有新浪 fallback
- **财联社** (https://www.cls.cn/telegraph)：盘中实时快讯，sign 算法逆向自前端
- **腾讯行情** (qt.gtimg.cn)：关联个股实时涨跌/PE/量比（海外友好，免鉴权）
- **DeepSeek** (https://platform.deepseek.com)：快讯利好/利空点评，可选（月费约 ¥2-3）

---

## 撤销 Token（重要安全操作）

本仓库由 Claude Code 创建时使用的 Personal Access Token 已在历史会话中暴露。
**强烈建议**完成首次跑通后立即去 https://github.com/settings/tokens **revoke 旧 Token**，
重新生成一个新的，且不要再保存到任何 AI 助手的记忆中。

Tushare token 同样建议旋转一次（https://tushare.pro/user/token）。
