# 学习路线（套路库搬进主站）+ 内容补全

基线：GitHub main 最新提交。分两条线并行：**A 功能**（学习路线页 + 进度）和 **B 内容**（把套路从 41 个补到约 150 个）。内容是这个功能的价值所在，**B 线质量优先于速度**。

## 产品定位

套路库现在是独立的只读页面（`static/patterns.html`，无需登录）。现在要把它做成主站里的**学习路线**：登录后在左侧栏“学习”分组里有一个入口，可以给每个套路标记学习状态，看每个分类和总体的进度，并且和用户自己的错题联动。独立页面保留（给没登录的人看，也方便分享），数据共用同一份。

决定：每个套路 **3 个状态**（未学 / 学习中 / 已掌握）+ **分类和总体进度条**；不做连续打卡天数和提醒。范围分两期：第一期 80 个（面试 + 学校课程），第二期补到约 150 个（含竞赛进阶）。

## A 线：功能（给 Muse）

硬规则同 `docs/specs/ai-quality-round1.md` 的“硬规则”一节（测试不真调 AI、不在项目里建临时目录、严格 CSP、改静态资源就同步版本号和测试 pin、迁移幂等）。迁移号接在最新之后（如果 AI 质量那条线占了 74，这里用 75；先 `grep -n "MIGRATIONS.append" db.py` 看最新号）。

### A1 数据与接口

- 表 `pattern_progress(user_id → users ON DELETE CASCADE, pattern_id TEXT, status TEXT CHECK in ('learning','mastered'), updated_at, PRIMARY KEY(user_id, pattern_id))`；**没有记录 = 未学**。
- 表 `pattern_example_done(user_id, pattern_id, example_key TEXT, done_at, PRIMARY KEY(user_id, pattern_id, example_key))`，`example_key` = `<source>:<id>`（如 `luogu:P2249`）。
- 表 `problem_patterns(problem_id → problems ON DELETE CASCADE, pattern_id TEXT, PRIMARY KEY(problem_id, pattern_id))`：一道错题最多挂 3 个套路。
- `pattern_id` 必须出现在 `static/data/patterns.json` 里（服务端启动时读一次作白名单）；不在就 422。
- 接口（都要登录 + CSRF 头，限流用现有 `rate_limited`）：
  - `GET /api/roadmap` → `{progress: {pattern_id: status}, examples_done: {pattern_id: [key,…]}, mistake_counts: {pattern_id: N}}`，只含本人数据。
  - `PUT /api/roadmap/{pattern_id}` `{status: "none"|"learning"|"mastered"}`（none = 删除记录）。
  - `PUT /api/roadmap/{pattern_id}/examples/{key}` `{done: bool}`（`key` 要是该套路 `examples` 里真实存在的）。
  - 新增/编辑题目接口 `POST /api/problems`、`PUT /api/problems/{id}` 接受可选 `pattern_ids: string[]`（≤3，去重，必须在白名单）。编辑时不传 = 保持不变，传 `[]` = 清空。
- 账号注销要一并删除这几张表（`main.py` 的 `delete_account_data`）；JSON 导出加上 `roadmap`（进度和例题完成）以及每道题的 `pattern_ids`；`legal.py` 隐私政策补一条“学习路线进度”，`TERMS_VERSION` 改成当天日期并同步测试 pin。

### A2 界面（主站新视图 `roadmap`）

- 左侧栏“学习”分组加入口“学习路线”；手机“更多”里也加；图标沿用现有风格（内联 SVG）。
- 页面顶部：总体进度（已掌握 X / 总数、学习中 Y），然后**每个分类一条进度条**（该分类里已掌握/总数）。
- 筛选：分类、难度（入门/进阶/竞赛）、状态、搜索（标题、触发信号）。
- 每个套路一张卡片：标题、分类、难度、**三态切换**（三个按钮，当前态高亮）、“你记过 N 道错题”徽标（>0 时显示，点它跳到“全部记录”并按该套路筛选，如果工作量大这一条可以放第二批）。展开后显示全部内容：触发信号、核心思路、Python/C++ 模板（标签页切换 + 复制按钮）、复杂度、常见坑、易混淆、相关套路（可点击跳转）、经典例题（每道有勾选框 + 链接）。
- **渲染逻辑抽成共享文件** `static/pattern-render.js`，独立页 `patterns.js` 和主站新视图共用，不要复制两份。
- 一个套路的例题全部勾完时，提示“要不要把状态改成已掌握？”（只提示，不自动改）。
- 新增记录页和编辑题目里加“属于哪个套路（可选，最多 3 个）”：带搜索的选择器，选项来自 `patterns.json`。
- 薄弱提示：总览页或学习路线页顶部，如果有“错题多、但状态还是未学”的套路，给一行建议“《X》你错过 N 题但还没学，建议先学这个”（最多提示 3 个）。
- 手机宽度可用，键盘可操作（三态按钮是 `aria-pressed` 的按钮组），有“减少动画”偏好时不做动效。
- 离线/请求失败静默回滚按钮状态；晚到响应按登录代次和账号校验丢弃（参照 `recommend.js` 的 `ticket()/current()`）。

### A3 测试

接口越权/非法 id/限流、迁移幂等、注销清理、导出含新字段、前端行为测试（参照 `tests/*_behaviour.cjs` 的写法：三态切换、回滚、迟到响应丢弃）、`patterns.json` 数据检查（见 B 线的数据测试，A 线只需要保证白名单读取正确）。

## B 线：内容补全

### B1 单个套路的标准（每个都必须满足）

字段沿用 `static/data/patterns.json` 现有结构：`id`（小写短横线，唯一）、`title`、`category`、`level`（入门/进阶/竞赛）、`signals`（≥3 条，写“看到什么特征该想到它”）、`idea`（1–3 句，讲清为什么这样做）、`template_python`、`template_cpp`（**都是能直接提交的完整程序**，读标准输入、写标准输出）、`complexity`、`pitfalls`（≥2 条真实常见错误）、`confusions`（和哪些套路容易混，怎么区分）、`examples`（≥3 道，洛谷题号 + 准确题名，力扣用题号 + 准确中文题名；**不确定的题宁缺毋滥**）、`related`（引用的 id 必须存在）。

**新增必填字段：** `sample_input`、`sample_output`：一组小样例，**两份模板对它都输出 `sample_output`**（忽略行尾空白）。这是自动质量关：测试会真的运行每个模板。

### B2 自动校验（测试，随内容一起交）

`tests/test_patterns_data.py`：
- 结构：必填字段齐全、`id` 唯一、`category`/`level` 在允许集合内、`related` 引用存在、`examples` 的 `source` 只能是 `luogu`/`leetcode-cn`、洛谷题号形如 `P1234`/`B2001`。
- 运行：对每个套路用 `subprocess` 跑 `python <模板>`（超时 5 秒），输入 `sample_input`，输出必须等于 `sample_output`。
- C++：如果机器上有 `g++` 则编译（`-O2 -std=c++17`）并同样比对；没有 `g++` 就 `pytest.skip`（GitHub Actions 的 Linux 有 g++，会真正跑）。
- 不得联网；临时文件放系统临时目录。

### B3 交付方式（并行作者不冲突）

每位作者交一个批次文件 `docs/pattern-batches/batch-<编号>.json`（数组，每项一个套路，字段如上），**不要直接改 `patterns.json`**。我来合并、去重、按提纲排序后写入。每批 8–12 个套路，同一批尽量同一分类。每批附 `REPORT.md`：列出每个套路的例题来源把握程度（确定 / 凭记忆），我会抽查。

### B4 提纲

下表是要收录的完整清单。**已有**的 41 个保留，但要补 `sample_input/sample_output`（第一期的第一批任务），并检查模板是否真能通过上面的运行测试，发现错误就修。

#### 第一期（目标共 80 个 = 已有 41 + 新增 39）

已有 41 个（id）：binary-search-basic, binary-search-answer, two-pointers-opposite, sliding-window, prefix-sum, difference-array, hash-counting, monotonic-stack, interval-greedy, regret-greedy, monotonic-queue, bracket-stack, linked-list-two-pointers, linked-list-reverse, tree-traversal, tree-dp-intro, backtrack-subset, backtrack-permutation, bfs-grid, dfs-grid, topo-sort, union-find, dijkstra, bellman-ford, floyd-warshall, linear-dp, knapsack-01, knapsack-complete, interval-dp, bitmask-dp, digit-dp-intro, mst-kruskal, graph-modeling, kmp, trie, manacher-intro, fast-pow-gcd, sieve-comb, bit-tricks, fenwick-intro, pitfalls-overflow。

新增 39 个：

| 分类 | id | 标题 | 难度 |
| --- | --- | --- | --- |
| 数组 | sort-custom | 排序与自定义比较 | 入门 |
| 数组 | matrix-spiral | 矩阵模拟（螺旋/旋转） | 入门 |
| 双指针 | dutch-flag | 荷兰国旗（三路划分） | 入门 |
| 双指针 | remove-inplace | 原地删除/去重（快慢指针） | 入门 |
| 前缀和与差分 | prefix-sum-2d | 二维前缀和 | 入门 |
| 二分 | binary-search-rotated | 旋转数组二分 | 入门 |
| 二分 | binary-search-real | 实数二分 | 进阶 |
| 数据结构 | discretization | 离散化 | 入门 |
| 堆 | heap-topk | 堆与 Top K | 入门 |
| 堆 | k-way-merge | 合并 K 个有序序列 | 进阶 |
| 堆 | median-two-heaps | 双堆维护中位数 | 进阶 |
| 栈 | expression-stack | 表达式求值（逆波兰/双栈） | 入门 |
| 链表 | linked-list-merge | 链表合并与排序 | 入门 |
| 树 | tree-level-order | 层序遍历 | 入门 |
| 树 | bst-basics | 二叉搜索树性质 | 入门 |
| 树 | lca-tree | 最近公共祖先（倍增） | 进阶 |
| 树 | tree-diameter | 树的直径 | 进阶 |
| 树 | tree-construct | 前序+中序建树 | 进阶 |
| 回溯 | backtrack-nqueens | N 皇后 | 进阶 |
| 回溯 | backtrack-partition | 分割与括号生成 | 入门 |
| 回溯 | backtrack-pruning | 回溯剪枝与去重 | 进阶 |
| 搜索 | memo-dfs | 记忆化搜索 | 入门 |
| 图遍历 | bfs-multi-source | 多源 BFS | 入门 |
| 图遍历 | bfs-01 | 0-1 BFS | 进阶 |
| 图遍历 | dfs-graph-cycle | 有向图判环（三色标记） | 入门 |
| 图论 | mst-prim | Prim 最小生成树 | 进阶 |
| 动态规划 | dp-fibonacci | 爬楼梯/斐波那契类 | 入门 |
| 动态规划 | dp-house-robber | 打家劫舍（相邻不选） | 入门 |
| 动态规划 | kadane | 最大子数组和（Kadane） | 入门 |
| 动态规划 | dp-grid | 网格路径 DP | 入门 |
| 动态规划 | dp-lcs | 最长公共子序列 | 入门 |
| 动态规划 | dp-edit-distance | 编辑距离 | 进阶 |
| 动态规划 | dp-stock-state | 股票买卖（状态机 DP） | 进阶 |
| 动态规划 | knapsack-multi | 多重/分组背包 | 进阶 |
| 动态规划 | dp-counting | 计数 DP（路径数/方案数取模） | 入门 |
| 分治 | merge-sort-inversions | 归并排序与逆序对 | 进阶 |
| 数据结构 | segment-tree | 线段树（单点修改+区间查询） | 进阶 |
| 数据结构 | sparse-table | 稀疏表 ST | 进阶 |

#### 第二期（再补约 70 个，做完第一期再开）

- **字符串**：palindrome-expand 回文中心扩展、rolling-hash 字符串哈希、z-function Z 函数、suffix-array 后缀数组、aho-corasick AC 自动机
- **设计题**：design-min-stack 最小栈、design-lru LRU 缓存
- **数据结构**：segment-tree-lazy 懒标记线段树、sqrt-decomposition 分块、mo-algorithm 莫队、persistent-seg 主席树、hld 树链剖分、dsu-weighted 带权并查集、ordered-set 有序集合/平衡树用法、trie-xor 01 字典树
- **树**：tree-serialize 序列化、tree-rerooting 换根 DP、tree-centroid 点分治入门、euler-tour-dfs-order DFS 序
- **图**：euler-path 欧拉路径、scc-tarjan 强连通分量、bridges-cutpoints 割点与桥、bipartite-matching 匈牙利算法、max-flow-dinic 最大流 Dinic、min-cost-flow 最小费用流、spfa-difference-constraints 差分约束、graph-2sat 2-SAT、dijkstra-layered 分层图最短路
- **动态规划**：dp-expectation 概率/期望 DP、dp-monoqueue-opt 单调队列优化、dp-convex-hull-opt 斜率优化、dp-tree-knapsack 树上背包、dp-broken-profile 轮廓线 DP、dp-game 博弈 DP、dp-rolling-array 滚动数组、dp-reconstruct 方案还原、bit-subset-enum 子集枚举与高维前缀和
- **数学**：mod-inverse 模逆元、inclusion-exclusion 容斥原理、euler-phi 欧拉函数、matrix-pow 矩阵快速幂、probability-expectation 概率与期望、game-nim Nim/SG 博弈、exgcd 扩展欧几里得、crt 中国剩余定理、number-theory-blocks 整除分块、mobius-intro 莫比乌斯入门、linear-basis-xor 线性基、gauss-elimination 高斯消元、catalan-counting 卡特兰与计数
- **几何**：cross-product-basics 向量叉积、convex-hull 凸包
- **搜索**：bfs-state-space 状态空间 BFS、bidirectional-bfs 双向 BFS、a-star A*、meet-in-the-middle 折半搜索、iterative-deepening 迭代加深
- **分治与倍增**：binary-lifting 倍增、cdq-divide CDQ 分治
- **技巧**：sweep-line-intervals 扫描线、offline-queries 离线询问、greedy-exchange-argument 邻项交换贪心、greedy-huffman 哈夫曼、ternary-search 三分、constructive-basic 构造题入门、interactive-basic 交互题入门、bitset-opt bitset 优化
- **实现陷阱**：pitfalls-io-speed 输入输出提速、pitfalls-recursion-depth 递归深度、pitfalls-modular-negative 取模与负数

## 实施顺序

1. **B 线第一批**：给已有 41 个补 `sample_input/sample_output` 并修模板（这批同时验证测试框架和交付流程）。
2. **A 线与 B 线并行**：Muse 做 A 线；Doubao/其他作者按批次写第一期新增 39 个。
3. 第一期合并上线后，再开第二期。

## 验收

- `PYTHONUTF8=1 python -m pytest tests -q -p no:cacheprovider --ignore=tests/e2e` 全绿（本机 `.claude` 目录引起的 `test_billing_docs_consistency` 误报可忽略）。
- 每批内容：测试里每个模板都真实跑通；`REPORT.md` 列出把握不足的例题。
- A 线：REPORT 里逐条说明 A1/A2/A3 完成情况，以及没做的和原因（比如“按套路筛选全部记录”如果放第二批要明说）。
