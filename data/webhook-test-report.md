# TradingView Webhook 场景测试报告

测试时间：2026-09-27T07:44:39.611670+00:00

本地接口：http://127.0.0.1:8000/api/webhooks/tradingview

## 本地 Demo 终端结果

- 客户端 A：Demo 模拟账户，已启用，0.01 手；测试前程序持仓为空。
- 开多、开空：HTTP 202 接受后由后台执行；MT5 返回 `10018 Market closed`，没有成交。
- 新鲜重复开空信号：`duplicate=true`，未创建第二条执行任务。
- 多头、空头 `__STOPLOSS` 平仓信号：HTTP 202，执行成功，但无对应持仓，属于无下单的空仓操作。
- 旧开多信号超过 180 秒后重放：先被时效校验拒绝。这不等同于新鲜重复信号测试。
- 测试结束：程序多空持仓均为空，未修改客户端开关、手数或保护止损配置。

### 已发送的本地 HTTP 请求

| 场景 | HTTP | 后台结果 | 说明 |
| --- | --- | --- | --- |
| open-long | 202 | failed | A: MT5 下单失败：10018 Market closed |
| expired-original-replay | 400 | 未进入执行队列 | TradingView 信号已经过期 |
| open-short | 202 | failed | A: MT5 下单失败：10018 Market closed |
| duplicate-open-short | 202 | failed | 识别为重复信号；A: MT5 下单失败：10018 Market closed |
| close-long-stop | 202 | success |  |
| close-short-stop | 202 | success |  |
| unsupported-strategy | 400 | 未进入执行队列 | 不是受支持的 TradingView 策略 |
| unsupported-symbol | 400 | 未进入执行队列 | 不支持的交易品种：EURUSD |
| expired-signal | 400 | 未进入执行队列 | TradingView 信号已经过期 |
| future-signal | 400 | 未进入执行队列 | TradingView 信号时间来自未来 |
| invalid-timestamp | 400 | 未进入执行队列 | TradingView timestamp 格式不正确 |
| unchanged-position | 400 | 未进入执行队列 | 不支持的仓位变化：flat → flat |
| missing-name | 422 | 未进入执行队列 | 请求参数校验 |
| malformed-json | 422 | 未进入执行队列 | 请求参数校验 |

## 隔离的端到端测试

新增测试：`tests/test_webhook_full_scenarios.py`。通过 FastAPI POST Webhook，实际运行信号解析、SQLite 存储、后台队列和 Mt5Gateway 下单代码；只将 MetaTrader5 终端 I/O 替换为内存模拟器，不连接实际终端。

覆盖：多头/空头开仓、同向重复不加仓、Webhook 去重、多头/空头策略止损、多头/空头止盈、重复平仓、双向反手先平后开、开仓附带正确方向和距离的券商保护止损、关闭保护止损、模拟券商止损后再收到 TV 平仓不重新开仓、各账户保存手数用于后续开仓、平仓采用原持仓实际手数、客户端停止阻止执行、算法交易关闭、开仓和平仓遭拒、无关 magic 的仓位不受影响，以及错误策略/品种/时间/仓位变化/请求格式。

## 尚未实盘式验证的项目

- 因终端休市，Demo 终端实际成交、带持仓平仓、双向反手、券商实际价格穿越止损后的自动平仓均未验证。隔离模拟通过不能当作实际成交证明。
- 当前运行服务的信号列表还没有返回新增的 `trigger_price` 字段。保存的原始信号仍包含价格，需重启加载最新后端代码后再确认界面。

## 重跑隔离测试

```powershell
python -m pytest tests/test_webhook_full_scenarios.py -q
python -m pytest -q
```

原始请求和返回结果见同目录 `webhook-live-probe.json`。重发时应生成新的 UTC timestamp 和唯一 id；旧请求可能因超时被拒绝。

完整回归结果：124 项通过，其中新增端到端场景 21 项；本地 Demo HTTP 请求 14 个。
