# Symbol Resolution — `utils/resolve_symbol.py`

关于符号类型检测和数据引导的说明。

## 用途

当用户输入一个符号（如 `931573`、`SPY`）但该符号不存在于状态文件或 asset-master 中时，`resolve_symbol` 负责：

1. 根据符号格式检测类型
2. 路由到正确数据源
3. 引导数据到 `knowledge/{region}/3_processed/{symbol}.csv`
4. 可选注册到 `asset-master.json`

## 符号类型受理矩阵

| 类型 | 符号模式 | 示例 | 数据源 | 支持状态 |
|:---|:---|---|:---|---:|
| **CSI_INDEX** | `93xxxx` | 931573 | CSI TR API (`{sym}CNY01`) | ✅ |
| **CSI_INDEX** | `000xxx` | 000300 | CSI TR API (`{sym}CNY010`) | ✅ （pattern 不保证成功） |
| **CSI_INDEX** | `399xxx` | 399300 | CSI TR API | ✅ |
| **CN_ETF** | `159xxxx` | 159307 | Sina → EastMoney | ✅ |
| **CN_ETF** | `51xxxx` | 510310 | Sina → EastMoney | ✅ |
| **CN_ETF** | `56xxxx` | 560700 | Sina → EastMoney | ✅ |
| **CN_OTC** | `007xxx` | 007751 | TR mapping → CSI API | ✅ |
| **CN_OTC** | `012xxx` | 012708 | TR mapping → CSI API | ✅ |
| **US** | `[A-Z]{2,5}` | SPY, QQQM | yfinance | ✅ |
| **HK** | `xxxx.HK` | 3032.HK | yfinance | ✅ |
| **CSI_TR** | `Hxxxxx` | H00300 | CSI TR API（直输） | ✅ |
| **CSI_TR** | `xxxCNY01` | 931573CNY01 | CSI TR API（直输） | ✅ |
| **CNI_INDEX** | `48xxxx` | 480080 | **不支持** — 国证指数无可用价格数据源 | ❌ |
| **CNI_INDEX** | `98xxxx` | 984096 | **不支持** — 国证指数无可用价格数据源 | ❌ |
| **UNKNOWN** | 其他 | UNKNOWN | 无 | ❌ |

## 数据源路由链

```
用户输入符号
    ↓
classify_symbol()
    ↓
根据类型选择数据源:

CSI_INDEX ──→ 查 tr_mapping.json（asset-master 的 tr_index 镜像）
         │        有 → 直输 CSI TR API
         └──→ 无 → 查 csi_patterns.json → 尝试 pattern后缀 → CSI TR API
         
CN_ETF   ──→ Sina API ─→ 失败 → EastMoney API

US/HK    ──→ yfinance

CN_OTC   ──→ tr_mapping.json → CSI TR API

CNI_INDEX──→ 明确返回 None（数据源不可用）
```

## CSI TR 代码映射的局限性

CSI 总收益指数的代码没有统一的生成规则：

| 价格指数 | TR 代码 | 规律? |
|:---|---|:---:|
| 931573 | `931573CNY01` | ✅ 93 + CNY01 |
| 000510 | `000510CNY010` | ✅ 000 + CNY010 |
| 000300 | `H00300` | ❌ 完全不同的编码体系 |
| 159307 | `H20955` | ❌ 完全不同的编码体系 |

因此需要两套机制并行：

1. **`tr_mapping.json`** — 手工维护的已知映射（来源：asset-master 的 `tr_index` 字段）
2. **`csi_patterns.json`** — 启发式后缀尝试，confidence 低的模式失败时返回 None

```json
// config/symbol_resolution/csi_patterns.json
{
    "93":  { "suffix": "CNY01",  "confidence": "high" },
    "000": { "suffix": "CNY010", "confidence": "medium" },
    "399": { "suffix": "CNY01",  "confidence": "low" }
}
```

对于 pattern 无法识别的符号（如 `000300` → TR=`H00300`），resolve 会返回 None，需要用户提供 TR 代码或手动补充 mapping。

## 各 CLI 的接入点

| CLI | 当前行为 | 接入 resolve_symbol 后 |
|:---|:---|---|
| **analyze** | asset-master 查不到 → 拒绝 | `pick_asset()` 查不到 → 调 `resolve(sym, add_to_master=True)` |
| **log** | Tier 3 live fetch 失败 → 拒绝 | `load_data()` 全部失败后 → 调 `resolve(sym)` |
| **review** | 只读 plan config | 不需要改 |
| **decide** | 只读 plan config | 不需要改 |

## 维护指南

### 新增 TR 映射

```bash
# 编辑 tr_mapping.json
{
    "159307": {
        "tr_code": "H20955",
        "name": "Div Low-Vol 100"
    }
}
```

### 新增 CSI pattern

```bash
# 编辑 csi_patterns.json
{
    "93": { "suffix": "CNY01", "confidence": "high" },
    ...
}
```

`confidence` 字段：
- `high` — 已知稳定的模式（如 93 开头的 CSI 指数）
- `medium` — 可能但不保证（如 000 开头的指数）
- `low` — 猜测性质，容易失败
