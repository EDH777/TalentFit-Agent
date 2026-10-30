# 企业人才匹配助手技术实现文档

当前版本：本地 MVP 2.3；日期：2026-10-27。

## 1. 技术选型

| 层 | 实现 | 取舍 |
|---|---|---|
| 网页 | React 18、TypeScript、Vite 7、Ant Design 5、React Router 7 | 中文管理界面和紧凑响应式布局，非 SSR |
| API | Python 3.11、FastAPI、Uvicorn、Pydantic | 类型校验、自动 OpenAPI、依赖式权限 |
| 对话/组队 Agent | LangGraph 1.2；独立组队 worker | 对话和项目组队需要可恢复图；简历入库保持顺序函数链 |
| 模型 | OpenAI 兼容接口；`qwen3.6-35b-a3b` | 复用本地授权配置，不在代码中保存密钥 |
| 存储 | SQLAlchemy 2、SQLite、SQLite FTS5 | 本地零外部数据库；当前规模足够，后续可迁移 PostgreSQL |
| 检索 | jieba + BM25、Qwen `text-embedding-v4` 1024维、NumPy余弦、RRF | 父子分块，向量和词法索引共同过滤启停状态 |
| 解析 | pypdf、python-docx、PDFium、RapidOCR ONNX | 坐标顺序、旋转、字体解码和扫描件 OCR 均在本地完成 |
| 组队求解 | OR-Tools CP-SAT、官方 MCP SDK stdio、SQLite 检查点 | 硬约束由确定性服务检查，工具只读且带签名上下文 |
| 验证 | pytest、FastAPI TestClient、Playwright Edge | 隔离数据库、示例夹具和独立真实模型冒烟分开 |

## 2. 架构与数据流

```text
React :5173
   │  HTTP/SSE + Bearer JWT
FastAPI :8000
   ├─ auth/deps：登录、令牌和角色校验
   ├─ candidates/resume：上传、解析、审核、父子分段和索引
   ├─ knowledge：文档/父段/子段管理和召回测试
   ├─ chat：个人会话 + LangGraph 规划/检索/回答
   └─ staffing：员工、排期、项目、运行、方案和审批
             └─ 独立 worker → LangGraph 组队图 → stdio MCP（只读）→ CP-SAT
```

代码职责：

- `backend/app/api/`：HTTP 路由、Pydantic 输入输出和人事/管理员权限。
- `backend/app/services/extraction.py`：PDF/DOCX/TXT/图片提取和 OCR。
- `backend/app/services/resume.py`、`preprocessing.py`：审核、字段抽取、语义整理、父子分段和索引。
- `backend/app/services/knowledge.py`：启停过滤、FTS/向量召回、RRF、分段管理和命中计数。
- `backend/app/services/agent.py`、`tracing.py`：聊天 LangGraph、动态节点事件、脱敏和历史保存。
- `backend/app/services/staffing/`：员工证据、每日可投入比例、需求、确定性求解、方案事务和运行恢复。
- `backend/app/workers/staffing.py`、`backend/app/mcp/`：组队后台执行、检查点和官方 MCP stdio 连接。
- `frontend/src/pages/`、`components/`、`services/`：登录、工作台、知识库、聊天和项目组队界面。

## 3. 数据模型

人才核心表：

| 表 | 关键字段 |
|---|---|
| `users` | id、username、password_hash、role、is_active |
| `candidates` | id、name、`job_category`、years_experience、tags_json、source_filename、file_path、file_sha256、status、deleted_at、created_at、updated_at |
| `resume_chunks` | id、candidate_id、parent_id、chunk_type、position、content、enabled、embedding_json、embedding_model、embedding_dimensions、hit_count |
| `resume_chunks_fts` | FTS5 虚拟表，索引有效子段正文和必要元数据 |
| `chat_sessions` / `chat_messages` | 账号隔离的会话、消息、来源摘要和动态执行记录 |

上传原文和处理文本只在后端保存，不作为上传文档公开响应；人工录入正文仍可由人事维护。`source_type` 和 `desired_position` 已移除，`created_at`同时作为入库时间。`job_category`只允许产品经理、前端开发工程师、后端开发工程师、测试工程师、AI应用开发工程师、现场实施工程师、硬件工程师、采购经理或 `NULL`（待确认）。

组队表由 `models/staffing.py` 管理：员工与简历关联、有效事实缓存、公司工作日、员工每日可投入比例、资源占用、项目、共享消息、运行、事件、方案、动作和资源状态。员工身份使用独立 UUID；关联资料带候选人创建时间和版本快照，不能用同名或复用的资料 ID继承身份。

## 4. 认证与 API 边界

所有业务接口要求 JWT；实际角色从数据库读取，不能信任前端传入的角色。只有 `hr` 能写人才、员工、排期和项目草稿；`admin` 可读并负责项目方案的明确批准，不能写人才资料。

主要接口：

| 方法 | 路径 | 作用 |
|---|---|---|
| `POST` | `/api/auth/login` | 登录并返回令牌与角色 |
| `GET` | `/api/candidates` | 读取人才列表，支持状态、文本和 `job_category` 筛选 |
| `POST` | `/api/candidates/upload/stream` | 1~3份文件的 SSE 入库进度 |
| `POST` | `/api/candidates/manual` | 人事手动录入 |
| `PUT/DELETE` | `/api/candidates/{id}` | 人事修改元数据或删除资料 |
| `GET/POST` | `/api/knowledge/...` | 文档、父子段、启停和召回测试 |
| `POST` | `/api/chat/sessions/{id}/messages/stream` | 个人会话的动态节点和流式回答 |
| `GET/POST` | `/api/staffing/projects/{id}/messages` | 项目共享对话：需求草稿与方案解释，不修改正式需求 |
| `/api/staffing/*` | — | 员工、工作日、每日可投入比例、项目、运行、方案和审批 |

写操作使用版本字段；员工岗位同步和知识库分类写入使用主简历快照/`expected_updated_at` 做 CAS，过期返回409。正式审批另需方案版本、输入指纹和幂等键。

## 5. 简历入库流水线

入库是后端普通函数顺序链，不是 LangGraph：

1. 校验扩展名、大小、PDF页数、空文件和活跃 SHA-256 重复；文件使用 UUID 物理名保存。
2. 按 PDF 显示坐标和页面旋转还原文字；字体解码失败或成片乱码才回退本地 OCR。DOCX读取段落与表格，TXT尝试 UTF-8/UTF-16/GB18030。
3. 调用统一审核和字段抽取，接受个人求职简历与在职员工职业履历；广告、文章、合同拒绝。一次模型调用同时判断姓名、职位描述、年限、最多30项标签和主要 `job_category`；非法岗位值降为待确认，不拒绝合法履历。
4. 审核通过后调用语义整理和规范化。整理只依据原文，不重复审核，不补写经历；结构格式错误最多基于原文重整一次，服务错误和上游审核拒绝不伪装成格式重试。
5. 按固定 `<<<RESUME_PARENT>>>` 和 `<<<RESUME_SECTION>>>` 标志生成语义父子段。父段上限4000、子段上限2048；短语义段保持独立，只有超长段才按换行/句子/空格回退，60字符重叠仅用于同一超长子段内部。父段保留子标志，去标志子段进入 FTS 和向量索引。
6. 在逐份事务中建立索引并发送阶段事件；提交成功后才报告100%。一份失败不阻断同批其他文件。文档页不提供原文、处理文本、分段设置、预览或重新预处理入口。

## 6. 混合检索与知识库管理

关键词路径使用 SQLite FTS5/BM25，向量路径使用 `text-embedding-v4` 和余弦相似度；两路结果通过 RRF 融合。文档、父段和子段启用状态共同过滤所有路径；停用或删除内容不会从旧父段、原文、标签或姓名回退成为 Agent 证据。索引缺失时返回实际降级模式和警告，不把全局索引状态冒充本次混合检索。

知识库页面使用紧凑文档列表。进入员工文档后，父段只显示第一非空行，点击“查看详情”在当前父卡片下展开子段；支持父子段的增删改、启停、搜索、分页和召回测试。修改父段会提醒覆盖其子段；只改元数据不恢复已删除分段。正式聊天回答成功后才增加子段和父段命中计数，召回测试不计数。

## 7. 个人聊天 Agent

LangGraph按实际任务动态执行：规划节点识别匹配、画像、对比或普通问答，必要时检索有效证据，再生成回答。普通问答可跳过检索；画像/对比按完整姓名约束候选人；最多8人，每人只使用有效子段证据。

节点开始/结束事件包含唯一 execution ID、节点类型、标题、状态、耗时和业务摘要，前端按事件顺序纵向展示，不固定节点数量或名称。同名重入和并行节点独立记录，刷新从消息历史恢复。回答通过 SSE 逐段输出，手机号、邮箱、地址和证件字段在进入模型及返回前脱敏；断流只保留未完成状态，不发送伪造的 `done`。

会话与消息按账号隔离；删除本人会话需校验所有权并清理全部消息，迟到的旧响应不能写回新会话。

## 8. 项目组队 Agent、MCP 与审批

组队 worker 使用独立 LangGraph：准备有效主简历事实 → 协调 Agent 路由 → 能力/资源专业子图并行 → 重新核验 → CP-SAT 求解 → 独立校验 → 方案汇总。专业 Agent 最多两轮、只读工具最多20次、运行预算180秒，求解累计5秒。技能和引用必须来自当前有效主简历子段；未知容量保持未知，不把缺证据说成不会。

能力和资源子图在 MCP 查询结束后各自产生受控提案：每条声明只包含员工 UUID、声明类型、规范化键、值和证据引用 ID，并附警告、阻塞项和观察范围；不保存原文引用、联系方式、提示词或授权信息。`reconcile` 节点按声明键归并提案并生成摘要事件。相同声明直接合并；不同声明优先与本次数据库快照核验，能被快照确认的保留权威值并记录已解决差异，无法核验的标记 `AGENT_DISAGREEMENT_UNRESOLVED` 并等待补充。求解器和审批事务仍是硬约束与正式写入的唯一裁决者，Agent 不能通过多数意见覆盖未知或冲突事实。

专业 Agent 首轮若返回空工具调用，图按职责发起一次最小范围只读查询（能力读简历证据，资源读容量，单协调读两者），仍受员工范围、MCP 权限和总预算约束；空查询不能形成成功提案。

MCP 由 worker 启动固定的官方 SDK stdio 程序并复用连接，只提供四个受限只读工具。每次调用通过 host 专用签名 `_meta`传入身份、项目、运行和 fence；凭据不进入模型参数、日志或检查点。

运行事件先持久化再 SSE，使用事件序号重放去重。SQLite 检查点只保存 ID、需求版本、受控摘要和预算；租约过期时废止旧 fence，以新 UUID/thread 最多重算一次。模型只生成未确认需求草稿和解释，不能隐式修改正式需求或执行占用。

正式流程为人事确认需求、生成或人工替换方案、校验并提交，管理员在事务内重新检查来源、身份、输入指纹和逐日可投入比例后批准。幂等动作使用同键同载荷返回原结果，异载荷返回409；浏览器离开只断开订阅，不取消后台运行。

## 9. 项目组队分级简化

默认页面只调用六类业务动作：`prepareEmployees`、`saveAvailability`、`saveProjectRequirement`、`generateRecommendation`、`submitRecommendation`、`approveRecommendation`。这些动作由前端 service 统一封装，后端 facade 再委托现有员工、项目、运行和方案服务；底层版本、指纹、幂等键和权限校验不由前端拼装。

项目详情采用三步条，推荐结果只显示当前有效方案；运行事件、完整能力证据、逐日排期、方案历史和人工替换位于折叠或高级入口。员工准备页只保留员工档案、公司工作日和每日可投入比例，系统准备页承接初始化已有占用与管理员关闭初始化。

界面日期统一按“含”展示：项目结束日期与员工台账结束日期都包含当天，提交时换算为内部排他 `end_date`；求解、容量与排期仍使用 `[start_date, end_date)` 语义。项目工作日不再手工勾选，由公司日历与日期区间自动推导；每个岗位默认全选项目工作日，可按天裁剪实际参与日期（例如采购只在第一周介入），服务端仍校验岗位日期属于项目工作日。页面按“需求 → 推荐 → 排期”分阶段展示，推荐阶段提供“上一步：修改需求”，修改后旧方案自动变为历史版本。

项目详情底部提供“组队助手”共享对话：人事可让模型把一句话需求整理成未确认草稿，草稿只回填需求表单，保存表单才产生新需求版本；管理员只能请求解释，内容限于当前方案、简历依据、排期冲突以及“未入选候选及原因”（来自本次求解的诊断，按姓名和原因列给模型，因此可以回答“为什么不是某人”）。对话不能确认需求、批准方案或写入占用。已确认需求可通过“修改需求”重新打开表单，保存后旧方案标记为 `superseded` 并要求重新生成。

简化不改变 Agent 链路：每次生成仍由独立 worker 创建可恢复 LangGraph，按需并行协调/能力/资源子图，经受限 MCP 读取有效主简历和排期，确定性服务重建候选池，CP-SAT 求解，独立校验后保存推荐。未知可投入比例继续传播为未知，生成不写占用，只有管理员批准事务成功后才落正式排期。正式项目支持人事提前结束或取消，项目级动作在资源写锁内把全部 active/applied 占用转为历史状态并更新项目状态；员工日历中的调整、结束和取消提供单条占用维护。

公司日历由 `services/staffing/capacity.py` 提供默认规则：周一至周五为工作日，周六周日为休息日。`company_work_days` 只保存与默认规则不同的节假日或调休覆盖；读取日历时合并默认值和覆盖值。员工在公司工作日没有 `employee_capacity_days` 覆盖时，快照按100%可投入；人事写入请假/调休比例或显式NULL后才覆盖默认值。非工作日显示为不可安排，不进入项目工作日选择。资源占用支持结束、取消和未来调整：历史记录保留，当前及未来容量只计算 `active/applied` 占用，归档前必须释放当前及未来占用。

员工事实抽取为 `partial` 时，运行进入等待业务资料，不把部分事实当作完整能力。技术名先经过 `backend/app/core/technology_aliases.py` 的 Unicode、大小写、分隔符和显式别名归一化；抽取后还会对有限的常见技术词表执行原文逐字校验补全，并复用主体、否定和引用校验；这不是按关键词猜测，补全项必须有当前有效子段的明确肯定引用。人事可在人员详情点击“重新抽取能力事实”，成功后再从运行详情点击“已补充，重新检查”；主简历有效分段不因抽取重试而改写。项目经历中明确写入的 `technologies` 也会参与必备技能匹配。

## 10. 配置、部署与验证

`backend/.env`使用绝对路径加载，启动脚本管理前后端、worker和MCP子进程。公网部署前仍需 HTTPS、反向代理、限流、正式用户管理、数据库迁移体系、备份和长任务治理。

后端测试使用 `..\chAgent\Scripts\python.exe -m pytest -q`；前端使用 `npm run build` 和 `npm run test:e2e`。浏览器测试使用隔离端口和临时数据库；真实模型脚本只使用隔离数据，并在结果中明确记录实际模型。当前详细结果见 `docs/test-report.md`，运行步骤见 `docs/environment.md`。

