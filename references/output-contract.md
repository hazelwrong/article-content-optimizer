# 优化交付格式

## 单篇默认输出

```yaml
article: 标题或 URL
route: targeted_edit | evidence_rebuild | retitle | merge | technical_fix | human_review
baseline:
  hard_gates: []
  score: null
  decisive_evidence: []
promise:
  current: ""
  revised: ""
data_gaps:
  - field: ""
    needed_for: ""
    source: ""
    status: missing | verified | remove_claim
changes:
  - location: ""
    action: keep | delete | rewrite | add | merge
    reason: ""
deliverable:
  optimized_file: ""
  source_ledger: []
recheck:
  hard_gates_closed: []
  hard_gates_remaining: []
  score_after: null
  final_decision: 通过上线 | 先发后改 | 人工复核 | 不发重改
soft_tags: []
```

正文较短时可以用中文表格表达同样字段，不要求机械输出 YAML。

## 批量输出

先给批次统计，再逐篇列：`路由、阻断、所需数据、修改动作、owner、SLA、复审结论`。相同模板错误要单独生成“生产规则修复”项，避免只修当前样本。

## 数据不足

关键事实缺失时交付数据缺口清单和可安全保留的正文，不输出伪完成稿。明确哪些字段补齐后可以继续，以及标题是否应降级。

## 文件命名

- HTML：`原文件名-optimized.html`
- Markdown：`原文件名-optimized.md`
- 批量报告：`article-optimization-report-YYYY-MM-DD.csv|xlsx|md`

不要覆盖原文件，除非用户明确要求。
