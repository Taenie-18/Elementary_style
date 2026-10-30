# 模型适配层接口

## 1. 目标

把“模型调用”从抽取编排中隔离出来，使 Stage 1 可以先接 mock、再接 Qwen3.8，而不改业务代码。

适配层只负责：

- 发起一次模型请求；
- 传入 system prompt、user prompt 和可选的 JSON Schema 投影；
- 返回原始文本、解析后的 JSON、token 用量、耗时和提供方请求 ID；
- 把网络/鉴权/限流/超时/上下文超限等错误归一为固定异常。

适配层**不负责**：

- 业务字段抽取；
- 任务拆分、主体索引、合并、review；
- 重试策略（由编排层决定）；
- 全局限流/配额（由编排层在调用前获取配额）；
- 完整 Schema 校验（由编排层在解析后执行）。

## 2. 接口定义

Stage 1 的实现应满足下面的 Python `Protocol`。这里只定义契约，不落地代码。

```python
from dataclasses import dataclass, field
from typing import Any, Mapping, Protocol


@dataclass(frozen=True)
class ModelRequest:
    request_id: str
    system_prompt: str
    user_prompt: str
    json_schema: Mapping[str, Any] | None = None
    temperature: float = 0.0
    max_tokens: int = 4096
    timeout_s: float = 60.0
    metadata: Mapping[str, str] = field(default_factory=dict)


@dataclass(frozen=True)
class ModelUsage:
    prompt_tokens: int
    completion_tokens: int
    total_tokens: int


@dataclass(frozen=True)
class ModelResponse:
    request_id: str
    provider_request_id: str | None
    model: str
    content: str
    parsed: Mapping[str, Any] | None
    usage: ModelUsage
    finish_reason: str | None
    latency_ms: int


class ModelAdapter(Protocol):
    def generate_json(self, request: ModelRequest) -> ModelResponse: ...

    def healthcheck(self) -> bool: ...
```

### 字段说明

| 字段 | 约束 |
| --- | --- |
| `request_id` | 调用方生成，用于日志和 review 关联；不传给模型，除非 provider 支持 |
| `system_prompt` | 任务级系统提示，包含角色、禁止编造、只依据输入 |
| `user_prompt` | 输入文本 + 字段规则 + JSON Schema 投影；不得包含未脱敏的真实案件数据 |
| `json_schema` | 可选的 provider 投影 Schema；不是完整业务 Schema |
| `temperature` | 默认 0，不允许模型随机发挥 |
| `max_tokens` | 默认 4096；必须小于 provider 的输出上限 |
| `timeout_s` | 默认 60 秒；适配层必须遵守 |
| `parsed` | 模型返回的 JSON 对象；解析失败时为 `None`，同时抛 `InvalidJSONError` |
| `usage` | 用于配额、成本和性能统计；provider 不返回时填 0，但必须保留字段 |
| `provider_request_id` | 排障用；没有则为 `None` |
| `latency_ms` | 从发请求到收到完整响应的墙钟时间 |

## 3. 调用语义

1. **一次调用只发一次网络请求。** 重试由编排层控制，适配层不内部重试。
2. **线程安全。** 适配层必须能被 4-5 个工作线程同时调用；HTTP 客户端连接池必须线程安全。
3. **不静默截断。** 输入超过上下文限制时抛 `ContextLengthError`，由编排层拆任务或报错。
4. **不吞错误。** 所有失败必须抛出明确异常；不能返回空字符串冒充成功。
5. **不记录敏感内容。** 默认不打印 prompt、输入文本、模型输出；只记录 `request_id`、model、latency、tokens、finish_reason、错误码。
6. **结构化输出优先。** provider 支持 JSON Schema 时传 `json_schema`；只支持 JSON mode 时传 `json_object`；都不支持时用提示词约束 + 本地解析。
7. **解析与校验分离。** 适配层只解析 JSON；字段/类型/枚举/业务规则由编排层用完整 Schema 和业务规则校验。
8. **不自动修复 JSON。** 不补引号、不截断、不猜测；无效 JSON 抛 `InvalidJSONError`，由编排层决定是否重试。

## 4. 错误分类

| 异常 | 触发条件 | 是否重试 | 编排层处理 |
| --- | --- | --- | --- |
| `AuthError` | API key 无效/过期/无权限 | 否 | 终止，返回配置错误 |
| `BadRequestError` | 请求参数/provider 不支持 | 否 | 终止，记录 provider 错误 |
| `ContextLengthError` | 输入或输出超过上下文 | 否 | 拆分任务或终止 |
| `RateLimitError` | 429/限流，可能带 `retry_after_s` | 是 | 等待后重试，计入配额 |
| `TimeoutError` | 超过 `timeout_s` | 是 | 重试 1 次，仍失败则报错 |
| `ServerError` | 5xx/provider 内部错误 | 是 | 重试 1 次 |
| `ContentFilterError` | provider 内容安全拦截 | 否 | 终止，进入人工处理 |
| `InvalidJSONError` | 返回内容不是合法 JSON | 是 | 重试 1 次，仍失败则报错 |
| `InvalidResponseError` | 缺少必要字段/响应结构异常 | 是 | 重试 1 次 |

重试上限按配置 `retryLimit: 1`；重试次数计入全局模型调用配额。

## 5. Qwen3.8 接入约束

后续使用 Qwen3.8 API。适配层应做成 Qwen adapter，而不是让业务代码直接依赖 Qwen SDK。

建议配置全部来自环境变量：

```text
QWEN_BASE_URL
QWEN_API_KEY
QWEN_MODEL
QWEN_TIMEOUT_S=60
QWEN_MAX_TOKENS=4096
```

接入时确认：

- 是否 OpenAI-compatible `/chat/completions`；
- 是否支持 `response_format={"type":"json_object"}`；
- 是否支持 JSON Schema/function calling；
- 是否支持关闭 thinking（如 `enable_thinking: false`），以降低抽取延迟；
- 实际 model 名称、上下文长度、输出上限和速率限制。

如果 Qwen3.8 只支持 JSON mode，适配层仍然返回 `content` 和 `parsed`；完整 Schema 校验在编排层完成。若 provider 对 `json_schema` 的支持不完整，按供应商能力生成投影，不要取消后端完整校验。

## 6. 与编排层的关系

```text
请求进入
  → 获取全局模型配额
  → 建立/读取共享主体索引（一次模型调用）
  → 任务 1/2/3/4（必要时 facts A/B 拆分）
  → 每个任务：适配层 generate_json()
  → 本地解析 + 完整 Schema 校验
  → 失败：按错误分类重试 1 次
  → 合并：subjectId + 字段路径
  → 业务校验 + review
  → 返回 {requestId, status, data, review}
```

配额在调用适配层之前获取；适配层本身不做全局限流。这样单进程可以用信号量，多实例可以换成 Redis，不改适配层接口。

## 7. 测试替身

Stage 0/Stage 1 早期应提供 `MockModelAdapter`：

- 从 `fixtures/cases/<caseId>/model-response.json` 读取固定响应；
- 不访问网络；
- 可模拟 `TimeoutError`、`RateLimitError`、`InvalidJSONError`；
- 用于跑通编排、合并、业务校验和 API，不用于评估准确率。

## 8. 适配层验收标准

- 同一 `request_id` 的响应可关联；
- 超时、429、5xx、无效 JSON 可被编排层识别；
- 4-5 线程并发调用不共享可变状态；
- 返回 `usage` 和 `latency_ms`；
- 不在日志中输出 prompt/案件原文；
- Qwen3.8 接入只替换 adapter，不改任务拆分、合并和业务规则。
