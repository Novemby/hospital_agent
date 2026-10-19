# Embedding API 与 FAISS 检索

## 安装

独立检索命令不需要 FastAPI、MySQL 或手语模型。建议使用单独的 Python 3.9–3.12 虚拟环境，避免旧版 requirements.txt 的 NumPy/Torch 固定版本冲突：

```powershell
python -m venv .rag-venv
.\.rag-venv\Scripts\python.exe -m pip install -r requirements-rag.txt
```

## 配置

把 `.env.rag.example` 的字段追加到项目根目录 `.env.local`，保留已有配置。填写真实服务商的 Embedding 模型名、API 根地址及 Key。系统环境变量优先于文件配置。

```dotenv
RAG_EMBEDDING_BASE_URL=https://api.siliconflow.cn/v1
RAG_EMBEDDING_MODEL=BAAI/bge-m3
RAG_EMBEDDING_API_KEY=你的Key
RAG_EMBEDDING_BATCH_SIZE=16
RAG_EMBEDDING_TIMEOUT=30
RAG_EMBEDDING_MAX_RETRIES=2
```

当前适配 OpenAI 兼容 `/embeddings` 接口：发送 model、input 字符串列表及 encoding_format=float，读取 data 中的 index 与 embedding。Base URL 不包含末尾 `/embeddings`。不兼容此协议的服务需要单独适配，聊天模型和 Embedding 模型分别配置。

本项目选择 SiliconFlow（硅基流动）的 BAAI/bge-m3。其官方接口支持上述请求格式，模型最大输入为 8192 tokens；当前约 500 字的片段留有余量。向量维度以 API 实际响应为准，不手动设置 dimensions。参考：[硅基流动 Embedding API 文档](https://api-docs.siliconflow.cn/docs/api/embeddings-post)。

首次建库将 23 份演示知识正文及标题、章节发送至所配置的 API；每次查询将问题发送至该 API。不发送患者画像，不调用聊天模型。批大小受服务商限制，可减小配置值；不要把真实 Key 提交到仓库。

## 建库与检索

```powershell
.\.rag-venv\Scripts\python.exe -m tools.build_knowledge_index
.\.rag-venv\Scripts\python.exe -m tools.search_knowledge "手语识别错了怎么办？" --top-k 3
```

也可通过 --chunks 和 --index-dir 为不同分块实验分别建库；搜索时使用相同路径。输出结果包含相似度 score、正文、章节、doc_id、chunk_id、source_path、source_refs 和 demo_unreviewed 状态，可用于下一步构造带来源的 Prompt。score 是余弦相似度，不是答案正确概率；当前不设置未经评测的拒答阈值。

## 索引结构与更新

`data/rag/index/manifest.json` 保存模型、地址、文本格式、向量维度、片段数量与校验和。每次全量重建写入一个新 generation 目录，其内为 index.faiss 与 chunks.json，写完才原子替换 manifest。这样失败时仍可使用之前发布的索引，成功重建不会重复添加旧片段。旧 generation 暂保留，不自动删除。

使用 L2 归一化向量和 FAISS IndexFlatIP 做精确余弦检索，适合当前小知识库。使用 JSON 保存原文，不使用 pickle。查询时校验模型配置、维度、文件校验和和原始 chunks 文件是否改变；发现不一致需要重建。服务商在同名模型下更新权重无法靠模型名称识别，正式实验应固定服务版本并记录服务商信息。

API 连接错误、超时、408、429 和部分 5xx 有有限重试；401/403 等配置错误立即失败。不会用随机向量或关键词匹配冒充成功的 Embedding。

## 验证

```powershell
.\.rag-venv\Scripts\python.exe -m unittest evaluation.test_rag_documents evaluation.test_rag_index -v
```

单元测试用受控向量和模拟 API 验证排序、持久化、元数据、重建、损坏检测、响应顺序和重试，不代表真实语义检索效果。填入真实 API 配置后，应使用 evaluation/rag_knowledge_smoke_cases.jsonl 检查命中材料。此阶段只做检索，不生成回答或判断业务路由。

运行真实 API 检索验收：

```powershell
.\.rag-venv\Scripts\python.exe -m tools.check_knowledge_retrieval --top-k 3
```

逐题原文、分数、来源和耗时保存在 `data/rag/retrieval_smoke_top3.json`。召回率仅统计 expected_route=knowledge 的题目，使用文档级标注；其他题保留检索结果，但不计入知识召回分母。该开发验收结果不等于独立测试集成绩、回答正确率或路由正确率。
