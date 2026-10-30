# 黄金集格式

## 0. 已生成

已根据 `file_raw.json` 生成 L001-L005 五个案件，全部为 `status=ok`。每个案件目录包含 `input.json`、`expected.data.json`、`expected.review.json`、`expected.meta.json`、`notes.md`。业务判断见各案 `notes.md`。

## 1. 你提供什么

Stage 0 只需要你提供**模拟民间借贷纯文本**，不需要 DOCX、不需要 JSON。

推荐目录：

```text
stage0/fixtures/raw/
  L001.txt
  L002.txt
  ...
```

如果文本已经按段落分好，也可以直接给 `fileContext` 数组：

```json
{
  "caseId": "L001",
  "fileContext": [
    "原告：……",
    "被告：……"
  ]
}
```

纯文本转 `fileContext` 的默认规则：

- 以空行分段；
- 没有空行时，整个文件作为 1 个 item；
- 每个 item 保留原文，不做改写；
- 证据的 `fileContextIndex` 和字符区间按转换后的数组计算。

## 2. 黄金集目录

每个案件一个目录：

```text
stage0/fixtures/cases/L001/
  input.json            # 完整请求体，符合 input.schema.json
  expected.data.json    # 期望业务 data
  expected.review.json  # 期望 review
  expected.meta.json    # 期望 status/error，例如 {"status":"ok","error":null}
  notes.md              # 覆盖点、人工判断
```

`input.json` 示例：

```json
{
  "requestId": "fixture-L001",
  "templateId": "private_lending_complaint",
  "schemaVersion": "0.1.0",
  "fileContext": [
    "原告：……",
    "被告：……"
  ]
}
```

`expected.data.json` 使用根目录 [loan_complaint.schema.json](../../loan_complaint.schema.json) 的 9 个顶层键；`expected.review.json` 使用 [review.schema.json](../review.schema.json)。

## 3. 建议覆盖的 10 个场景

| 编号 | 场景 | 期望状态 |
| --- | --- | --- |
| L001 | 单自然人原告 + 单自然人被告；无书面合同；无担保；无代理人 | `ok` |
| L002 | 组织原告 + 自然人被告；有委托代理人；有信用代码/法定代表人 | `ok` |
| L003 | 多原告 + 多被告；分别记录 | `ok` |
| L004 | 多当事人，部分有代理人、部分没有 | `needs_review` |
| L005 | 有物保 + 有保证合同，担保字段完整 | `ok` |
| L006 | 明确“没有担保”“没有保证合同”，条件字段全 null | `ok` |
| L007 | 只写“2026年3月底前归还”，无完整截止日 | `ok`（日期 null，原文保留） |
| L008 | 本金/利息/标的总额不一致 | `needs_review` |
| L009 | 没有被告，或被告主体全空 | `error` |
| L010 | 同一主体名称/地址在不同段落冲突 | `needs_review` + conflict |

可以额外加：

- 明确“无委托代理人” vs 完全没提代理人；
- 明确“无管辖约定” vs 没提管辖；
- 部分还款、已还本金/利息；
- 利率只写“LPR 的 4 倍”或“无息”；
- 金额大写/小写同时出现；
- 多个电话号码/多个地址。

## 4. 期望输出怎么确认

- 你只提供文本时，我生成 `expected.data.json` 和 `expected.review.json`；
- 你审核并确认后，黄金集冻结；
- 若你同时提供期望 JSON，我按你的版本为准，只补 Schema 兼容性检查。

## 5. 验收标准

- 每个 `input.json` 通过 [input.schema.json](../input.schema.json)；
- 每个 `expected.data.json` 通过 [loan_complaint.schema.json](../../loan_complaint.schema.json)，并启用 `FormatChecker`；
- 每个 `expected.review.json` 通过 [review.schema.json](../review.schema.json)；
- evidence 的 `quote` 必须等于对应 `fileContext` 字符区间；
- `status=ok` 的案例必须通过全部业务规则；
- `needs_review` 的案例必须至少有一个 warning/conflict/missing；
- `error` 的案例必须没有 `data`，且有明确错误码；
- 不出现编造值、从身份证反推、从沉默推“否”等违反核心规则的值。
