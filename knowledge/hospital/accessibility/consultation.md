---
doc_id: "ACCESS-003"
title: "面诊表达润色与医生沟通"
category: "accessibility"
hospital_id: "hospital_sys_demo"
status: "demo_unreviewed"
version: "1.0"
updated_at: "2026-10-02"
source_type: "project_demo"
source_refs: ["backend/utils/langchain_agent.py","backend/routers/agent.py","frontend/js/handsign_assist.js"]
---

# 面诊表达润色与医生沟通

> 适用范围：hospital_sys 演示知识；未经医院审核，不代表真实医院服务承诺。

## 润色功能用途
面诊辅助页面可以把识别词汇或患者文字交给 AI，整理为较自然的表达，帮助医生理解。系统还可输出客观摘要以及医生侧的历史辅助信息。

## 原意优先
AI 润色不是新的患者事实。患者或工作人员应核对主要症状、部位、时间、否定表达和数量；发现错误时更正后再提供给医生。不能把模型补充的内容当作患者已经确认。

## 历史信息核对
既往记忆可能来源于患者自述、医生确认或模型推断，来源和确认状态不同。使用时应核对是否仍准确；未经确认的推断不能作为确诊结论。

## 医生说明与术语
可请求医生用文字说明。页面的术语解释用于辅助理解，不能代替医生对个人检查结果、诊断和治疗方案的说明。

