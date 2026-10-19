# 带引用的 RAG 回答

链路：问题 → Embedding → FAISS Top-K → 证据 Prompt → 聊天模型 JSON → 引用校验 → 答案与来源。

默认复用项目聊天配置：系统环境变量/根目录 `.env.local` 优先，之后读取 `backend/.env` 中的 OPENAI_API_KEY、OPENAI_BASE_URL 和 LC_MODEL。也可使用 RAG_CHAT_API_KEY、RAG_CHAT_BASE_URL、RAG_CHAT_MODEL 独立覆盖。Embedding 配置保持独立，不能把 Embedding 模型名当作聊天模型。

## 命令

在装有 requirements-rag.txt 依赖的环境中执行：

```powershell
python -m tools.ask_knowledge "手语识别错了怎么办？" --output data/rag/answer_demo.json
python -m uvicorn backend.rag_app:app --host 127.0.0.1 --port 8000
```

独立 API 无需 MySQL 和手语模型。原 hospital 主应用也注册同一个端点 `POST /api/knowledge/chat`，请求：

```json
{"question":"手语识别错了怎么办？","top_k":3}
```

使用 `/docs` 调试。question 最长 2000 字符，top_k 范围 1–8。同步路由由 FastAPI 线程池执行，本阶段不包含流式输出。

## 前端咨询入口

在项目根目录另开终端：

```powershell
python -m http.server 8001 --bind 127.0.0.1 --directory frontend
```

打开 http://127.0.0.1:8001/knowledge.html 可直接测试，无需登录、数据库或摄像头；挂号页 http://127.0.0.1:8001/reg.html 右下角也有“医院服务咨询”按钮，以对话框展示同一组件。咨询独立于现有挂号 Agent，不修改挂号会话或槽位。每次问题独立发送，历史记录只用于页面展示，不作为模型上下文。

默认调用 http://127.0.0.1:8000/api/knowledge/chat。独立 RAG 后端允许 localhost:8001 和 127.0.0.1:8001 的跨域请求。若后端正在运行，请重启以加载新增 CORS 配置。其他前端端口需修改允许来源；其他 API 地址可在模块加载前设置 window.HOSPITAL_KNOWLEDGE_API_BASE。

页面展示回答状态、演示标记和可展开引用，引用文本与模型答案通过 textContent 渲染，不执行 HTML。生成失败时展示候选原文并明确未生成已校验回答；检索失败时展示提示。发送时禁止重复提交，可取消等待，失败后保留问题以便重试。取消仅停止浏览器等待，不保证中止已经发出的模型请求。

## 响应与引用

answer 包含后端生成的 [S1] 引用编号；sources 是实际被引用的原文证据，包含 quote、doc_id、chunk_id、标题、章节、路径、状态、分数和外部参考链接。retrieved_sources 是全部检索候选，不应当全部显示为已引用来源。scope_notice 展示演示数据性质，timings_ms 包含检索、生成和总耗时。

生成模型只能给出逐条 claims，每条绑定来源和连续原文。后端拒绝未知来源、非原文 quote、缺少引用的说明和模型自造编号。结构校验失败时不展示模型答案，而返回 generation_unavailable 和检索原文。校验来源存在及 quote 精确匹配，不自动证明语义蕴含；正确性、引用是否充分支持每条说明仍需人工评测。

status 为 answered、clarify、insufficient、business_query、generation_unavailable 或 retrieval_unavailable。前三种及 business_query 由模型根据证据判断，目前并非 LangGraph 或确定性路由。business_query 只说明需要业务查询，没有执行真实号源查询；clarify 的追问暂不维护多轮槽位。

clarify 的 follow_up 是模型生成的追问，可能没有来源；不应解读为已验证的医院事实。insufficient 和 business_query 的 follow_up 由后端固定提示覆盖，避免显示模型未引用的事实说明。本阶段未校准相似度拒答阈值，也未实现对模型无依据事实的完整自动判定。

聊天请求使用 OpenAI 兼容 chat/completions 和 JSON response_format，服务商必须支持此格式。超时和部分临时错误有限重试。日志仅记录请求 ID、状态、片段数与耗时，不记录 Key、患者问题或完整 Prompt。

## 验证

```powershell
python -m unittest evaluation.test_rag_documents evaluation.test_rag_index evaluation.test_rag_answers -v
```

API 测试另需 `httpx==0.27.2`，运行 `python -m unittest evaluation.test_rag_api -v`。

正式评测需补充独立题目和人工答案检查，开发演示成功不能代替回答正确率、引用准确率或业务查询正确率。
