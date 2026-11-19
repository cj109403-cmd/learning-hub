# 学习台结构说明

这份说明面向接手复查的 Claude Code 和后续维护者。路径均相对项目根目录；本文介绍当前实现与复查入口，实际验收结果另见 `app/QA.md` 和 `WORKLOG.md`。先按 `AGENTS.md` 阅读规定文件；Claude Code 还需先读 `docs/CLAUDE_CODE_REVIEW.md`。

## 1. 先建立整体认识

应用由三层组成：系统外壳负责开关窗口和后台进程；同一份网页负责展示；同一份 Python 服务负责课程规则和持久保存。Mac 和 Windows 不维护两套学习逻辑。

```text
Mac：学习台.app ── Swift / AppKit / WKWebView ─┐
                                               ├─ HTTP 127.0.0.1 ─ app/server/main.py
Windows：快捷方式 ─ pythonw ─ Edge 应用窗口 ───┘                      │
                         网页 app/static/                            ├─ 只读 content/、data/
                                                                    ├─ 运行时写 progress/
                                                                    └─ 更新子进程：七个内容根 + backups/
```

主要入口：

| 入口 | 作用 |
|---|---|
| `app/macos/main.swift` → `AppDelegate.swift` | Mac 原生应用入口 |
| `app/windows/install.ps1` | 一次性为当前用户创建快捷方式 |
| `app/windows/launch.pyw` → `app/desktop/launch.py` | Windows 隐藏启动，连接服务并打开 Edge 应用窗口 |
| `app/server/main.py` 的 `main()` | 服务进程入口：单实例、端口、启动登记、HTTP、退出 |
| `app/static/index.html` → `app.js` | 网页启动、页面导航、编辑、设置和退出冲刷 |
| `app/updater/update_content.py` | 备用交互更新入口，以及应用内更新 worker |
| `启动学习台.command`、`更新内容.command` | 保留的 Mac 备用入口，脚本文字为 ASCII |

后端只用 Python 3.9+ 标准库和项目内固定版本的纯 Python PyYAML。网页是 ES modules，没有框架或构建步骤。Markdown、清洗和代码高亮库在 `app/vendor/`；运行时不从 CDN 下载。只有用户主动打开外链或视频时才访问外网。

## 2. 模块职责与修改入口

| 文件或目录 | 职责；应在这里修改什么 |
|---|---|
| `app/server/main.py` | `LearningHub` 组合各模块，`Handler` 做 HTTP 边界检查，组装路由；不要在这里重复实现判题或存储规则 |
| `app/server/content.py` | 每次学习请求读取课程和卡片、检查结构、拆 Markdown、计算有效前置；错误卡片跳过并报告 |
| `app/server/judge.py` | integer / number / text / choice / set 五类判定；数值容差与规范化 |
| `app/server/rules.py` | 解锁、推荐、完成条件、`perform()` 学习操作；所有正式学习修改由后端判断 |
| `app/server/views.py` | 将内部内容整理为页面数据；显式挑选可公开字段，不把原卡片直接序列化 |
| `app/server/export.py` | AI 复制文本、学习记录摘要及运行时导出 |
| `app/server/progress.py`、`migrations.py` | 正式记录和设置、并发修订检查、原子写入、备份恢复、格式迁移 |
| `app/server/drafts.py` | 独立磁盘草稿仓库；序号、确认和显式丢弃，不负责判题或写正式记录 |
| `app/server/lifecycle.py` | 内存中的活动计数、更新门控和心跳；本模块没有文件访问 |
| `app/server/updates.py` | 应用内更新检查、候选确认、启动独立 worker、公开进度和安全错误信息 |
| `app/updater/` | ZIP 检查、内置课程检查（`content_check.py`，不执行包内代码）、按根整体替换、持久事务与回滚；与学习仓库独立 |
| `app/platform_support.py` | 唯一共享系统差异边界：锁、替换重试、目录同步、下载目录、隐藏子进程和文件定位 |
| `app/file_safety.py` | 路径拼写、白名单、普通文件、符号链接和 Windows reparse point 检查 |
| `app/static/app.js`、`pages/` | 页面状态和交互编排、真实内容展示；临时编辑状态不等于正式记录 |
| `app/static/api.js` | HTTP、保存队列、断线重试、409 字段冲突、桌面控制令牌 |
| `app/static/html.js` | 多行 HTML 模板的标签：去掉标签之间的排版换行，输出与一行模板逐字相同 |
| `app/static/drafts.js` | 输入即时备份到内存/localStorage 和磁盘草稿；比较版本后才能清理 |
| `app/static/desktop.js`、`platform.js` | 心跳、原生外观桥、Mac/Windows 快捷键和平台文案 |
| `app/static/updates.js`、`settings.js` | 设置里的课程更新流程、外观设置界面 |
| `app/macos/`、`app/windows/`、`app/desktop/` | 各平台启动与窗口外壳；不增加独立的学习规则 |
| `app/distribution/build_share.py` | 根据明确允许清单生成本地分享 ZIP |
| `app/distribution/export_open_source.py` | 导出不含历史与个人数据的开源副本（示例课程、README、LICENSE、CI 模板在 `open_source/`），导出前做隐私扫描 |
| `app/course_extras.json` | 这门课特有的显示设置（目前只有任务提示的默认署名 `hints_from`）；可选，开源导出不含 |

人物从 `content/course.yaml` 的 `people` 读取，不能写死人名或人物 id；只属于某一门课的显示设置放 `app/course_extras.json`。视觉和动效以 `learning-ui` skill 为准；`app/static/tokens.css` 是 tokens v2.2 的副本。

退休卡按 CONTENT_SPEC v0.6 处理：隐含前置跳到最近的未退休卡，显式退休前置忽略；不能继续学习，不进路线和推荐；已完成历史仍进记录页和摘要。进度只计活跃卡，显示编号仍由原课程数组位置决定。

## 3. 一次学习操作怎样流动

1. `app.js` 获取 `/api/bootstrap`，取得公开课程、正式记录、设置、revision 和控制令牌。随后执行 `visit`；`known_cards` 和 `pending_new_cards` 的合并顺序由 `rules.perform()` 实现。
2. 用户编辑文本时，`drafts.js` 立即保存草稿，并安排停顿 500ms 后的正式保存。任务答案只暂存，点击“检查答案”才调用判题。
3. `api.js` 将正式学习修改串行送到 `POST /api/op`。答案、总结、文字结论另带 `base`（这次编辑开始时框里的文字）。设置由自己的前端队列写 `PUT /api/settings`。
4. `LearningHub.api()` 先进入生命周期活动门，再由学习路由持有请求锁和共享内容锁，读取这一刻的课程与记录。
5. `ProgressStore.mutate()` 在进程间记录锁内重读磁盘、复制数据，再调用 `rules.perform()`；需要时先检查 revision 或字段基线（见下节）。判题转到 `judge.py`。
6. 完整新记录校验并原子写入成功后，服务返回新快照和 revision。`views.py` 负责裁剪公开数据。
7. 前端确认这一个版本已保存，才请求清理相同版本的草稿。迟到的旧响应不能删除新输入，也不能覆盖输入框里的新内容。

自测题的参考答案按内容合同公开；任务 `checks.expected`、容差和内部判定参数不出现在公开卡片中。任务参考解法同时满足“允许查看”和“用户已明确查看”才发送。更新校验错误也必须经过 `updates.public_error()` 分类，不能把可能含标准答案或 YAML 原文的诊断直接塞进 API。

## 4. 正式记录、设置和草稿是不同的数据

| 位置 | 含义与管理者 |
|---|---|
| `progress/progress.json` | 正式学习记录，格式 `spec_version: 2`；只能由运行中的服务写 |
| `progress/settings.json` | 外观设置，独立于学习记录；默认清透、跟随系统、黑体、标准字号 |
| `progress/backups/` | 正式记录自动备份，保留最近 20 份 |
| `progress/progress.corrupt-*.json` | 损坏原件，永久保留，不参与自动轮换 |
| `progress/exports/` | 用户主动导出的学习记录 Markdown |
| `progress/drafts/` | 尚未正式确认的输入；独立草稿信封格式 `version: 1` |
| `logs/`、`logs/runtime/` | 程序日志、实例锁和启动登记；不存答案或草稿正文 |
| `backups/content-*/` | 内容更新备份，与学习记录备份完全不同 |

### 正式记录写入与冲突

`progress.py` 的 `_atomic_write()` 在目标同目录创建唯一临时文件，完整写入、flush、文件 fsync，再整体替换目标，最后同步目录。任一步失败都不能回应“已保存”。替换后目录同步失败时，文件可能已完整更新；程序仍报告未确认成功，重新读取实际字节，不能武断地重试覆盖。

revision 是正式文件原始字节的 SHA-256，不向 JSON 增加修订计数字段。线程锁加进程间文件锁覆盖“重读 → 比较 → 修改 → 写入”。

冲突只按字段判断（2026-11，`rules.needs_revision()`）：

- **程序状态**（visit、看过、开始、阅读位置、自测标记、任务尝试、看参考解法、完成）不可能覆盖别人写的字，直接写进最新记录，不查 revision。
- **勾选**（资源、评分要点）带想要的状态 `done: true/false`，重发不会来回翻转。
- **输入的文字**（答案、总结、文字结论）带 `base`。只有存储的文字既不等于 `base`、也不等于这次要写的文字时，才算“这个字段在别处被改过”，返回 409；前端保留草稿，由用户核对。同样的保存重发不算冲突。不带 `base` 的旧式请求仍按整份 revision 检查。
- 前端的 `base` 来自文本框渲染时的值（`defaultValue`），之后是本窗口上次存下的文字；同一字段的保存依次进行，所以连续快速保存不会误报。恢复草稿和重新连接后的补存明确以当前记录为基准。

### 备份、恢复和迁移

- 每次正常启动先原样备份已有有效记录；首次空记录也建立备份。新备份确认写入之后才轮换，保留最近 20 份。
- JSON 损坏时，将原件改名为 `progress.corrupt-时间.json`，从新到旧寻找有效备份。恢复前保留备份，页面提示恢复来源和可能缺失较新的操作。
- 无可用备份时暂停写入；不会建立空文件盖过损坏记录。更高版本也进入只读保护，不当成损坏文件降级。
- v1 → v2 在 `migrations.py`：补 `pending_new_cards: []`；每张卡补 `ui.learn_completed`，已完成或已有自测/check 记录时为 true，否则 false；版本升为 2，未知字段、旧卡和尝试历史原样保留。
- 迁移先备份，内存中逐步变换和校验，最后一次写入。将来改格式必须先由 Claude 更新合同，再新增迁移函数及测试；不能把内容版本号当记录版本。

### 未确认草稿永不自动删除

`DraftStore` 按“客户端 + 卡片 + 字段”的 SHA-256 命名文件。字段包括自测回答、总结、文字结论、未检查的任务答案和设置。**阅读位置（`position`）自 2026-10-14 起不再是草稿**（CODEX_BRIEF 18.8）：服务端收到旧版发来的位置草稿直接视为已处理，启动整理时清掉已有的位置草稿；页面按卡片各自记录待写的位置，最多每 2 秒写一次，离开或退出时补写所有卡，`set_ui` 安静重试，失败就放弃、下次打开重新记录。`baseline` 保存编辑基线用于辨别上下文，不是自动覆盖授权。

每个客户端字段有递增 `sequence`。同序号同文字可幂等重试，旧序号或同序号不同内容拒绝；不同客户端互不覆盖。文件中有独立的 `version: 1`，不能与 `progress.json` 的 `spec_version: 2` 混用。

两种方式可以清除草稿正文：

- `POST /api/drafts/confirm`：服务器自己读取正式记录或设置，核对该字段确已保存为相同内容、草稿序号也相同，才确认。不能相信前端传来的“已保存”标志。
- 用户在草稿面板逐条点击“丢弃”，再确认：调用 `/api/drafts/discard`，只处理指定 id 和序号。

处理完后保留一个不含正文的 tombstone（处理标记）：序号、`draft: null` 和文字指纹。它阻止延迟到达的旧请求把已处理版本重新生成。没有按时间、数量或程序启动次数删除未确认草稿的逻辑；无法识别或损坏的草稿保留原件并显示提示。

`GET /api/drafts` 返回待处理草稿。页面持续提示“等待确认”，由用户恢复或丢弃；重启不自动把旧草稿写入正式记录。恢复未检查的任务答案只填入输入框，仍需用户点击“检查答案”。旧浏览器 localStorage 草稿在原浏览器再次打开时迁入，应用不会扫描别的浏览器资料。项目移动或端口改变后，跟随项目的磁盘草稿仍可读取。

Claude Code 打磨时补充的三点（只加强，不改变上面的规则）：

- **同一字段按顺序写盘：** `Drafts.persist()` 对同一个“客户端 + 卡片 + 字段”排队发送；轮到时如果内存里已有更新的序号，旧版本不再发出。较新的版本自己报告成败，旧版本被覆盖不再显示“尚未写入磁盘”。
- **启动时核对旧窗口的草稿：** `Drafts.reconcile()` 在载入时和 3.5 秒后各运行一次，对其他客户端、超过 3 秒的草稿调用 `/api/drafts/confirm`。是否清除仍完全由服务端按正式记录判断；内容不一致的草稿原样保留并提示。3 秒的间隔避免和仍在运行、正要自己确认的窗口抢先。
- **同一字段只留一份（2026-10，CODEX_BRIEF 18.3）：** 新草稿到达时，别的窗口留下的同一卡片同一字段的草稿移到 `progress/drafts/archive/`（不删除），原位置留下处理标记，迟到的旧请求不能把它复活；和正式记录相同的草稿直接视为已处理。每次服务启动也按这条规则整理一遍已堆积的草稿，结果写进日志。
- **提示的时机：** 页面只把“较早窗口的、或在正常保存时间内没有确认的”草稿显示为提示（本窗口 4 秒、其他窗口 6.5 秒内视为仍在保存中）；一旦出现保存失败信息，全部草稿立即显示。完成按钮的禁用条件、`flushPending()` 和退出保护仍按全部草稿计算，没有放宽。页面隐藏时 `api.leaving` 让小请求带 `keepalive`，阅读位置有更大机会在关窗后落盘。
- **状态写入不再冲突：** 2026-11 起程序状态不查 revision（见“正式记录写入与冲突”），原先 `API.retryable` 的冲突重试已删除。草稿提示只统计用户输入的字段（自测回答、总结、文字结论、任务答案）；卡片页只显示卡片里的提示，页面顶部的总提示只在不在卡片页时出现。刷新或 ⌘R 时，上一页离开时用 keepalive 发出的位置保存可能晚于新页面读取记录到达；不重试会误报“记录已在别处更新”。所有承载文字的操作仍然不自动重试。
- **设置里的提示标记：** `progress/settings.json` 新增 `card_tips_seen`（布尔，默认 false），只决定第一次进入卡片页的轻提示是否显示；旧设置文件缺这个字段时按默认值处理，学习记录格式不变。
- **提示气泡在最上层：** `#toast` 用 `popover="manual"` 显示在浏览器最上层，设置等 `<dialog>` 浮层打开时也看得见；不支持 popover 的浏览器保持原来的固定定位。更新被未确认草稿挡住时，原因和“查看草稿”直接显示在设置的更新区，草稿列表提供“回到设置”。

## 5. 生命周期与安全退出

`Lifecycle` 用同一把条件锁管理 `active`、`updating`、`stopping`、`stop_requested`。需要读写学习数据的请求先进入 `operation()`；更新先关闭新请求入口，再等待已接收操作排空，避免“开始更新”和“开始保存”交叉。

页面启动后立即心跳，之后每 15 秒一次；页面重新可见、窗口获得焦点也补心跳。**2026-10 起（CODEX_BRIEF 18.2）：** Mac 原生外壳启动服务时传 `--parent-pid`，服务不再看心跳，而是每 2 秒检查外壳进程还在不在，外壳不在了才收尾退出（实测原因：窗口隐藏约 8 分钟后 WebKit 会暂停页面，心跳停止，旧的 60 秒超时把服务关掉，于是弹出"后台意外停止"）。其他启动方式（Windows Edge 窗口、备用浏览器）仍靠心跳，超时放宽到 30 分钟。每次退出都在 `logs/learning-hub-*.log` 写明原因（外壳退出、外壳要求、信号、心跳超时或异常）。外壳发现服务意外退出时自动重启（同一端口），10 分钟内第 4 次才弹框；页面顶部显示"正在重新连接……"，请求在约一分钟内逐渐加长间隔重试。网页内容进程被系统结束时，外壳自动重新加载页面。重连后，本窗口还没确认的文字按字段重新保存；如果那个字段在这期间被别的窗口改过，就保留草稿让用户选择。服务使用单调时钟判断心跳超时，并且没有活动操作、没有更新，才关闭。`/api/health`、`/api/heartbeat`、`/api/updates/status` 只访问内存，不读学习记录、不等待内容锁。控制修改请求带 `X-Learning-Control`；健康响应不返回令牌。

Mac 正常退出和重新载入先调用 `window.learningHub.flushPending()`，覆盖文本队列、阅读位置、设置队列和草稿保护。返回值区分：

| 字段 | 含义 |
|---|---|
| `saved` | 所有编辑队列确认，且没有任何待确认草稿；未检查任务答案或旧客户端草稿也使它为 false |
| `protected` | 当前草稿的最新版本都已获得磁盘确认 |
| `updating` | 页面知道有内容更新正在进行 |

原生壳通常等待最多 2 秒；超时改查 `protectionState()`。正式保存未成功但草稿已经可靠落盘，可以退出；两者都未确认则取消正常退出并保留窗口。更新期间继续等待，不能按普通 2 秒超时强杀事务。服务也会独立拒绝更新中的普通 shutdown；收到系统停止信号时延后到事务结束。

`ServiceController` 只关闭它拥有、且健康身份核对成功的进程。收到经过认证的 shutdown 成功响应后，正常等 2 秒，仍不退出才强制结束这个明确的子进程；不会按端口、程序名或陈旧 pid 文件寻找进程来杀。复用的已有服务不归当前壳强制结束。

Windows 没有同等的原生退出回调，靠即时磁盘草稿、页面离开时冲刷和心跳收尾；尚未保护的输入会请求浏览器离页确认，具体行为必须在 Windows 真机验收。强杀或断电前从未抵达任何磁盘的文字不能凭空恢复。

## 6. 内容更新的边界与事务

允许替换的完整根只有：

```text
content/  data/  docs/  tools/  HANDOFF.md  AGENTS.md  .agents/skills/learning-ui/
```

更新器不构造 `ProgressStore` 或 `DraftStore`，不访问 `progress/`、`workspace/`，也不遍历项目根目录。它只访问七根内容、自己拥有的内容备份/事务区、指定 ZIP 和固定的程序校验依赖。受认可脚本和 vendored PyYAML 是可信程序依赖的只读访问，不是更新包可以覆盖的对象。

流程与代码位置：

1. `UpdateManager._check()` 只列出系统下载目录顶层的命名更新包；应用内额外限制 ZIP 为 64 MiB，按日期与整数批次比较内容版本。
2. `UpdatePackage` 检查所有条目：七根范围、必需根（`content/`、`data/`；其余五根包里有才替换，没有就保持原样）、必需文件、路径歧义、重复/大小写/Unicode 冲突、链接、特殊文件、内部内容引用和大小限制。读取同一份不可变 ZIP 字节来计算 SHA-256 并解析。
3. `inspect_update()` 在系统临时目录解包，真正运行校验；不替换现有课程。只有通过的新版候选才展示版本差异和新增卡片，点击“检查更新”本身不应用。
4. 用户点击“更新”后，页面先冲刷输入。后端 `reserve_update()` 关闭正式写入口、排空已接收操作；复制已确认 ZIP 到作业私有临时目录，再检查一次。
5. 通过独立子进程运行：

   ```text
   python -B -m app.updater.update_content ZIP --root ROOT
     --non-interactive --expected-sha256 SHA --expected-content-version OLD_VERSION
   ```

   worker 在同一个独占内容锁内核对 ZIP 指纹和现有内容版本。候选被换掉，或检查之后另一更新器已改课程，都拒绝应用，防止旧候选覆盖新课程。
6. `transaction.replace_locked()` 备份七根、同步备份与暂存树，发布持久事务清单，再逐根替换并校验。成功提交后清理暂存，保留最近 10 份有本工具完整标记的内容备份；手工或旧格式备份不轮换。
7. worker 逐行输出 JSON `progress` / `result` / `error`，应用轮询内存状态。前端成功后重新 bootstrap 和 visit，以正常新卡规则显示结果。

事务清单为 `backups/.content-update-transaction.json`，内容锁为 `backups/.content-update.lock`。清单记录旧根是否存在、备份、暂存和事务阶段。异常时按清单恢复七根；强中断后，下次服务启动或备用更新入口调用 `recover_pending()`。未完成事务存在时，普通内容读者拒绝读取混合版本；已提交事务只清理，不倒退有效新内容。回滚失败保留清单与备份，明确报告，只有确认恢复成功才向页面说“内容已恢复原样”。

`content_check.py` 是更新时唯一的内容检查：用 `app/server/content.py` 的 `validate_course()` 与 `load_content()` 把候选课程完整读一遍，读不通就失败。**更新包里的任何代码都不执行**，包里的 `tools/validate_content.py` 只是随内容复制的作者自查工具。（2026-11 起取代原先“运行经指纹认可的包内校验脚本”的做法：认可列表、隔离运行器和每次改脚本都要人工复核的流程一并删除。）

检查结果只含卡片 id 和加载器的固定问题描述，不含答案或 YAML 原文，所以页面可以显示前几行；其他错误仍只显示分类后的安全提示。

## 7. Mac 和 Windows 的差异

| 部分 | Mac | Windows |
|---|---|---|
| 外壳 | Swift + AppKit + WKWebView，独立 `.app` | Edge `--app=` 窗口，没有原生 Windows 程序 |
| Python 选择 | 优先系统 Python 3.9+，再查常用 conda learn 位置及环境登记 | 安装/备用脚本先查 `py -3` 的版本，再查 conda learn；快捷方式直接运行选定的 `pythonw.exe` |
| 单实例 | 原生锁激活已有窗口；服务另有实例锁和启动登记 | 服务实例锁；重复启动复用通过健康核对的服务 |
| 退出 | 原生 flush → 等更新结束 → 认证 shutdown → 收尾自己的进程；外壳异常结束时服务凭 `--parent-pid` 在约 2 秒内退出 | 页面关闭后靠心跳超时收尾（30 分钟；看护进程待 Windows 上实现，见 CODEX_BRIEF 18.2） |
| 文件锁 | `fcntl.flock`，共享读/独占写 | `msvcrt` 固定第 0 字节互斥；共享读请求也保守串行 |
| 原子替换 | `os.replace`，错误传播 | 只对占用/权限类错误最多重试 5 次；失败不假装成功 |
| 同步目录 | 目录 fsync，失败报告 | Python 标准库不支持目录 fsync，明确跳过；文件内容仍 fsync |
| 下载目录 | 用户 Downloads | `SHGetKnownFolderPath` 查询真实 Downloads，支持迁移位置 |
| 文件定位 | `open -R` | `explorer.exe /select,路径` |
| 键盘提示 | ⌘ | Ctrl |

`WindowController.swift` 管窗口、首帧背景、缩放和 JS flush；`Menus.swift` 建标准编辑菜单；`Bridge.swift` 限制原生消息只能来自本服务的主框架。外链交默认浏览器；官方视频 iframe 保留在 WebKit，不能调用原生桥。原生专用顶部留白由网页的 `data-desktop` 样式生效。

浏览器资料不放在分享包中：macOS 14+ 使用按项目路径哈希派生 UUID 的持久 WK 数据仓库，由 WebKit 管理其系统位置；macOS 13 回退到非持久 WK 数据仓库；正式记录和未确认磁盘草稿仍由 Python 服务持久保存，不使用跨项目默认仓库。Windows 使用 `%LOCALAPPDATA%/LearningHub/Browser/<project_id>/` 独立 Edge 资料。项目 id 是规范化绝对项目路径的哈希；搬家会改变浏览器命名空间，正式记录和磁盘草稿仍在项目目录中。

原生窗口使用外壳注入的真实系统深浅色及变化事件判断“跟随系统”，避免手动深色窗口反过来影响 WebKit 的系统媒体查询。后端意外停止时可在原生提示中选“重新启动后台”；外壳仅尝试原端口，成功后调用 `window.learningHub.reconnect()` 更新控制令牌、重读记录并重新确认磁盘草稿，保留输入框 DOM 和文字，冲突留给用户逐条恢复。

Mac 的项目位置和窗口尺寸由 UserDefaults 记住。`--ephemeral` / `--ephemeral-profile` 只让 WebKit 资料不持久，不能理解为应用所有偏好都进入临时模式。

## 8. 如何重新编译 Mac 外壳

在项目根目录运行：

```bash
bash app/macos/build.sh
```

脚本先确认 `swiftc`，用系统 `iconutil` 把用户提供的 `app/assets/icons/orange/AppIcon.iconset`（各尺寸齐全，16、32 像素为加粗版）原样打包成 `app/macos/build/LearningHub.icns`，再直接编译 Swift 文件，组装根目录的 `学习台.app` 并做本机临时签名及校验。不缩放、裁切或另加边距。Windows 直接使用用户提供的多尺寸 `app/assets/icons/orange/learning-hub.ico`。之前的台灯图标（`app/assets/icons/` 下的 PNG、`.icns`、`.ico`）原样保留作存档，构建不再读取或改写它们。旧的图标绘制脚本已移除。中间文件位于 `app/macos/build/`；这些构建产物不进 Git。当前按本机架构构建，最低部署目标 macOS 13，不是通用双架构二进制，也没有发布签名或公证。

改 Swift、Info.plist 或外壳图标后需要重编译；改 `app/static/`、Python 后端或课程无需重编译外壳。应用本身并未内嵌课程/后端；它查找旁边的项目，找不到再用上次位置或让用户选择。

原生开发检查必须指定事先准备好的临时项目，例如：

```bash
"学习台.app/Contents/MacOS/LearningHub" --project-root "$TEMP_PROJECT" --ephemeral
```

`TEMP_PROJECT` 必须是仅从明确允许的源码和课程清单复制出的临时副本，不能指向真实项目。构建本身不启动服务；不要为了测试直接打开真实项目的应用图标。原生自动化工具可能在读取已退出应用的状态时自动重启应用，且不保留 CLI 参数：因此自动化验收还应把 `.app` 复制到临时项目内、设置独立的验收 bundle id 并临时签名，再绑定该验收应用。退出后通过进程或应用清单确认，不能再读取原窗口状态导致自动重开。

## 9. 怎么测试与定位故障

在项目根目录运行全套标准库测试，无需额外安装依赖：

```bash
python3 -B -m unittest discover app/tests -q
```

Mac 可另外用系统 Python 运行同一命令，检查 Python 3.9 兼容性。测试建立 `TemporaryDirectory` 或明确 fixtures；即使测试读取真实课程源码，也只复制明确内容根，不复制整个项目。

| 复查问题 | 优先看哪些测试 |
|---|---|
| 内容、退休、推荐、判题、答案边界 | `test_content.py`、`test_rules.py`、`test_judge.py`、`test_views.py`、`test_export.py`、`test_security.py` |
| 正式保存、冲突、磁盘故障、崩溃恢复与迁移 | `test_progress.py`、`test_migrations.py` |
| 草稿永久保留、乱序、处理标记、显式确认 | `test_drafts.py`、`test_update_api.py` |
| 心跳、排空、重复启动、认证退出 | `test_lifecycle.py`、`test_desktop.py` |
| ZIP、白名单零访问、回滚、中断、worker 版本绑定 | `test_updater.py`、`test_update_api.py` |
| 校验诊断是否泄漏 expected 或解法 | `test_update_privacy.py` |
| Windows 分支与文件语义 | `test_platform.py`、`test_desktop.py` |
| 分享包私人目录零访问、解包可运行 | `test_distribution.py` |

`test_updater.monitor_parent()` 监测假项目的 open/stat/lstat/scandir/readlink/rename 等访问；验证“不碰私人目录”不能只比较文件是否改变。永远不要用真实 workspace 当测试哨兵。

测试不依赖具体课程：默认用本项目的课程，设 `LEARNING_HUB_TEST_COURSE=app/tests/fixtures/demo-course` 即对三张卡的示例课程运行同一套测试（开源副本里示例课程就是 `content/`）。

改前改后对比：`app/tests/browser_snapshot.py` 冻结时钟、走固定流程，保存 91 个页面状态的截图、DOM、每个元素的计算样式和位置；先在旧版本拍一份，改完再拍一份，用 `--compare` 对比。纯重构应当完全相同（Edge 下命令面板文字抗锯齿和“揭示答案”后的滚动时序有已知的小噪声）。它按本项目课程的卡片设计，不进开源副本。`app/tests/browser_field_conflicts.py` 用两个窗口验证字段冲突规则，WebKit 和 Chromium 都可运行。

浏览器检查脚本为 `app/tests/browser_phase3.py`（完整前端流程）和 `app/tests/browser_phase3_reconnect.py`（真实服务重启与外观桥），以及 `app/tests/browser_phase3_exit_focus.py`（更新焦点、未检查答案与旧客户端的退出保护），使用项目外安装的 Playwright 和显式指定的本机浏览器。三者都用 `--browser` 指定浏览器路径；只有 `browser_phase3.py` 另外支持可选的 `--screenshots` 输出目录。它们自行创建临时课程、记录和浏览器会话，不属于应用运行依赖。截图证明布局，不能替代真实点击、键盘、滚动和退出流程。

原生 WKWebView、菜单、外链、中文输入法、关闭重开需要实际 Mac 操作。Windows 分支模拟通过不等于真机验收；照 `app/WINDOWS_TEST.md` 在 Windows 电脑验证 Python/Edge、快捷方式无黑窗口、保存恢复、心跳关闭、字体、文件定位和更新。

排查入口：启动问题看 `logs/` 和原生弹窗；保存问题看 API 错误、页面草稿提示及 `progress.py`；内容更新问题看更新状态、事务清单和内容备份。读取或处理用户真实记录前遵守 AGENTS 权限；复现优先用临时副本，不能删除用户原件或为了消除报错手动重建空记录。

## 10. 分享与接手复查注意点

生成本地分享 ZIP：

```bash
python3 -B app/distribution/build_share.py
```

输出在 `app/distribution/dist/`，可用 `--name learning-hub-share-example.zip` 改文件名；`--root` 用于明确的临时测试根。`FILES` 和 `TREES` 是允许清单，程序先检查清单再打包，仅在允许子树内遍历。它不会扫描整个项目再排除私人目录；`progress/`、`workspace/`、`logs/`、`backups/`、Git 历史、WORKLOG、截图及构建产物不读入分享包。符号链接/junction 在进入前拒绝。新增可运行模块或必要说明后，应同时检查分享清单和分享测试。

分享包提供源码、课程和离线依赖；Mac 收件人在自己的机器编译外壳，Windows 收件人运行安装脚本。每台电脑独立保存学习记录，没有同步功能。本次分享不是开源发布；未来开源应另建不带当前历史的干净仓库，并核对第三方许可证和内容授权。

接手时优先守住以下边界：

- 学习规则只在服务端；公开字段由 `views.py`、`export.py` 和更新错误分类控制。
- 四种版本分开：课程格式、内容版本、学习记录格式、保存 revision；草稿还有独立信封版本。
- 草稿保护与正式保存必须分别确认，不能用一个布尔“保存成功”吞掉冲突或磁盘错误。
- 新更新检查不能持久修改课程；正式应用要在内容锁内同时核对 ZIP 指纹和确认时的旧版本。
- 健康、心跳、进度轮询不应接入内容读取或学习记录锁。
- 更新器、分享工具不依赖真实 progress/workspace，测试也不能用它们。
- 更新器不执行包内代码；要让更新时多检查一项，就改 `app/server/content.py` 的加载规则（程序读课程时也会用到），不要改成去运行 `tools/` 里的脚本。
