# hospital_sys RAG 演示知识库

本目录包含 23 份知识正文：10 份科室介绍、4 份挂号流程、4 份检查事项、4 份无障碍服务和 1 份服务边界。适合初步分块、检索、引用展示和未知问题兜底测试。当前没有执行 Embedding、建库或接入问答接口。

## 数据性质

所有文档属于演示数据，状态为 demo_unreviewed，没有对应真实医院。科室介绍是编辑编写的服务概述，不是已核验的医院科室官方说明。检查文档提供信息核对流程，不提供个体化医学方案。项目功能依据代码编写，运行情况仍需联调验证。

source_type 区分 project_demo（项目／演示编写）、editorial_demo（编辑编写）、reference_adapted（外部资料概括）和 reference_and_project（政策与项目功能结合）。source_refs 的代码路径或外部链接不意味着整篇文档逐句得到来源支持。引入真实医院时逐条审核并更新 status、hospital_id、来源和日期。

## 导入约定

- 只把 manifest.json 的 documents 列表中的 Markdown 正文作为知识文档；不要把 README、manifest 和评测问题向量化。
- 解析 YAML front matter 后按二级标题和段落分块。建议先尝试 400～600 中文字符、约 80 字重叠；短文档可保留整篇。
- 每个块继承 doc_id、title、category、hospital_id、status、version、updated_at、source_type、source_refs；附加 section、chunk_id 和文本偏移。
- 用 doc_id、version、段落位置及内容散列生成稳定 chunk_id，重复导入时避免重复写入。
- 把演示性质随检索结果一起传给生成模型，并在回答或引用卡片中展示。不要只在 README 标注后丢弃这一边界。
- 引用展示文档标题、doc_id、段落、原文片段及适用范围。外部参考来源与本地知识证据分别展示，不能把本地自编内容伪装成官网原文。
- 涉及实时号源和挂号结果时走业务查询；涉及未收录的院内位置、收费或服务时间时兜底。

## 来源与核验

项目依据：backend/config.py、backend/routers/registrations.py、backend/routers/agent.py、backend/utils/langchain_agent.py、backend/routers/sign_language.py、frontend/js/scene1.js、frontend/js/handsign_assist.js。

政策参考：[关于开展残疾人友好医疗机构建设的意见](https://app.www.gov.cn/govdata/gov/202508/27/535382/article.html)，2025 年文件，本次于 2026-10-02 查阅。只用于无障碍服务建设背景，不能据此承诺本院设施或人员排班。

检查参考：[中国医药大学附设医院门诊检验组](https://www.cmuh.cmu.edu.tw/Department/CustomPage/75)，本次于 2026-10-02 查阅。只用于抽血检验准备与项目有关的一般说明；没有复制该院具体时长、楼层或工作时间作为演示医院规则。

## 检索验收问题

evaluation/rag_knowledge_smoke_cases.jsonl 提供 16 个初始人工标注问题，包括可回答、需追问、需实时查询和信息不足四类。expected_doc_ids 是文档级相关证据，不是正式 chunk 级 Recall@K 标注。拆块后需要补充 gold chunk／段落；需业务查询或兜底的问题不应计入常规知识召回率分母。

这组问题只用于初步验收，不是独立盲测数据，也没有对应实验成绩。正式评测应另设开发集与测试集，并保存逐题检索、回答、引用和耗时。

