# 板块指数与板块状态派生

## 固定采集编号

板块指数事实表和三张板块状态表使用与全 A 快照完全相同的 `collection_id`：

```text
YYYYMMDD001 ... YYYYMMDD254
```

编号只由固定计划时间决定。一次节点失败时，该编号在计划表中保留失败状态；板块指数事实和三张状态表不造空行，下一节点继续使用自己的编号。

| 编号 | 计划时间 |
|---|---|
| `001-011` | 09:15:08-09:25:08，每分钟 |
| `012-132` | 09:30:08-11:30:08，每分钟 |
| `133-249` | 13:00:08-14:56:08，每分钟 |
| `250-253` | 14:57:08、14:58:08、14:59:08、15:00:08（自2026-09-14起） |
| `254` | 15:30:08（自2026-09-14起） |

所有关联严格按 `collection_id`：

```text
hithink_snapshot_derived
hithink_market_state
hithink_limit_up_pool
hithink_limit_down_pool
hithink_sector_index_snapshot
concept_state / industry_state / style_state
```

## 四张表

| 表 | 角色 |
|---|---|
| `hithink_sector_index_snapshot` | 同花顺板块指数 API 的原始事实 |
| `hithink_concept_state` | 概念板块本地聚合状态 |
| `hithink_industry_state` | 行业板块本地聚合状态 |
| `hithink_style_state` | 风格/特色指数本地聚合状态 |

指数事实只保存 API 返回的 OHLC、涨跌幅、成交量和成交额；不在事实表里计算一分钟变化或板块强弱。

## 单节点顺序

```text
全 A derived + 同节点涨跌停池 + market_state 完成
        ↓
读取当前 active sector_catalog
        ↓
分批请求板块指数 API，写 hithink_sector_index_snapshot
        ↓
使用同 collection_id 的 derived、池、market_state 和历史成员映射
        ↓
写 concept_state / industry_state / style_state
```

当前 API 实测安全批量为 600 个代码；815 个活跃板块使用 2 次请求。`scheduled_time` 固定为计划节点，`source_time` 保留每一批 API 的真实返回时间。

## 成员历史

板块成员只使用历史有效映射：

```text
observed_from <= trade_date
AND (observed_to > trade_date OR observed_to IS NULL)
```

不使用今天的成员名单回填历史日期。

## 核心口径

| 字段组 | 口径 |
|---|---|
| 板块涨跌、九档分布 | 同编号成分股 `price_change_ratio_pct` 聚合 |
| 涨停/跌停 | 同编号官方池交集；没有同编号池事实则四个依赖字段为 `NULL` |
| 累计成交额/量 | 成分股 `turnover` / `volume` 求和 |
| 一分钟成交额/量 | 成分股 `turnover_delta_1m` / `volume_delta_1m` 求和 |
| 指数一分钟涨跌 | 当前与相邻编号、同 session、恰差 60 秒的指数 `last_price` 比较 |
| 上日同期 | 官方交易日历的上一交易日 + 同一三位序号；缺失不向前跳日 |
| 市场占比 | 板块值 / 同编号 `hithink_market_state` 值，结果为百分数 |
| TOP1/3/5 | 板块内有效一分钟成交额排序后占本板块一分钟成交额比例 |

一阶分钟指标要求当前节点和前一编号节点在同 session 且相差 60 秒。二阶指标还要求前一节点自身已有有效一分钟增量。收盘首次基线没有前一连续分钟，因此相关一分钟字段为 `NULL`。

## 收盘基线

首次初始化只允许写当前交易日的 `254` 号收盘最终节点，绝不倒推此前盘中历史。自2026-09-14起计划时间为 `15:30:08`；API 实际返回时间写入 `source_time`。
