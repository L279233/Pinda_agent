# 聘达智能招聘系统

聘达智能招聘系统是一个面向高校就业与企业招聘场景的智能化平台，整合岗位管理、简历解析与评估、在线考试、智能面试、招聘知识库和统一问答等能力，帮助招聘方和求职者完成从信息发布到人才评估的协同流程。

## 主要功能

- 用户认证与角色权限管理
- 招聘岗位、申请记录和候选人流程管理
- 简历上传、结构化解析、岗位匹配与智能评估
- 在线考试、自动评分和考试结果查看
- 多阶段智能面试与实时流式对话
- 企业招聘资料与知识库管理
- 基于知识库的智能问答和 Web 搜索补充
- 招聘通知、任务和应用状态管理

## 技术架构

- **前端**：Vue 3、TypeScript、Vite、Pinia、Element Plus、Axios
- **后端**：Python、FastAPI、SQLAlchemy、Pydantic
- **智能编排**：LangChain、LangGraph、MCP
- **模型能力**：DeepSeek API；本地 Embedding、Reranker 和分类模型
- **数据与基础设施**：PostgreSQL、Milvus、MinIO、etcd、Docker Compose

项目采用前后端分离架构。前端位于 `frontend/`，后端 API 和智能体位于 `backend/`，基础设施服务由根目录的 `compose.yaml` 管理。

## 环境要求

- Python 3.11+
- Node.js 18+
- Docker Desktop 或 Docker Engine + Compose
- 可用的 DeepSeek API Key（使用智能问答、简历评估或面试能力时需要）

## 快速开始

### 1. 配置环境变量

复制示例配置并填写实际值：

```bash
cp .env.local.example .env.local
```

Windows PowerShell：

```powershell
Copy-Item .env.local.example .env.local
```

至少需要检查数据库连接、MinIO 配置、JWT 密钥和 `DEEPSEEK_API_KEY`。真实密钥不要提交到 Git 仓库。

### 2. 启动基础设施

```bash
docker compose up -d
```

该命令启动 PostgreSQL、Milvus、MinIO 和 etcd。首次使用时可根据需要执行 `scripts/init_db.sql` 及 `scripts/init_milvus.py`。

### 3. 安装并启动后端

```bash
python -m venv .venv

# macOS/Linux
source .venv/bin/activate

# Windows PowerShell
.\.venv\Scripts\Activate.ps1

pip install -r backend/requirements.txt
uvicorn backend.main:app --reload
```

后端默认地址为 `http://localhost:8000`，API 文档位于 `http://localhost:8000/docs`。

### 4. 安装并启动前端

```bash
cd frontend
npm install
npm run dev
```

前端默认地址为 `http://localhost:5173`。如需生产构建：

```bash
npm run build
```

## 测试

在项目根目录执行：

```bash
pytest
```

仓库中的 `scripts/` 和 `backend/**/test_*.py` 还包含数据初始化、服务检查及端到端验证脚本。执行依赖外部服务的测试前，请先启动 Docker Compose 服务并完成环境变量配置。

## 模型文件

本地模型权重体积较大，已通过 `.gitignore` 排除，不应直接提交到 GitHub。请根据实际部署需求下载并放置到 `backend/models/` 对应目录，或调整配置使用远程模型服务。

## 目录结构

```text
backend/                 FastAPI 后端、业务服务和智能体
frontend/                Vue 3 前端
scripts/                 初始化、检查和测试脚本
sample_recruitment_docs/ 示例招聘知识库文档
compose.yaml             PostgreSQL、Milvus、MinIO 等基础设施
.env.local.example       环境变量配置模板
```

## 安全说明

- `.env.local` 及其他真实环境配置不会提交到仓库。
- 请为生产环境生成随机且足够长度的 `JWT_SECRET_KEY`。
- 不要在日志、测试数据或提交记录中写入 API Key、数据库密码和用户隐私信息。
- 生产部署前请修改 Compose 中的默认账号密码，并限制数据库、Milvus 和 MinIO 的外部访问。

## 许可证

当前项目尚未指定开源许可证。公开发布前请根据项目版权和使用方式补充 `LICENSE` 文件。
