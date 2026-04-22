# Phase 1.7 — 企业知识库前端产品化重构

> 本阶段只重构前端产品外观、信息架构和页面体验；保留 RAGFlow / Agent v2 的全部后端能力、API、鉴权、数据模型和现有功能路由。

## 背景

Phase 1 已完成 Agent v2 后端、SSE 会话、工具调用可视化、模板系统和脚注引用。当前产品仍大量保留 RAGFlow 原版前端外壳、品牌露出和工程师向的信息架构，不适合作为我们自己的企业知识库产品对外演示。

项目内已有参考稿：

- `design-refs/zhiyuan/project/知源 · 企业知识库.html`
- `design-refs/zhiyuan/project/design-canvas.jsx`

参考稿定位为「知源 · 企业知识库」，采用 Linear/Vercel 风格：冷中性色、teal 品牌色、轻量边框、紧凑企业工作台、三列 Agent 对话体验。

## 目标

1. 把第一眼的产品识别从 RAGFlow 改成我们的企业知识库产品。
2. 用「知源」参考稿统一全局 shell、导航、首页、登录页、Agent 工作台和主要业务页面风格。
3. 保留全部功能：知识库、聊天、Agent 工作台、搜索、Agent 编排、记忆、文件、用户设置、管理台、分享页、组件能力都继续可访问。
4. 避免一次性大爆炸重写：先换全局外壳和高频页面，再逐步替换各业务模块内部 UI。

## 非目标

- 不改后端接口、数据库结构、Agent v2 运行时。
- 不删除原 RAGFlow 功能。
- 不重写业务逻辑和数据请求层。
- 不引入新的大型前端框架。

## 设计原则

- **功能保留优先**：UI 改造必须复用现有 hooks、services、routes 和权限逻辑。
- **参考稿优先**：视觉 tokens、导航结构、首页密度、Agent 三列布局优先参考 `design-refs/zhiyuan`。
- **企业场景优先**：文案从开源 RAG 引擎转向企业知识库、可信引用、Agent 协作、数据治理。
- **渐进迁移**：每批改造都保持可运行，并更新 `STATUS.md` 交接下一批入口。

## 分批计划

### P1.7-A — 全局品牌与外壳

- 更新全局品牌名、Logo 组件和基础 tokens。
- 重构全局 shell 为左侧企业工作台导航（参考 `design-refs/zhiyuan/project/ui.jsx` 的 Sidebar / Topbar 模式）。
- 移除公网 RAGFlow / Discord / GitHub 的显眼入口，保留帮助、语言、主题、通知、用户设置。
- 重构登录页为企业知识库产品入口。
- 重构首页为企业知识库概览，继续复用真实知识库 / 应用列表数据。

### P1.7-B — Agent / 对话工作台统一

- 将 `/agent-chat` 与全局 shell 视觉完全统一。
- 保留现有 SSE、Session、工具调用、引用、模板能力。
- 优化空状态、新建会话、工具侧栏与引用阅读体验。
- 将原「对话」详情页向 `/agent-chat` 的工作台体验靠齐：左会话栏 / 中消息区 / 右设置栏、暗色 token、阅读宽度、选中态。

### P1.7-C — 知识库与文档页面

- 重构 `/datasets`、`/dataset/**`、文档列表、解析状态、检索测试、知识图谱页面。
- 保留文档上传、解析配置、chunk 预览、知识图谱、权限等功能。

### P1.7-D — 其余功能收口

- 重构搜索、Agent 编排、记忆、文件管理、用户设置。
- 统一空状态、表格、卡片、弹窗、表单、侧栏和响应式行为。
- 最后扫掉 RAGFlow 显性品牌文案与旧视觉碎片。

## 验收标准

- 登录后主路径不再显得是原版 RAGFlow 前端。
- 所有已有路由仍可访问，核心功能按钮仍在。
- 本批改动文件 targeted ESLint 通过。
- 关键入口模块可被 Vite 正常转换返回（开发期用本地 dev server 抽查）。
- 全量 `npm run type-check` 当前存在上游/既有 TS 债，不作为 P1.7 单批阻塞项；若本批引入新类型错误，需要单独修复。
- 至少手动检查：登录页、首页、知识库列表、Agent 工作台、文件管理、用户设置。

## 当前入口

当前继续入口：

1. `web/src/pages/next-chats/chat/*` — 对话页继续向 Agent 工作台细节对齐
2. `web/src/pages/agent-chat/*` — 与全局左侧 shell 的 spacing / token 收口
3. `web/src/pages/datasets/*`、`web/src/pages/dataset/**` — 下一批核心业务页
4. `web/src/locales/zh.ts` / `web/src/locales/en.ts` — 新产品文案
5. `web/src/global.less`、`web/src/layouts/*` — 全局主题和 shell
