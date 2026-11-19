---
id: demo-average
type: task
title: 算一组测量的平均值
subtitle: 示例导师的小任务：五次吸光度测量
takeaway: "平均值等于总和除以个数；先数清楚有几个数，再动手算，最后看结果是否落在数据范围之内。"
minutes: 20
requires: []
verified: 2026-11-19
resources: []
from: mentor
message: |
  我把同一份溶液测了五次吸光度，数据在 absorbance.csv 里。

  能帮我算一下平均值吗？顺便看看哪一次最高、一共测了几次。（数据是模拟的。）
simulated: true
capstone: false
data:
  - file: absorbance.csv
    description: 五次吸光度测量，一行一个数
checks:
  - id: c1
    prompt: 五次测量的平均值是多少？（保留两位小数）
    kind: number
    expected: 0.52
    tolerance: 0.005
  - id: c2
    prompt: 哪一次测量的数值最高？
    kind: choice
    options:
      - { id: a, text: 第 1 次 }
      - { id: b, text: 第 2 次 }
      - { id: c, text: 第 3 次 }
      - { id: d, text: 第 4 次 }
      - { id: e, text: 第 5 次 }
    expected: c
  - id: c3
    prompt: 一共有几次测量？
    kind: integer
    expected: 5
debrief: |
  算得没错，平均 0.52，第 3 次偏高一点，但还在正常范围里。下次可以试试用 Python 读 CSV 文件来算。
---

## 任务说明

**数据：** `absorbance.csv` 里有五次测量的吸光度：

```
0.48
0.51
0.57
0.50
0.54
```

**要做的事：**

1. 数一数一共有几个数。
2. 算出它们的平均值（总和除以个数），保留两位小数。
3. 找出数值最高的是第几次。
4. 把答案填到下面。

可以用计算器，也可以用 Python：

```python
values = [0.48, 0.51, 0.57, 0.50, 0.54]
```

## 提示

### 提示 1

总和可以用 `sum(values)`，个数可以用 `len(values)`。

### 提示 2

平均值就是 `sum(values) / len(values)`；最高值是 `max(values)`，再看它排在第几个。

## 参考解法

```python
values = [0.48, 0.51, 0.57, 0.50, 0.54]
print(len(values))                          # 5
print(round(sum(values) / len(values), 2))  # 0.52
print(values.index(max(values)) + 1)        # 3，即第 3 次
```

总和是 2.60，除以 5 得 0.52。最高的 0.57 是第 3 次测量。
