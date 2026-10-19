---
doc_id: "REG-002"
title: "号源查询与确认挂号"
category: "registration"
hospital_id: "hospital_sys_demo"
status: "demo_unreviewed"
version: "1.0"
updated_at: "2026-10-02"
source_type: "project_demo"
source_refs: ["backend/routers/agent.py","backend/routers/registrations.py"]
---

# 号源查询与确认挂号

> 适用范围：hospital_sys 演示知识；未经医院审核，不代表真实医院服务承诺。

## 查询号源
选择科室和日期后，系统向业务数据库查询号源。上午、下午是否有号，以接口返回为准。知识文档不能回答“明天还剩几个号”或承诺某位医生一定出诊。

## 确认步骤
挂号对话会展示匹配的科室、日期和时段，并提示回复“确认”。请核对信息后再确认。看到匹配信息不等于已挂号成功；提交成功后系统才返回挂号结果和排队号。

## 成功后的核对
保留系统返回的挂号记录和排队号。排队号不是精确看诊时间，实际进度需要查看业务页面或询问现场工作人员。

## 失败与不确定结果
网络错误或超时不代表一定没有提交。先核对是否已有挂号记录，再决定后续操作，避免反复点击造成重复提交。本文不承诺后端已经实现自动去重。

