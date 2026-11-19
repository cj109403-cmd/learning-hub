# 课程格式规范 CONTENT_SPEC

版本：v0.8（2026-10-09）

## 0. 这份文件是什么

这是**课程内容和程序之间的合同**。

- Claude 按它写内容（卡片、任务数据、标准答案）。
- Codex 按它读内容、做页面、存记录。
- 任何一方想改格式，先改这份文件、版本号 +1、在文末「变更记录」写清改了什么，再动内容或代码。不允许"代码里先改了，文档回头补"。

---

## 1. 目录结构与归属

```
learning-hub/
├── AGENTS.md                  Codex 的工程规则（第 2 步写）
├── HANDOFF.md                 交接文件（Claude 维护）
├── WORKLOG.md                 Codex 的工作日志（Codex 追加）
├── docs/
│   ├── CONTENT_SPEC.md        本文件
│   └── STORYLINE.md           故事线设定
├── content/
│   ├── course.yaml            课程总表：有哪些线、每条线的卡片顺序
│   └── cards/
│       ├── python/<card-id>.md
│       ├── stats/<card-id>.md
│       └── analysis/<card-id>.md
├── data/
│   ├── tasks/<card-id>/       学生在任务里要用的文件
│   └── generators/            生成模拟数据的脚本 + 标准答案（可复现）
├── tools/
│   └── validate_content.py    内容校验脚本（Claude 写）
├── progress/
│   ├── progress.json          学习记录（只由程序写）
│   └── backups/               自动备份
├── app/                       程序（Codex 写）
├── workspace/                 用户自己的 Notebook 和代码（用户专属）
└── .agents/skills/learning-ui/ 设计规范 skill
```

| 位置 | 谁能写 | 谁只读 |
|---|---|---|
| `content/` `data/` `docs/` `tools/` | Claude（经用户确认） | Codex 只读，**一个字都不许改** |
| `app/` | Codex | — |
| `progress/` | 只由程序在运行时写 | 任何人都不手动改（需要迁移时先备份） |
| `HANDOFF.md` | Claude | Codex 只读 |
| `workspace/` | 只有用户自己 | Claude 和 Codex 都不读不写，程序也不显示、不扫描这个文件夹 |
| `WORKLOG.md` | Codex | Claude 只读 |

Codex 如果认为内容有错（比如字段缺失、链接失效），**在 WORKLOG.md 里记录问题，不要自己改内容**。

---

## 2. course.yaml（课程总表）

```yaml
spec_version: 1
content_version: "2026-10-02.2"   # 每次内容更新都改，格式 YYYY-MM-DD.N
lab:
  name: 气道炎症药理课题组
  fictional: true                  # 界面上标注"虚构"
people:                            # 故事人物；任务卡的 from、welcome 的 from 都引用这里的 id
  - { id: xu,     name: 徐老师,   role: 课题组负责人, initial: 徐 }
  - { id: beibei, name: 贝贝师姐, role: 博士四年级，负责数据分析, initial: 贝 }
  - { id: chen,   name: 陈师兄,   role: 博士二年级，做实验为主, initial: 陈 }
welcome:                           # 第一次打开时欢迎页上的话
  from: xu
  message: |
    欢迎加入课题组。……
tracks:
  - id: python              # 线的 id，和 cards/ 下的文件夹同名
    label: A                # 显示用的字母，卡片编号 = 字母 + 序号（A1、A2…）
    title: Python 基础
    summary: 能看懂、能修改、能用 Python 处理简单的生物数据
    cards:                  # 这条线的卡片顺序，就是数组顺序
      - py-first-code
      - py-variables
      - task-primer-check
      # …
  - id: stats
    label: B
    title: 统计学
    summary: …
    cards: [ … ]
  - id: analysis
    label: C
    title: 数据处理
    summary: …
    cards: [ … ]
```

规则：

1. **卡片 id 永久不变。** 一旦发布，不改名、不删除。不再使用的卡在卡片里写 `retired: true`，程序把它从地图和推荐里隐藏，但保留学习记录。
2. 卡片的显示编号（A1、A2…）由它在数组里的位置算出来，不写死在卡片里。所以中间插入新卡，编号会变，但 id 和学习记录不受影响。
3. 每张卡必须且只能出现在一条线里一次，文件必须放在 `cards/<线的 id>/` 下。
4. course.yaml 只列**已经写好**的卡。计划中但还没写的卡记在 HANDOFF.md 里，不进 course.yaml。
5. 每次内容更新，`content_version` 必须变化（同一天第二次更新就把 `.N` 加 1）。程序据此判断"有新内容"。

---

## 3. 卡片文件

每张卡是一个 Markdown 文件：开头是 YAML 头信息（结构化字段），后面是正文（长段文字）。

卡片分两种：

- **lesson（学习卡）**：讲解 + 资源 + 怎么学 + 自测题。
- **task（导师任务卡）**：导师派的实际任务，要动手处理数据，答案由程序自动判定。

### 3.1 所有卡片都有的字段

```yaml
id: stats-pvalue          # 必须和文件名一致；只用小写字母、数字、连字符
type: lesson              # lesson | task
title: p 值到底是什么
subtitle: 搞懂 p 值的真正含义，以及最常见的误读   # 一句话，显示在推荐卡和标题下
takeaway: "p 值是……"      # 参考要点（v0.8），见下方说明
minutes: 30               # 预计用时（分钟）
requires: []              # 额外的前置卡（通常用于跨线依赖），见第 5 节
verified: 2026-10-02      # 最近一次核实内容和链接的日期
resources: [ … ]          # 见 3.3，可为空
```

**takeaway（参考要点，v0.8 新增）**：这张卡最值得记住的一两句话，10–120 个字。只在"结"阶段显示，学习者写不出总结时可以参考，也可以一键用作自己的总结（之后可以修改）。写法要求：

- 用平实的话说出**这一节最核心的道理或做法**，而不是复述标题。
- 任务卡的要点写"从这个任务里学到的方法或教训"，**不写任何具体答案数值**。
- 每张卡都应该有；缺少时校验脚本给出警告。

### 3.2 正文结构

正文用固定的二级标题（`##`），**名字和顺序都不能变**，程序按标题切分内容。

lesson：

```markdown
## 讲解
（必有。Claude 写的中文讲解，见第 8 节写作标准）

## 怎么学
（必有。具体步骤：先看什么、再做什么、注意什么）

## 延伸
（可选。学有余力时看的内容）
```

task：

```markdown
## 任务说明
（必有。要做什么、交付什么、用哪些文件）

## 提示
### 提示 1
（逐级提示，越往后越具体。程序一次只展开一条）
### 提示 2
…

## 参考解法
（必有。完整代码 + 解释。默认隐藏，见第 4 节解锁规则）
```

### 3.3 资源 resources

```yaml
resources:
  - kind: video             # video | course | practice | reading
    title: "p-values: What they are and how to interpret them"
    source: StatQuest       # 提供方
    url: https://www.youtube.com/watch?v=XXXXXXXXXXX
    embed: youtube          # youtube | bilibili | none
    lang: en                # en | zh
    minutes: 11
    required: true          # true = 必看/必做；false = 选看
    focus: 重点看"p 值是在假设什么都没发生的前提下算出来的"这一点
```

- `kind`：video 视频；course 交互课程（如 Kaggle 某一节）；practice 练习题（如 Rosalind）；reading 文章或文档。
- `embed`：视频能否在页面里直接播放。`youtube` 和 `bilibili` 可以内嵌；`none` 只给跳转链接。不管能不能内嵌，**程序都必须同时给"在新窗口打开"的链接**，因为 YouTube 需要代理，内嵌加载失败时要能跳过去看。
- `focus`：一句话说明看这个资源时要特别注意什么，避免"看完了但不知道重点"。

### 3.4 自测题 questions（lesson 必有，3–5 题）

```yaml
questions:
  - id: q1
    kind: concept           # concept 概念 | predict 预测代码输出 | apply 联系实际
    prompt: |
      p = 0.03 是不是意味着"原假设成立的概率是 3%"？为什么？
    answer: |
      不是。p 值是**假设原假设成立**时，观察到这么极端（或更极端）结果的概率……
```

- `prompt` 和 `answer` 都支持 Markdown（包括代码块）。
- 每张学习卡至少有一道 `predict` 或 `apply` 题，不能全是背概念。
- `answer` 要讲清楚**为什么**，不能只给结论。

### 3.5 任务卡专有字段

```yaml
type: task
from: xu                 # 发任务的人，必须是 course.yaml 里 people 的 id
message: |                 # 以消息形式出现在"课题组"页，口语化，150 字左右
  你好，欢迎加入……
simulated: true            # true = 模拟数据；false = 真实公开数据（必须在 source 里写来源）
source:                    # simulated: false 时必填
  citation: Himes BE, et al. (2014) PLoS ONE 9(6): e99625
  url: https://…
  license: …
capstone: false            # true = 这条线的毕业任务
debrief: |                 # 可选；完成任务后，导师的一句回复（出现在课题组对话里）
  收到，结论很清楚……
data:                      # 可选；路径相对 data/tasks/<card-id>/
  - file: primers.txt
    description: 3 条候选引物序列
checks: [ … ]              # 见 3.6，至少 1 个
writeup:                   # 可选；需要写一段文字结论的任务才有
  prompt: 给徐老师写一段约 200 字的结论
  rubric:                  # 自查要点，每条都要自己勾选
    - 说明了 AW-17 是否降低了 IL-6，以及降低了多少
    - 给出了支持结论的统计证据
    - 指出了至少一个局限
```

### 3.6 答案判定 checks

（下面的数字和序列只是演示格式用的，不是真实任务的答案。）

```yaml
checks:
  - id: c1
    prompt: 有几条引物的 GC 含量在 40%–60% 之间（含边界）？
    kind: integer
    expected: 7

  - id: c2
    prompt: 引物 P05 的 GC 含量是多少？（百分数，保留一位小数，不用写 %）
    kind: number
    expected: 52.4
    tolerance: 0.05        # number 类型必填：允许的绝对误差

  - id: c3
    prompt: 写出 P05 的反向互补序列
    kind: text
    expected: CGTTAGGCATCA
    normalize: [trim, upper, no_space]

  - id: c4
    prompt: 根据结果，你会建议徐老师怎么做？
    kind: choice
    options:
      - { id: a, text: 全部引物直接下单 }
      - { id: b, text: 只下单 GC 含量合格的引物 }
      - { id: c, text: 全部重新设计 }
    expected: b

  - id: c5
    prompt: 哪些引物不合格？（写编号，用逗号或空格隔开，顺序不限）
    kind: set
    expected: [P02, P07, P11]
    normalize: [trim, upper]
```

判定规则（程序必须严格按这个实现）：

| kind | 怎么判 |
|---|---|
| `integer` | 去掉首尾空格后解析为整数，与 `expected` 完全相等 |
| `number` | 解析为小数，`|输入 − expected| ≤ tolerance` 即正确；`tolerance` 必填 |
| `text` | 按 `normalize` 处理后与 `expected` 完全相等 |
| `choice` | 选中项的 id 等于 `expected` |
| `set` | 按分隔符拆成若干项，每项按 `normalize` 处理，作为集合与 `expected` 比较（不看顺序，重复项只算一次） |

- `normalize` 可选值：`trim` 去首尾空格；`upper` 转大写；`lower` 转小写；`no_space` 去掉所有空白字符。
- `set` 的分隔符：英文逗号、中文逗号、英文分号、中文分号、空格、换行，任意混用都行。
- 数字输入要接受全角数字和全角小数点（程序先转半角）；`number` 和 `integer` 允许末尾带一个 `%`，判定前去掉。
- 答错时只显示"不对，再检查一下"，**不显示正确答案**。

**标准答案的来源：** 所有 `expected` 必须由 `data/generators/` 里的脚本计算得出，并写入 `data/generators/<card-id>.answers.json`。校验脚本会核对卡片里的 `expected` 和这份文件是否一致。不允许手算后直接填。

---

## 4. 完成与解锁规则

### 4.1 学习卡怎样算完成

1. 每道自测题：可以先在输入框里写自己的答案（可选，但鼓励写），点"对照答案"后展开参考答案，然后自己标记"答对了"或"没答对"。
2. 所有题都标记为"答对了"。
3. 一句话总结"这节我学到了什么"：**可选**（v0.8 起）。只有用户在设置里打开了"完成卡片时要求写总结"（`settings.json` 的 `require_summary`，默认 `false`）时，才是必填。
4. 满足以上必需的条件后，"完成"按钮才可用。

标记"没答对"的题可以之后重新标记。卡片保持"进行中"。

### 4.2 任务卡怎样算完成

1. 所有 `checks` 都判定正确。
2. 如果有 `writeup`：文字结论不能为空，且每条 `rubric` 都已自己勾选。
3. 一句话总结：同 4.1 第 3 条，默认可选。**文字结论（writeup）不受这个设置影响，仍然必填**，因为它是任务本身的一部分。

参考解法的解锁：**任务完成后**，或者**任意一个 check 已经答错 3 次后**，才能展开。展开过参考解法会记录下来（`solution_viewed: true`），但不影响完成。

### 4.3 已完成的卡

可以随时重新打开阅读，状态保持"已完成"，不会因为重看而重置。

---

## 5. 前置关系与推荐

### 5.1 前置关系

一张卡"可学"的条件：它的所有前置卡都已完成。

- **隐含前置：** 同一条线里，每张卡默认以**前一张卡**为前置（第一张没有）。
- **额外前置：** `requires` 里列出的卡（通常是别的线的卡）。
- 校验脚本保证前置关系里没有循环、没有指向不存在的卡。

**退休的卡（`retired: true`）怎么处理前置关系：**

- 隐含前置**跳过**退休的卡：一张卡的隐含前置，是同一条线里排在它前面、**最近的一张没有退休的卡**。
- `requires` 里如果写了一张退休的卡，这一条**视为已满足**，直接忽略。
- 退休的卡不显示在路线和推荐里，也不能学；但它的学习记录永远保留，已完成的仍然出现在"记录"页的历史里。
- 线的进度（"完成 X / 共 Y"）只统计**没有退休**的卡，分子分母都不含退休的卡。

这样，以后退休任何一张卡（不管完成没完成），都不会把后面的卡卡住。

### 5.2 推荐算法（首页显示 2–3 张）

程序必须按以下规则实现，结果是确定的、可以解释的：

1. **继续上次：** 如果有"进行中"的卡，取最近一次有操作的那张，放第一位。最多 1 张。
2. **每条线的下一张：** 对每条线，找出按顺序第一张"可学且未完成"的卡（跳过已经作为"继续上次"的那张）。
3. **排序：** 这些候选按"这条线上次有完成记录距今的天数"从大到小排（从没学过的线排最前）；天数相同按线在 course.yaml 里的顺序。
4. 取前几张，加上"继续上次"总共最多 3 张。
5. 每张推荐卡显示一句推荐理由：

| 情况 | 理由文案 |
|---|---|
| 继续上次 | 上次学到一半 |
| 上次打开程序之后才解锁的（解锁时间 = 最后一张前置卡的完成时间，晚于上一次打开程序的时间） | 完成「<前置卡标题>」后解锁 |
| 这条线还没开始 | 还没开始这条线 |
| 这条线 7 天以上没学 | 这条线上次学习是 N 天前 |
| 其他 | 这条线的下一节 |

**刻意不做的：** 每日目标、连续打卡天数、"你已经 N 天没学了"之类制造压力的提醒。

---

## 6. 学习记录 progress.json

**两个"版本号"不是一回事：** `course.yaml` 的 `spec_version` 是**课程内容格式**的版本（目前 1）；`progress.json` 的 `spec_version` 是**学习记录格式**的版本（目前 **2**）。还有 `content_version` 表示课程内容本身有没有更新，`revision` 表示这次保存前记录有没有被别处改过。四者互不相关。

```json
{
  "spec_version": 2,
  "welcome_seen_at": "2026-10-03T19:30:00+08:00",
  "last_visit_at": "2026-10-03T20:15:00+08:00",
  "known_cards": ["py-variables", "py-functions"],
  "seen_content_version": "2026-10-03.3",
  "pending_new_cards": [],
  "cards": {
    "stats-pvalue": {
      "status": "in_progress",
      "started_at": "2026-10-03T19:40:00+08:00",
      "updated_at": "2026-10-03T20:10:00+08:00",
      "done_at": null,
      "questions": {
        "q1": { "my_answer": "……", "self_mark": "correct", "at": "…" }
      },
      "checks": {
        "c1": { "attempts": [ { "value": "7", "correct": true, "at": "…" } ] }
      },
      "solution_viewed": false,
      "resources_done": [0],
      "ui": { "phase": "practice", "anchor": "q2", "learn_completed": true },
      "writeup": { "text": "", "rubric_checked": [] },
      "summary": "",
      "content_verified_at_completion": null
    }
  },
  "events": [
    { "at": "…", "type": "card_started", "card": "stats-pvalue" }
  ]
}
```

- `status`：没有记录 = 未开始；`in_progress`；`done`。
- `events.type`：`card_started` `question_marked` `check_attempt` `solution_viewed` `writeup_saved` `summary_saved` `card_done`。
- `writeup.rubric_checked`：已勾选的自查要点序号（从 0 开始）。没有 `writeup` 的任务不写这个字段。
- `resources_done`：用户标记为"已完成"的资源序号（从 0 开始）。只是给自己看的进度，**不影响**完成条件。
- `ui.phase`：上次停在哪个阶段，`learn` / `practice` / `wrap`；`ui.anchor`：上次停留位置的锚点（小节或题目 id）。用于重新打开时回到原处。
- `ui.learn_completed`（v2 新增，默认 `false`）：用户点过"学"阶段末尾的"下一步"，或滚动到过"学"阶段底部，就设为 `true`，**之后不再变回 `false`**。用于阶段指示条上"学"的小对勾和首页主卡的三阶段进度。不影响卡片的完成条件。
- `welcome_seen_at`：看过欢迎页的时间；没有这个字段就显示欢迎页。
- `known_cards`：上一次打开程序时 course.yaml 里存在的全部卡片 id。
- `seen_content_version`：上一次打开程序时的 `content_version`。
- `pending_new_cards`（v2 新增，默认 `[]`）：**新加入、还没被用户看过**的卡片 id。规则：
  1. 每次 `visit` 时：`新增 = 当前全部卡片 id − known_cards`；`pending_new_cards = (原 pending_new_cards ∪ 新增)`，再去掉已经不存在或已 `retired` 的卡；然后才把 `known_cards` 更新为当前全部卡片 id。顺序不能反，否则会丢掉"新"标签。
  2. 用户打开某张卡的页面（包括锁定的卡，查看前置要求也算），就把它从 `pending_new_cards` 里去掉。
  3. 第一次使用程序时（还没有 `known_cards`），不把任何卡算作"新"：直接把 `known_cards` 设为当前全部卡片，`pending_new_cards` 为空。
- 外观偏好（主题、深浅色、正文字体、字号、动效）存在单独的 `progress/settings.json`，不放进 progress.json。`settings.json` 也可以存**纯界面状态的标记**（例如 `card_tips_seen`：第一次进卡片页的提示是否看过）。另有一个学习偏好 `require_summary`（v0.8，默认 `false`）：为 `true` 时，完成卡片必须写一句话总结。规则：只能是"丢了也不影响学习"的东西，不能存任何学习内容或进度。
- **防丢数据：** 写入时先写临时文件再整体替换；每次启动时自动备份一份到 `progress/backups/`，保留最近 20 份。
- 输入框里的内容（自测答案、一句话总结、文字结论）要自动保存，关掉页面也不丢。
- 时间一律带时区（北京时间 +08:00，以后去加拿大会自动变）。

### 6.1 版本迁移

- **v1 → v2**：补上 `pending_new_cards: []`，每张卡的 `ui` 补上 `learn_completed`（如果这张卡已经是 `done`，或已经有任何自测 / check 记录，设为 `true`；否则 `false`）；`spec_version` 改为 2。其他字段原样保留，包括程序不认识的字段。
- 程序遇到比自己认识的更高的 `spec_version`：拒绝写入，提示用户，不降级。
- 以后每次改学习记录的格式，都要在这里写清迁移规则，并升 `spec_version`。

导出给 AI 的"学习记录摘要"和"卡住了"的复制格式，在第 2 步写给 Codex 的指令里定义。

---

## 7. 视频与外部链接

- 视频默认**不自动加载**：先显示一个占位块（标题、来源、时长），点击后才加载播放器。这样页面打开更快，没开代理时也不会卡住。
- YouTube 内嵌使用 `youtube-nocookie.com` 域名。
- 所有外部链接在新窗口打开。

---

## 8. 内容写作标准（Claude 写内容时遵守）

**讲解：**
- 中文为主，术语第一次出现时附英文，如"p 值（p-value）"。
- 顺序：这是什么 → 为什么重要 → 在生物或制药研究里用在哪 → 一个具体小例子 → 常见误区。
- 一般 800–1500 字。超过就拆成两张卡。
- 学习卡总用时（读讲解 + 看资源 + 做练习）一般 30–60 分钟。
- 讲解里出现的代码必须实际运行过（注明 Python 版本），输出要和文中写的一致。

**准确性：**
- 每个事实性说法都要核实，拿不准的不写。
- 每个资源链接写之前都要实际打开核实，并更新 `verified` 日期。
- 模拟数据必须标 `simulated: true`，页面会显示"本任务使用模拟数据"，避免把虚构的数字当成真实科学结论。

**任务：**
- 一个任务不超过 60 分钟，超过就拆。
- 标准答案只能来自生成脚本的输出。
- 提示由浅到深，最后一条提示接近答案但不直接给出。

**语气：**
- 像一个耐心的师兄师姐在讲，平实、具体。
- 不用夸张的感叹号、不说"超简单""轻松掌握"。

---

## 9. 校验

`tools/validate_content.py` 会检查：

- id 唯一、和文件名一致、格式正确
- 必填字段齐全、类型正确
- 正文的二级标题齐全且顺序正确
- `requires` 指向存在的卡、整体没有循环
- `data` 里列的文件都存在
- `checks` 的 kind 合法，`number` 都有 `tolerance`，`expected` 与 generators 的答案文件一致
- 学习卡有 3–5 道题，且至少一道 predict 或 apply
- 链接格式正确、`verified` 日期存在

**程序遇到不合格的内容时：** 在页面顶部显示一条提示，列出哪张卡有什么问题，并跳过这张卡，其余正常运行。不要尝试自动修复内容。

---

## 变更记录

- v0.1（2026-10-02）：初稿。
- v0.8（2026-10-09）：用户实际使用后的调整。每张卡新增 `takeaway`（参考要点）；一句话总结改为默认可选，由 `settings.json` 的 `require_summary` 控制（默认 `false`）；任务的文字结论仍然必填。学习记录格式不变。
- v0.7（2026-10-03）：允许 `settings.json` 存纯界面状态的标记（如 `card_tips_seen`），明确不能存学习内容或进度。学习记录格式不变。
- v0.6（2026-10-03）：补充退休卡对前置关系和进度统计的影响（应 Codex 阶段二 QA 的提问）。学习记录格式不变，仍是 spec_version 2。
- v0.5（2026-10-03）：学习记录格式升为 `spec_version: 2`：新增 `ui.learn_completed`、`pending_new_cards`（应 Codex 阶段二计划的要求补齐），写明 `welcome_seen_at`、`known_cards`、`seen_content_version` 的含义和 v1 → v2 迁移规则；说明四种版本号的区别。
- v0.4（2026-10-02）：course.yaml 新增 `content_version`、`lab`、`people`、`welcome`；任务卡的 `from` 改为引用 course.yaml 的 people；任务卡新增可选 `debrief`；学习记录新增 `resources_done`、`ui`、`known_cards`、`seen_content_version`；外观偏好单独存 `progress/settings.json`。
- v0.3（2026-10-02）：新增用户专属的 `workspace/` 文件夹（放自己写的 Notebook 和代码），任何工具都不碰它。
- v0.2（2026-10-02）：C 线 id 改为 `analysis`（避免和顶层 `data/` 文件夹混淆）；course.yaml 只列已写好的卡；任务卡 `data` 改为可选，新增可选的 `writeup`（文字结论 + 自查要点）；数字答案允许带 `%`；明确"刚解锁"的判定时间；学习卡用时一般 30–60 分钟。
