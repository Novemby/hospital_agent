---
doc_id: "ACCESS-002"
title: "手语识别使用与识别错误处理"
category: "accessibility"
hospital_id: "hospital_sys_demo"
status: "demo_unreviewed"
version: "1.0"
updated_at: "2026-10-02"
source_type: "project_demo"
source_refs: ["frontend/js/scene1.js","frontend/js/handsign_assist.js","backend/routers/sign_language.py"]
---

# 手语识别使用与识别错误处理

> 适用范围：hospital_sys 演示知识；未经医院审核，不代表真实医院服务承诺。

## 如何开始使用
在项目提供的手语输入页面开启摄像头并授权。保持手部和上半身在可见区域，按页面提示开始识别；不同页面的启动提示可能不同，应以当前页面为准。

## 候选词与发送前核对
项目会显示识别结果或候选词，并提供确认或切换交互。发送前检查文字是否符合原意，尤其是症状、部位、否定词和日期。识别完成不等于系统已经提交挂号。

## 识别失败时怎么办
可改善光线、调整取景并重新比划；结果仍不正确时改用文字输入或向工作人员寻求协助。识别模型覆盖有限词汇，不承诺理解任意手语句子或所有方言手语。

## 摄像头无法使用
检查浏览器授权和页面提示；可以使用文字输入继续沟通。本文不承诺所有浏览器、网络或设备都能正常运行，也没有定义原始视频的保存期限。

