# ADR 索引（Architecture Decision Records）

架构决策记录。每个 ADR 记录一个不可逆或高成本回退的技术决策：
背景、决策、备选方案与后果。

新 ADR 从 `0001` 开始编号，文件名 `NNNN-短标题.md`，状态使用
Proposed / Accepted / Superseded by NNNN。

| 编号 | 标题 | 状态 | 日期 |
|------|------|------|------|
| [0001](0001-redis-as-source-of-truth.md) | Redis 作为实时状态真相源，PG 作为镜像 | Accepted | 2026-08-23 |
| [0002](0002-prompt-externalization-fail-fast.md) | Prompt 全量外置 YAML，缺失即启动失败 | Accepted | 2026-08-23 |
| [0003](0003-multi-model-fallback-cooldown.md) | 多模型源按序切换 + 失败冷却 | Accepted | 2026-08-23 |
