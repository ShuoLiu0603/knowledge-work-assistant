# 手工验收清单

本清单用于阶段 11 的人工验收和项目演示。每次重要改动后，至少跑一遍“核心链路”。

## 1. 本地环境

- [ ] `docker compose -f infra/docker-compose.yml --env-file .env.example up --build` 能启动所有服务。
- [ ] `GET http://localhost:8000/api/health` 返回 `{"status":"ok"}`。
- [ ] `GET http://localhost:8000/api/ready` 返回 `{"status":"ok", ...}`，且 `database`、`redis`、`qdrant`、`minio` 均为 `ok`。
- [ ] 前端 `http://localhost:5173` 可以打开。
- [ ] worker 日志中没有持续重启或连接失败。
- [ ] `python scripts/smoke_demo.py` 可以完成注册、建库、上传、入库、问答和清理。

## 2. 鉴权与权限

- [ ] 新用户可以注册、登录并访问 `/api/me`。
- [ ] 第一个注册用户自动成为 `is_admin=true`、`security_level=5`。
- [ ] 用户 A 创建的私有知识库，用户 B 不能访问详情、文档和会话。
- [ ] 管理员可以创建部门并给用户分配部门；同部门用户能看到部门知识库，其他部门用户不能访问。
- [ ] 普通用户可以创建私人知识库，并能在自己的私人知识库中直接上传、查看和删除文档；上传时不需要选择密级。
- [ ] 管理员可以创建公开知识库；普通用户能看到和检索公开知识库，但不能向公开知识库上传或删除文档，直接调用接口会返回 `403` 并产生审计记录。
- [ ] 普通用户在公开/部门知识库中只能检索到 `security_level <= 自身清级` 的文档 chunk；在自己的私人知识库中可读取全部文档。
- [ ] refresh token 可以刷新 access token。
- [ ] 登出后被撤销的 refresh token 不能继续刷新。

## 3. 文档入库

- [ ] 管理员向公开知识库或 owner/editor 向私人知识库上传 `demo/company_policy_demo.md` 后，文档状态从 `uploaded` 进入处理流程。
- [ ] 文档最终变为 `indexed`。
- [ ] `chunk_count > 0`。
- [ ] `GET /api/documents/{document_id}/chunks` 能看到切分结果。

## 4. RAG 问答

- [ ] 对“住宿报销上限是多少？”提问能返回答案。
- [ ] 答案包含引用对象，引用来源能指向 demo 文档。
- [ ] 检索解释中能看到 rewritten query、routes、candidates、selected chunks。
- [ ] 空知识库或无相关内容时，回答能说明依据不足，并给出改写问题、检查入库状态或联系管理员的建议。

## 5. 流式会话

- [ ] 前端问答页能逐段显示回答。
- [ ] 刷新页面后，历史会话和消息仍在。
- [ ] 会话可以删除，删除后消息不会再出现在历史记录中。
- [ ] 引用面板可以查看来源 chunk。
- [ ] `conversations.summary` 会在多轮会话后更新。

## 6. Agent 编排

- [ ] 普通问题进入 RAG Agent。
- [ ] “请总结……”进入 Summary Agent。
- [ ] “请写一份……”进入 Writing Agent。
- [ ] `GET /api/agent-runs` 能看到 trace 和 AgentState 快照。

## 7. 记忆系统

- [ ] 输入 `I prefer concise answers` 产生 `create`。
- [ ] 再次输入同样偏好产生 `touch`，active 记忆不重复增加。
- [ ] 输入 `I prefer detailed answers` 产生 `supersede`，旧偏好变为 `superseded`。
- [ ] 输入相近偏好产生 `merge`。
- [ ] 输入 `Do not remember this temporary note` 产生 `ignore`。
- [ ] 后续回答能读取 active 偏好并做基础个性化。

## 8. 评估与展示

- [ ] `/admin` 只有管理员可访问，普通用户访问会回到问答页。
- [ ] 管理员控制台能查看全局指标、部门、用户清级、数据源入口和安全审计。
- [ ] `python scripts/run_eval.py ...` 能输出 Recall@K、MRR、citation_hit_rate。
- [ ] README 可以指导一个新用户完成启动、上传、提问和评估。
- [ ] 架构图、RAG pipeline、Agent 编排文档能支撑面试讲解。
