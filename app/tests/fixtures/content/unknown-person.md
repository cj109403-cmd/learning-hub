---
id: task-one
type: task
title: 测试任务 task-one
subtitle: 用于隔离测试
minutes: 35
requires: []
verified: '2026-10-03'
resources: []
from: not-a-person
message: '第一段导师消息。


  第二段导师消息。'
simulated: true
capstone: false
debrief: 这是一条完成后的回复。
data:
- file: sample.txt
  description: 测试数据
checks:
- id: c1
  kind: text
  prompt: 填写测试结果
  expected: EXPECTED_NEVER_SEND_f4d7
writeup:
  prompt: 写一段结论
  rubric:
  - 说明结果
  - 指出局限
---
## 任务说明
测试任务
## 提示
### 提示 1
测试提示
## 参考解法
LOCKED_SOLUTION_NEVER_SEND_9a26
