# 文档加载与分块

从项目根目录运行（Python 3.9+）：

```powershell
python -m pip install PyYAML==6.0.2
python -m tools.build_knowledge_chunks
python -m unittest evaluation.test_rag_documents -v
```

默认输出 `data/rag/chunks.jsonl`，UTF-8 编码，一行一个片段。该目录是可重建产物，已加入 gitignore。无需数据库、模型或 API Key。

调整参数：

```powershell
python -m tools.build_knowledge_chunks --chunk-size 300 --chunk-overlap 50 --output data/rag/chunks_300.jsonl
```

加载器只读取 manifest 中列出的正文，使用 YAML safe_load 解析 front matter，校验文档标识、标题、分类、医院和状态，拒绝重复条目、越界路径及空正文。元数据字符串（尤其 version 与 updated_at）请保留引号。

分块大小按 Python 字符数计算，不是 token 数。短文档保留整篇，超长文档按二级标题隔离，再优先按段落、句末切开；超长无标点段落只能按字符切开，需人工抽查。重叠只发生在同一章节内，优先复用完整段落，实际重叠可能小于配置值或为零。超过单块长度的章节标题只保留在首块原文，其他块仍通过 section 字段继承标题。

每块继承所有 front matter 元数据，并包含 source_path、section、chunk_id、start_char、end_char 与 text。偏移为加载后正文中的半开字符区间，正文不含 front matter、换行统一为 LF；`body[start_char:end_char]` 等于 text。不要把偏移直接应用到原文件字节或包含 front matter 的文本。

chunk_id 由 doc_id、version、正文位置和片段内容散列生成，同一输入和参数重复导出保持稳定。修改正文或分块配置后需重建索引并移除旧片段；稳定 ID 本身不执行向量库去重或删除。

当前只准备数据，没有执行 Embedding、向量检索或生成回答。检索时可拼接 title、section 和 text 作为 Embedding 输入，保留 text 为原文证据；status 和 source_refs 应继续随引用传递。
