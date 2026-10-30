# Stage 0：契约冻结

状态：**契约定义中，未修改任何现有代码。已根据 5 份模拟文本生成 L001-L005 黄金集，等待需求方审核。**

本目录只保存 Stage 0 的接口/规则/数据契约，不包含抽取实现，也不改动仓库根目录的 Schema、字段配置和样例。

## 已确认的边界

| 项 | 决策 |
| --- | --- |
| 案件类型 | 只做民间借贷；需求文档中的买卖合同例子不作为验收样例 |
| 输入 | 只支持 `fileContext: string[]`；每项为一个段落/文本块；所有项属于同一案件 |
| 输出 | 9 个顶层键的嵌套业务 JSON；`subjectId` 不进入业务输出 |
| 当事人 | 支持多原告、多被告、多第三人 |
| 代理人 | MVP 保持一个全局代理人区块；不同当事人代理状态不一致时进入 review |
| 借款/担保粒度 | MVP 只支持单笔借款、单份保证合同 |
| 枚举基数 | 保持现有 Schema 多选；非法组合由业务校验拦截，不改 Schema 结构 |
| 业务校验 | 结构错误报错；业务问题返回数据 + `needs_review`；无原告/被告或主体全空报错 |
| 证据 | E2：关键字段必须有 evidence；`否/无` 必须有明确否定引文 |
| 模型 | 定义可替换适配层；后续接入 Qwen3.8 API |
| 模拟数据 | 由需求方提供纯文本；黄金集覆盖单/多当事人、代理人、担保、日期、冲突等 |

## 字段契约变更（B 方案）

- 新增 `事实与理由/5.借款利率/补充内容`，字段 ID 为 **F160**；
- 现有 F001-F159 保持不变，F160 追加在字段配置末尾；
- 已同步：`loan_complaint.schema.json`、`field_config.json`、`field_dictionary.md`、`inventory.json`、`example_from_attachment.json`、`build_fields.py`；
- `schemaVersion` 仍为 `0.1.0`（初版未冻结；正式冻结时建议升为 `0.2.0`）。

## 交付物

| 文件 | 用途 |
| --- | --- |
| [model-adapter.md](model-adapter.md) | 模型适配层接口、错误分类、Qwen3.8 接入约束 |
| [business-rules.md](business-rules.md) | 5B 互斥规则、E2 证据规则、代理状态和业务校验等级 |
| [input.schema.json](input.schema.json) | `POST /v1/extract` 请求体 |
| [subject-index.schema.json](subject-index.schema.json) | 内部共享主体索引 |
| [review.schema.json](review.schema.json) | 证据、冲突、缺失、警告 |
| [api-contract.md](api-contract.md) | 请求/响应包装、状态、错误码、HTTP 映射 |
| [fixtures/README.md](fixtures/README.md) | 模拟文本和黄金集的目录/格式约定 |

## 本阶段明确不做

- 不写模型抽取代码；
- 不解析 DOCX；运行时只接受 `fileContext`；
- 不支持多笔借款、多份保证合同；
- 不支持按主体区分代理人；
- 不修改根目录现有 `loan_complaint.schema.json`、`field_config.json` 和样例文件。

## 黄金集进度

已根据 `fixtures/raw/file_raw.json` 的 5 份模拟文本生成：

| 案件 | 当事人 | 状态 | 关键点 |
| --- | --- | --- | --- |
| L001 | 陈明远 / 李志强 | `ok` | 有书面借款协议；部分还款；逾期年利率 4% 作为 `5.借款利率` 补充说明 |
| L002 | 林晓雨 / 周文博 | `ok` | 无书面合同；微信确认；签订时间取借款发生日；`17/合同约定` null |
| L003 | 刘思远 / 郑海波 | `ok` | 借条；三期还款；已还一期；逾期起始取最后一期次日 2025-10-16 |
| L004 | 许安宁 / 何文杰 | `ok` | 口头借款；签订时间取借款发生日；`17/合同约定` null |
| L005 | 王嘉诚 / 赵子航 | `ok` | 三笔交付聚合为一次借款；借款确认书；逾期年利率 3% 作为 `5.借款利率` 补充说明 |

每个案件目录包含 `input.json`、`expected.data.json`、`expected.review.json`、`expected.meta.json`、`notes.md`。

## 下一步

1. 需求方审核 L001-L005 的期望 `data` 和 `review`；
2. 如有修改，先改 `notes.md` 中的业务判断，再同步 JSON；
3. 5 个案例确认后，补充 `needs_review` / `error` 场景（多当事人、代理状态不一致、金额冲突、无被告、主体全空）；
4. 黄金集冻结后，再进入 Stage 1 的抽取实现。
