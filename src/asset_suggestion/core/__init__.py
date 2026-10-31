"""核心业务逻辑层。

对应《公共设施报修与资产协同 · 首期系统设计方案》§5–§14：

    匹配层五阶段  config/db/districts/extract/anchor/recall/rank/gates
    治理四模块    audit(留痕) / authz(授权) / handover(交接) / sync(降级)
    编排与度量    service / metrics / replay / seed / api / llm

本层只允许依赖 utils/ 与标准库，不依赖项目外的第三方库。
"""
