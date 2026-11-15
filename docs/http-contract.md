# 本地工作台接口

服务默认仅监听回环地址，供本机使用，不是公开多租户系统。所有 JSON 响应使用 UTF-8，出错时有 error 文本；业务输入错误为 400，未知路径为 404。静态资源仅允许根页面和固定资源名。

| 路径 | 方法 | 当前含义 |
| --- | --- | --- |
| /health | GET | 进程存活 |
| /api/study | GET | 当前完整业务资料 |
| /api/summary | GET | 现行描述与资料核对 |
| /api/sensitivity/meta | GET | 敏感性分析的可用情景、输入、范围字段、默认值与限制 |
| /api/sensitivity | POST | 运行全局敏感性分析 |
| /api/action | POST | 执行网页表单对应的现行操作 |
| /api/notes | GET | 本地工作批注 |
| /api/notes | POST | 保存 text 和 selection，返回新增批注 |

现行操作的具体字段在 data/default-action.json，可通过网页直接提交。已有接口应保持可用；新的分析接口、结果表示与交互方式可按产品需要设计，不要求沿用现行描述性结果的字段。数值未定义应使用 null 并解释原因，不以 NaN 或 Infinity 冒充 JSON 数值。

## /api/sensitivity

请求（均为可选，缺省值见 /api/sensitivity/meta）：

```json
{
  "scenario": "reference",
  "bootstrap": 200,
  "confidence": 0.95,
  "degree": 2,
  "seed": 20261115,
  "measurement_costs": {"participants": 800, "hours": 120, "hourly_cost": 60}
}
```

- `scenario` 必须是 study.scenarios 中的 id；未知值返回 400。
- `bootstrap` 为 20 至 600 的整数，`degree` 为 1 至 3，`confidence` 在 0.5 与 1 之间，`seed` 为非负整数。
- `measurement_costs` 只接受 participants、hours、hourly_cost，值为有限非负数；未给出的组合不计算净价值。

响应主要字段：`configuration`（输入、估计量、锚定、容量剔除、测量成本）、`decision`（按期望总成本给出的当前方案）、`targets`（每个目标的均值、方差、Shapley 贡献、区间、排名区间、单独/排他份额、交互、拟合 R²、内部噪声占比）、`degree_check`、`bounds_only`（只有范围的输入，无分布假设）、`one_at_a_time`（现行 ±10% 做法对照）、`internal_noise`（解析积分与同参数重放）、`information`（补测价值、翻转概率、建议）、`diagnostics`（相关、样本量、自助完成/失败、假设、警告）。数值未定义时为 null。
