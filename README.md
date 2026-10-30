# fortune-telling-benchmark

面向中文命理问答研究的**最小可运行评测框架**：先明确测量对象，再核验题目、答案与授权，最后比较模型。

> **当前不是已验证题库或排行榜。** 仓库只包含 8 道人工合成的四选一管线测试题，不含赛事原题、原始命盘、个人经历或第三方答案全文。合成题不表达任何「算命为真」的主张。尚未选择模型、批准预算或执行付费模型调用。

## 我们测什么，不测什么

| 轨道 | 所测对象 | 当前状态 |
| --- | --- | --- |
| `synthetic_pipeline` | 输入、调用、评分流程是否正确 | 本仓库 8 道合成题，仅作软件自检 |
| `competition_agreement` | 与赛事公开答案的一致程度 | 数据与原始答案核验、再分发授权均待完成 |
| `rule_reasoning` | 在明确给定规则下是否正确推导 | 设计预留，无题库 |
| `real_event` | 对有独立证据的真实事件能否产生可检验预测 | 独立研究设计预留，无题库 |

**赛事答案一致率 ≠ 对真实未来的预测准确率 ≠ 命理理论得到科学验证。** 不将上述轨道混成一个能力结论，也不把未经复核的来源称作 gold standard。详见 [评测方法](docs/METHODOLOGY.md) 和 [来源清单](docs/SOURCES.md)。

## 3 分钟离线跑通

要求 Python 3.10+；仅使用标准库，不需安装依赖。在仓库根目录执行：

```bash
python3 -m unittest discover -s tests -v
python3 -m ftbench validate --cases data/synthetic/cases.jsonl
python3 -m ftbench score \
  --cases data/synthetic/cases.jsonl \
  --predictions data/synthetic/predictions.jsonl
```

预期总计：`total=8`、`correct=4`、`incorrect=1`、`invalid=1`、`refused=1`、`missing=1`、`error=0`，`accuracy=0.5`。这只是预设的软件测试结果，不是任何模型的成绩。

评分会输出总体、按年份、按轨道及逐题分类。缺答、无效格式、拒答、请求失败均保留在总分母中。重复 ID、未知 ID、非法 JSON 或字段错误会明确报错，不能悄悄丢弃。预测文件可以为空，此时所有题计作 `missing`。

## 输入约定

每个 UTF-8 JSONL 行是一个对象，不允许空行、重复 JSON 键或额外字段。机器可读说明见 [题目 schema](schemas/case.schema.json) 和 [预测 schema](schemas/prediction.schema.json)；CLI 还会检查跨行唯一 ID 等约束。

题目必需字段：

```json
{"id":"demo-01","year":2022,"track":"synthetic_pipeline","question":"【人工合成】请选择数字 2。","choices":{"A":"1","B":"2","C":"3","D":"4"},"answer":"B","source_id":"synthetic-local-v1","synthetic":true}
```

预测格式：

```json
{"id":"demo-01","response":"B","status":"ok"}
{"id":"demo-02","response":null,"status":"refusal"}
{"id":"demo-03","response":null,"status":"error","error_code":"network_error"}
```

以上预测记录仅展示格式，不是同一套可直接评分的数据。`response` 只去掉首尾空白后与 `A/B/C/D` **大小写敏感地精确匹配**；`b`、`答案是 B`、多选、空字符串都算 `invalid`，不会用宽松正则猜答案。`REFUSE` 或显式 `status=refusal` 算拒答；其他自然语言拒答算格式无效，避免不透明的语义判定。参见方法文档中的指标定义。

## 可选：OpenAI-compatible HTTP runner

**默认仅预演**，不读密钥、不创建结果文件、不发请求。端点、模型与预算由操作者决定，下面的域名是不可用的占位符：

```bash
python3 -m ftbench run \
  --cases data/synthetic/cases.jsonl \
  --endpoint https://YOUR-PROVIDER.example/v1/chat/completions \
  --model YOUR-MODEL \
  --output-dir runs/first-run
```

只有明确批准费用、确认数据可发往该服务后，才在安全的终端会话中设置环境变量 `BENCH_API_KEY`，并给上述命令添加 **`--execute`**。不要把密钥放到命令参数、代码、题目或 Git 历史里；不要公开提交模型输出或运行目录。这里未执行任何真实 API 请求。

- 每题最多一次请求，无自动重试；不按提供方价格计算费用。总请求上限等于题数，输出 token 上限由 `--max-tokens` 控制，输入仍可能计费。
- 只实现非流式 Chat Completions 风格的 `model/messages/temperature/max_tokens/n` 参数和 `choices[0].message.content` 响应。并非所有兼容服务/模型都接受这些字段；实际兼容性需在批准后验证。
- 密钥仅从环境变量读取（可用 `--api-key-env` 改名）；默认要求 HTTPS，不跟随重定向。只有 `--allow-local-http` 才允许 loopback 的 HTTP 测试端点。
- 提示只选取 `question` 与 `choices`，不发送 `answer`、`source_id`、年份或其他字段。仍须人工检查题干、选项没有答案泄漏或个人敏感信息。
- 结果目录必须不存在，避免覆盖。记录请求模型名、参数、提示版本、数据 SHA-256、开始/结束时间及状态；逐题保存预测，输出 `report.json`。
- HTTP/网络失败记为错误并继续；中断时已保存部分结果，并以全体题目评分。再次执行须使用新目录，不会自动补跑或重复付费。
- 保存的是**请求的模型名称**，不是经第三方证明的后端快照；`temperature=0` 也不保证完全可重复。

## 数据边界与后续门槛

1. 当前候选线索来自 [MingLi-Bench](https://github.com/DestinyLinker/MingLi-Bench) 和 [fate-bench 来源目录](https://github.com/shunshi-ai/fate-bench/blob/main/data/raw/hkjfma/INVENTORY.md)，只登记链接，不复制其代码或原始题目。
2. 据来源描述，2022–2025 每年 40 题、合计 160 题。2022–2024 的 120 题最多称为「主集候选」，**不是已验证 gold**。2025 来源单独标记，不与已验证题目混同。
3. 原图可读性、原始答案、转录、题号对应、来源版本、隐私与再分发许可均需核验；当前尚未完成。详见来源清单的逐年状态。
4. 通过数据门槛后，再决定模型名单、成本上限、重复次数与公平参数；本仓库没有自动调用付费模型的 CI 或任务。

## 目录

```text
ftbench/                 校验、离线评分、显式执行的 HTTP runner
data/synthetic/          原创合成自检样例及预设预测
schemas/                单行 JSON schema（另需跨行校验）
tests/                  确定性离线单元测试，网络响应均模拟
docs/METHODOLOGY.md      指标、分轨、污染风险、数据准入
docs/SOURCES.md          来源链接、核验与权利状态
```

## 许可

本仓库尚未指定代码、文档及合成样例的许可证，待仓库所有者确认后添加；公开可见不等于已授予开源再利用许可。**这也不授予任何第三方赛事题目、图片、命盘、答案或个人资料的使用/再分发权利。** 第三方仓库代码的 MIT 等软件许可，不能自动解释为其所收录赛事数据的许可。未核清授权前，不将原始数据添加到公共仓库。
