# 发布版环境与运行手册

## 安装

在项目根目录执行：

```powershell
.\scripts\setup.ps1
```

脚本创建 `chAgent` 虚拟环境、安装后端锁定依赖、复制 `backend/.env.example` 为 `backend/.env` 并安装前端依赖。

## 配置

编辑 `backend/.env`，填写 OpenAI 兼容模型服务：

```dotenv
OPENAI_API_KEY=你的模型密钥
OPENAI_BASE_URL=https://你的兼容接口/v1
MODEL_NAME=qwen3.6-35b-a3b
EMBEDDING_ENABLED=true
EMBEDDING_MODEL=text-embedding-v4
EMBEDDING_DIMENSIONS=1024
```

账号变量为 `ADMIN_USERNAME`、`ADMIN_PASSWORD`、`HR_USERNAME` 和 `HR_PASSWORD`。发布模板已经提供本地演示账号，正式使用前请修改密码和 JWT 密钥。

## 启动

```powershell
.\scripts\dev.ps1
```

- 前端：`http://127.0.0.1:5173`
- API 文档：`http://127.0.0.1:8000/docs`

停止：

```powershell
.\scripts\stop.ps1
```

启动脚本会管理 FastAPI、Vite、项目组队 Worker 和本地 stdio MCP 子进程。数据库、上传文件、检查点和运行日志只生成在本机的被忽略目录中。

## 业务默认值

- 每批最多上传 3 份文件，单份最多 15MB，PDF 最多 20 页。
- 父段最多 4000 字符，子段最多 2048 字符。
- 公司工作日默认周一至周五，员工工作日没有个人覆盖时默认 100% 可投入。
- 项目组队需要经过人事提交和管理员批准，批准前不会写入正式排期。

## 验证

```powershell
# 后端：隔离测试（内存数据库 + 合成数据）
cd backend
..\chAgent\Scripts\python.exe -m pytest -q

# 前端：类型检查与构建
cd ..\frontend
npm run build

# 浏览器验收：本机 Edge、独立端口、临时数据库
npm run test:e2e

# 运行时自检
Invoke-RestMethod http://127.0.0.1:8000/health
```

组队相关用例会启动本地 `chAgent` 的 stdio MCP 子进程，因此需要先执行 `scripts/setup.ps1`。Playwright 使用 8001/5174 端口和临时数据库，不会读写正式人才库。
