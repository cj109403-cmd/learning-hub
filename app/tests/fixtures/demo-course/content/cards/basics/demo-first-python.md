---
id: demo-first-python
type: lesson
title: 第一行 Python
subtitle: 用 print 让电脑把结果显示出来
takeaway: "print() 会把括号里的内容显示出来；文字要放在引号里，没加引号的算式会先算出结果。"
minutes: 20
requires: []
verified: 2026-11-19
resources:
  - kind: reading
    title: Python 官方教程：把 Python 当作计算器
    source: Python 官方文档
    url: https://docs.python.org/zh-cn/3/tutorial/introduction.html
    embed: none
    lang: zh
    minutes: 20
    required: true
    focus: 读“数字”和“文本”两小节，边读边在自己的电脑上试几行。
questions:
  - id: q1
    kind: predict
    prompt: |
      下面这行代码会显示什么？

      ```python
      print(2 + 3)
      ```
    answer: |
      显示 `5`。没有引号时，`2 + 3` 是一个算式，Python 先算出结果，再把结果显示出来。
  - id: q2
    kind: predict
    prompt: |
      下面这行代码会显示什么？

      ```python
      print("2 + 3")
      ```
    answer: |
      显示 `2 + 3`。放在引号里的是**文字**（字符串），Python 原样显示，不会去计算。
  - id: q3
    kind: concept
    prompt: |
      为什么 `print(你好)` 会报错，而 `print("你好")` 不会？
    answer: |
      没有引号时，Python 会把 `你好` 当成一个变量名去找它的值；这个变量从来没有定义过，所以报 `NameError`。加上引号，`"你好"` 就是一段文字，可以直接显示。
---

## 讲解

`print()` 是你最先会用到的函数：括号里放什么，它就显示什么。

```python
print("你好，学习台")
print(2 + 3)
```

运行后会显示两行：

```
你好，学习台
5
```

注意两点：

- **文字要放在引号里。** `"你好，学习台"` 是一段文字（字符串），单引号、双引号都可以。
- **没加引号的算式会先算。** `2 + 3` 先算出 `5`，再显示出来。

## 怎么学

1. 读完讲解，把上面两行代码抄到自己的 Python 里运行一遍。
2. 改一改：换成你的名字，或者算 `7 * 6`，看看结果。
3. 做“练”里的三道题，先猜结果再看答案。
