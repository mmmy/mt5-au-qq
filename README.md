# MT5 AU QQ

项目提供一个本地 Web 页面，用于管理 TradingView 黄金策略警报、接收策略 webhook，并通过本机 MetaTrader 5 模拟账户执行交易。后端使用 Python/FastAPI，前端使用原生 HTML、CSS 和 JavaScript。

## 功能

- 输入 1～6 个空格分隔的价格创建警报
- 自动替换 `payload.json` 中对应的六组价格和开关
- 创建时自动把 Pine 策略开始时间更新到当前 K 线
- 只展示名称以 `MT5_AU_QQ::GOLD_PRICE::` 开头的警报
- 删除前在服务端确认警报属于本项目
- 使用请求 UUID 防止相同创建请求被重复执行
- 接收 TradingView 策略 webhook，并根据仓位变化识别开多、开空、平多、平空
- SQLite 持久化信号、执行状态和 MT5 订单
- 单一 MT5 工作线程串行执行交易
- 默认仅允许模拟账户，使用固定小手数和独立 magic number
- 页面展示 MT5 状态、本机 webhook URL、交易总开关和手动测试按钮

## 安装和运行

建议使用虚拟环境：

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -r requirements.txt
```

确认项目根目录存在有效的 `.tv-cookie` 和 `payload.json`，然后启动：

```powershell
python -m uvicorn app.main:app --host 127.0.0.1 --port 8000
```

浏览器打开 <http://127.0.0.1:8000>。API 文档位于 <http://127.0.0.1:8000/docs>。

不要使用多个 Uvicorn worker。所有 MT5 操作都在一个专用工作线程中串行执行。程序启动后默认停止交易，必须在页面确认 MT5 状态正常后手动启用。

MT5 需要满足以下条件：

- Windows MT5 终端已经启动并登录模拟账户
- MT5 顶部“算法交易/Algo Trading”已经开启
- “工具 → 选项 → 智能交易系统”允许算法交易
- 券商提供的黄金品种名称与 `MT5_SYMBOL` 一致

## 配置

可通过环境变量覆盖默认值：

| 环境变量 | 默认值 | 说明 |
| --- | --- | --- |
| `TV_COOKIE_FILE` | 项目根目录 `.tv-cookie` | TradingView Cookie 文件 |
| `TV_PAYLOAD_FILE` | 项目根目录 `payload.json` | TradingView 警报模板 |
| `TV_ALERT_NAME_PREFIX` | `MT5_AU_QQ::GOLD_PRICE::` | 本项目警报名标识 |
| `TV_ORIGIN` | `https://cn.tradingview.com` | TradingView Origin/Referer |
| `TV_REQUEST_TIMEOUT_SECONDS` | `20` | TradingView 请求超时秒数 |
| `TRADINGVIEW_WEBHOOK_URL` | `http://127.0.0.1:8000/api/webhooks/tradingview` | 写入新警报并展示在页面的 webhook URL |
| `DATABASE_FILE` | `data/trading.db` | SQLite 数据文件 |
| `MT5_TERMINAL_PATH` | 自动识别当前终端 | MT5 terminal64.exe 路径 |
| `MT5_SYMBOL` | `XAUUSD` | MT5 黄金品种名称 |
| `MT5_VOLUME` | `0.01` | 固定下单手数 |
| `MT5_MAX_VOLUME` | `0.10` | 程序允许的最大手数 |
| `MT5_MAGIC` | `26082301` | 本程序仓位标识 |
| `MT5_DEVIATION` | `20` | 下单允许偏差点数 |
| `MT5_EMERGENCY_SL_DISTANCE` | `20` | 券商端灾难保护止损价格距离，0 表示关闭 |
| `MT5_DEMO_ONLY` | `true` | 只允许模拟账户 |
| `SIGNAL_MAX_AGE_SECONDS` | `180` | webhook 信号最大有效秒数 |
| `TRADING_ENABLED_AT_START` | `false` | 启动时是否自动允许交易，不建议开启 |

`.tv-cookie` 已加入 `.gitignore`，不能提交到版本库。如果 Cookie 曾经被提交或泄露，应立即退出 TradingView 会话并重新登录。

## API

```text
GET    /api/alerts
POST   /api/alerts
DELETE /api/alerts/{alert_id}
GET    /api/health
POST   /api/webhooks/tradingview
GET    /api/trading/status
POST   /api/trading/enable
POST   /api/trading/disable
POST   /api/mt5/actions/open_long
POST   /api/mt5/actions/open_short
POST   /api/mt5/actions/close_long
POST   /api/mt5/actions/close_short
GET    /api/trade-signals
POST   /api/trade-signals/clear
GET    /api/trade-orders
```

创建请求示例：

```json
{
  "prices": "4600 4620.5 4660",
  "request_id": "6ec3de30-cbfc-4402-b418-168b40d18b38"
}
```

TradingView webhook 根据 `prevMarketPosition` 和 `marketPosition` 判断操作。相同 webhook 会通过信号哈希去重，不会重复下单。当前本机开发阶段按需求暂不校验 `signalToken`，部署到公网前必须增加鉴权。

“清除记录”只会在页面隐藏已经结束的信号，不会删除去重数据；等待中和执行中的信号不会被清除。

程序只查询和操作 `MT5_MAGIC` 匹配的仓位，不会主动平掉手工仓位或其他 EA 的仓位。停止交易不会自动平掉已经存在的仓位。

## 测试

测试不会请求 TradingView，也不会创建或删除真实警报：

```powershell
python -m pytest -q
```
