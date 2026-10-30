# API 契约

## 1. 端点

```text
POST /v1/extract
Content-Type: application/json
Accept: application/json
```

MVP 为同步接口，单请求超时 60 秒；不做异步任务队列、不做结果缓存、不做幂等重放。

## 2. 请求

请求体必须通过 [input.schema.json](input.schema.json) 校验。

```json
{
  "requestId": "req-20261030-0001",
  "templateId": "private_lending_complaint",
  "schemaVersion": "0.1.0",
  "fileContext": [
    "原告：……",
    "被告：……",
    "……"
  ]
}
```

| 字段 | 必填 | 说明 |
| --- | --- | --- |
| `requestId` | 是 | 调用方生成；原样回传；用于关联 review |
| `templateId` | 是 | Stage 0 只接受 `private_lending_complaint` |
| `schemaVersion` | 是 | 当前 `0.1.0` |
| `fileContext` | 是 | `string[]`，同一案件，按顺序拼接；1-20 项，单项 ≤ 50000 字符，总计 ≤ 100000 字符 |

不接受的输入：

- DOCX/PDF/图片；
- 空数组；
- 超过 20 项；
- 单项超过 50000 字符或总计超过 100000 字符；
- 与民间借贷无关的案件文本（返回 `error`，不强行映射）。

## 3. 响应

所有响应都使用统一包装：

```json
{
  "requestId": "req-20261030-0001",
  "status": "ok",
  "data": {},
  "review": {},
  "error": null
}
```

| 字段 | 必填 | 说明 |
| --- | --- | --- |
| `requestId` | 是 | 回显请求值 |
| `status` | 是 | `ok` / `needs_review` / `error` |
| `data` | 是 | `ok`/`needs_review` 时为 9 个顶层键的嵌套业务 JSON；`error` 时为 `null` |
| `review` | 是 | 始终返回；结构见 [review.schema.json](review.schema.json) |
| `error` | 是 | `ok`/`needs_review` 时为 `null`；`error` 时为错误对象 |

### 3.1 `status=ok`

- 请求通过输入 Schema；
- `data` 通过完整 [loan_complaint.schema.json](../loan_complaint.schema.json) 校验，且启用 `FormatChecker`；
- 通过 [business-rules.md](business-rules.md) 的全部业务校验；
- `review.conflicts`、`review.missing`、`review.warnings` 为空；
- HTTP 200。

### 3.2 `status=needs_review`

- `data` 通过结构 Schema；
- 存在业务警告、冲突、关键证据缺失，或代理状态不一致；
- `data` 保留，不自动修正；
- `review` 必须说明问题；
- HTTP 200，调用方不得把 `needs_review` 当最终结果直接使用。

### 3.3 `status=error`

- 输入非法、模型失败、输出无法通过结构 Schema、无原告/被告、主体记录全空等；
- `data` 为 `null`；
- `error` 非空；
- `review` 可以为空数组，也可以包含已经生成的主体索引/证据；
- HTTP 状态按第 5 节映射。

## 4. `data` 与 `review` 的边界

- `data` 只包含业务字段，不包含 `subjectId`、`requestId`、`confidence`、`sourceRow`；
- `subjectId`、证据、冲突、缺失、警告只进入 `review`；
- `data` 的结构以根目录 [loan_complaint.schema.json](../loan_complaint.schema.json) 为准；
- `review` 的结构以 [review.schema.json](review.schema.json) 为准；
- `review.subjectIndex` 必须与 [subject-index.schema.json](subject-index.schema.json) 同构；
- 任何字段值都不得通过 `review` 反向改写；review 只解释、不篡改。

## 5. 错误对象与 HTTP 映射

```json
{
  "code": "NO_PARTIES",
  "message": "未识别到原告或被告",
  "retryable": false,
  "details": {}
}
```

| code | HTTP | 说明 | 可重试 |
| --- | --- | --- | --- |
| `INVALID_REQUEST` | 400 | 请求 Schema 不通过、空数组、超长 | 否 |
| `MODEL_AUTH` | 401 | 模型鉴权失败 | 否 |
| `MODEL_RATE_LIMIT` | 429 | 模型限流，重试后仍失败 | 是 |
| `MODEL_TIMEOUT` | 504 | 模型超时，重试后仍失败 | 是 |
| `MODEL_ERROR` | 502 | 模型 5xx/内部错误 | 是 |
| `INVALID_JSON` | 502 | 模型返回非法 JSON，重试后仍失败 | 是 |
| `SCHEMA_INVALID` | 422 | 输出无法通过完整 Schema | 否 |
| `NO_PARTIES` | 422 | 没有原告或没有被告 | 否 |
| `EMPTY_PARTY` | 422 | 当事人记录所有叶子为 null | 否 |
| `INTERNAL` | 500 | 未分类内部错误 | 否 |

`retryable=true` 只表示调用方可以重试整个请求；服务内部是否重试由编排层按 `retryLimit: 1` 决定。

## 6. 超时、并发和配额

- 单请求超时：60 秒；
- 工作线程：默认 5，可配置 4；
- 有界队列：20；
- 单进程全局模型并发：5；
- 重试 1 次；重试计入配额；
- 多实例共享配额时，后续替换为 Redis/集中式配额，不改变 API。

## 7. 日志与隐私

- 默认不记录 `fileContext`、prompt、模型原始输出；
- 记录：`requestId`、`status`、model、latency、tokens、error.code、subject 数量；
- 测试数据使用模拟文本；如接入真实数据，按部署环境要求做脱敏和保留期限控制。
