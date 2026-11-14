# 本地工作台接口

服务默认仅监听回环地址，供本机使用，不是公开多租户系统。所有 JSON 响应使用 UTF-8，出错时有 error 文本；业务输入错误为 400，未知路径为 404。静态资源仅允许根页面和固定资源名。

| 路径 | 方法 | 当前含义 |
| --- | --- | --- |
| /health | GET | 进程存活 |
| /api/study | GET | 当前完整业务资料 |
| /api/summary | GET | 现行描述与资料核对 |
| /api/action | POST | 执行网页表单对应的现行操作 |
| /api/notes | GET | 本地工作批注 |
| /api/notes | POST | 保存 text 和 selection，返回新增批注 |

现行操作的具体字段在 data/default-action.json，可通过网页直接提交。已有接口应保持可用；新的分析接口、结果表示与交互方式可按产品需要设计，不要求沿用现行描述性结果的字段。数值未定义应使用 null 并解释原因，不以 NaN 或 Infinity 冒充 JSON 数值。
