# 演示数据

`company_policy_demo.md` 是用于本地演示的企业制度文档，覆盖差旅报销、发票要求、知识库问答规范和当前单 Agent 工具循环约定。

推荐演示路径：

1. 启动本地服务。
2. 注册一个演示用户。
3. 创建知识库：`企业制度演示知识库`。
4. 在知识库详情页上传 `demo/company_policy_demo.md`。
5. 等待文档状态变为 `indexed`。
6. 到“问答”页提问：`住宿报销上限是多少？`
7. 查看答案、引用来源、检索解释、Agent trace 和长期记忆面板。
8. 使用 `demo/rag_eval_questions.json` 运行 RAG 评估脚本。
