# 相关不确定输入下预算模型的全局敏感性与补测选择

这是供本地讨论使用的数据应用。当前版本提供有约束的单情景预算计算、共同随机冲击重放，以及保留联合关系的全局敏感性分析。网页可以检查原始记录、运行现行操作、查看各关键投入对预算超支与方案选择的影响、回查贡献估计的误差与适用假设、比较补测价值，并保存批注。

## 全局敏感性分析

`POST /api/sensitivity` 使用 data/study.json 的联合观测，按情景锚定后估计条件期望，用 Shapley 效应把目标方差分摊到参与人数、使用时长与人员成本。份额带自助区间与排名区间；每个目标同时给出拟合 R²、交互与联合可解释比例、复合乘积驱动诊断和适用假设。输入强相关时单独份额会重复计算共享信息，页面明确提示应优先阅读联合与交互部分。

只有范围、没有概率的 overhead 只做端点与情景值的范围分析，不假设均匀分布，也不进入 Shapley 分解。内部随机冲击与输入独立，超支概率和期望超支额按正态分布解析积分；同一参数换随机种子的重放只展示模拟噪声尺度，不计入参数重要性。补测价值按条件期望选择方案（即完美信息价值），给出单输入与组合的价值、自助区间和决策翻转概率；提供测量成本后按净价值排序，否则明确说明不能据此分配经费。详见 docs/analysis-contract.md 与 docs/http-contract.md。

## 环境与启动

使用 Python 3.11.14，数值依赖锁定在 requirements.txt。Docker 基础镜像按内容摘要固定；构建阶段安装依赖，服务、检查和演示阶段不下载数据、依赖或模型。所有业务记录都是本地构造的课程与研究管理资料，不包含个人或真实实验信息。

```sh
docker build -t decision-sensitivity-studio:local .
docker run --rm -p 8080:8080 decision-sensitivity-studio:local
```

浏览器打开本机 8080 端口。复用已有 Python 环境时，先在准备阶段安装锁定依赖，再运行服务：

```sh
python3 -m pip install --only-binary=:all: -r requirements.txt
python3 -m app.service
```

浏览器回归使用锁定的 Playwright 1.56.0 与其配套 Chromium。镜像构建阶段预装浏览器和系统库，测试与服务运行不下载任何依赖，不依赖宿主机的 Chrome 或 Edge。直接使用 Python 环境时，也需在准备阶段执行 `python3 -m playwright install --with-deps chromium`。

APP_STUDY 可以指定同结构本地资料，APP_STATE_DIR 指定批注目录，默认是 .local。批注只保存人工文字与当时的表单选择，不冻结分析数据或生成统计发布；删除该目录可回到干净本地状态，原始资料不变。

## 构建与检查

```sh
python3 -m compileall -q app tests
python3 -m unittest discover -s tests -v
docker run --rm --network none decision-sensitivity-studio:local python -m unittest discover -s tests -v
```

测试覆盖现行领域语义、HTTP 错误边界与批注重开、全局敏感性的数值语义（用已知答案的合成基准核对估计器，覆盖独立加性、强相关、纯交互、确定性模型与纯内部噪声），并在真实 Chromium 中遍历资料表、运行敏感性分析、核对贡献区间与范围分析、按测量成本重新比较建议，保存批注并刷新确认。浏览器缺失或脚本报错会令测试失败，不跳过检查。data/analysis-cases.json 是分析使用的独立检查资料，docs/analysis-contract.md 说明其统计意义和适用限制；这些资料不要求每次输出某种结论方向。

## 短时间实机查看

```sh
python3 -m app.demo
```

该入口启动真实本地 HTTP 服务，逐项打印健康检查、资料概况、分析元数据、现行操作与一次完整全局敏感性分析的实际响应，随后关闭服务。默认资料在九十秒内完成，不读取预存日志。网页交互也使用同一服务。正式运行前先完成镜像构建和依赖准备。

## 代码位置

app/domain.py 定义业务资料语义与现行操作；app/sensitivity.py 实现联合输入下的全局敏感性、内部噪声分离与补测价值；app/service.py 是网页和接口共同使用的服务；web/app.js 提交现行表单、呈现原始记录并驱动敏感性分析页面；app/storage.py 保存工作批注。外部接口与分析边界分别见 docs/http-contract.md 和 docs/analysis-contract.md。
