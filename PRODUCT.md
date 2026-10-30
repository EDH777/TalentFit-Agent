# TalentFit Agent

<!-- impeccable:product-schema 1 -->

## Platform

web

## Users

单公司人事（hr）维护员工简历与资源，管理员（admin，老板）查看并审批项目组队。两种角色共用组队业务记录；原人才助手会话按账号隔离。

## Product Purpose

将原 Dify 人才助手和知识库迁移为可维护的前后端应用，并增加基于内部员工简历与明确工时台账的项目组队。模型帮助理解需求与引用能力证据，确定性求解器检查技能和逐日容量，管理员明确批准后才占用排期。

## Operating Context

本地 Windows MVP；React、TypeScript、Ant Design，FastAPI、SQLAlchemy、LangGraph、SQLite。沿用紧凑中文业务界面。组队页面按指导文档分为项目需求、组队助手、组队方案三个区域；员工容量维护属于项目组队内部入口。

## Capabilities and Constraints

- 简历均为内部员工资料，员工 UUID 与资料 ID 独立，禁止按姓名自动合并。
- 技能与经历只引用有效主简历子段；工时来自独立台账，未知不视为空闲。
- 人事维护和提交；管理员审批并占用，聊天不能代替执行按钮。
- 原人才助手、知识库、上传限制及个人会话隔离保留。
- 13px基础、20px标题、44px顶栏；桌面与390px窄屏，宽表格局部滚动。
- 不展示上传原文、处理中间文本、模型思维链或密钥。

## Evidence on Hand

以上内容直接来自用户交付的 `docs/superpowers/specs/2026-10-26-project-staffing-implementation-guide.md` 和既有 `AGENTS.md`、`DESIGN.md`；无新增产品假设或视觉重设计。
