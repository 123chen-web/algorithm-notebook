# 任务单：套路库第二期（8 批，共 70 个套路）

给：Muse　　基线：GitHub main 最新提交（先拉最新；现有清单已经是 **80 个**套路）。
**第一期（`docs/specs/pattern-content-batches.md`）里的全部规则继续适用**：字段格式、`sample_input/sample_output` 的独立验算、`python tools/check_pattern_batch.py` 自查、报告格式、不要改 `patterns.json`、不要在项目目录建临时文件、不联网抓题面。**这份文件只写第二期的新增要求和清单。** 先通读第一期的任务单再开始。

第二期偏竞赛进阶，难度高、容易出错，所以**准确性比数量更重要**：写不准的宁可不写，在报告里说明，我来处理。

## 第一期验收时发现的问题，这一期必须避免

1. **洛谷题号和题名要一一对应。** 第一期里有一道把 P1210 写成了“回文质数”（实际 P1210 是《最长的回文》，回文质数是 P1217）。题号记不牢就不要写，宁可例题不够 3 道、在报告里说明。**报告里“凭记忆”的题我会逐个核对，核对不上的会删掉。**
2. **Python 模板必须是真实换行 + 4 个空格缩进**；**C++ 模板也用 4 个空格缩进**，运算符两边留空格（写 `a && !b`、`x != y`、`c ? a : b`，不要写 `&&!`、`!=` 贴着、`?`/`:` 贴着）。第一期的批次 02 因为缩进是 1 个空格，被我整体重排过一遍，这次请一次写对。
3. **本机没有 g++ 的话**，用在线编译器（C++17）把每个 C++ 模板跑一遍样例再交。GitHub 的自动检查会真的编译，写错会变红。

## 第二期的特殊情况（按这里的约定写）

- **交互题入门（`interactive-basic`）**：标准输入输出没法真的做交互。模板写成“本地模拟评测机”的形式：输入先给一个隐藏的整数 `secret`，程序里用一个 `ask(x)` 函数代表“向评测机提问”，用二分找出 `secret`，最后输出 `secret` 和**提问次数**。在 `idea` 里讲清真实交互题要用 `flush`（C++ 的 `cout << endl` 或 `fflush`，Python 的 `print(..., flush=True)`）和提问次数上限。
- **有序集合/平衡树用法（`ordered-set`）**：Python 标准库没有平衡树，Python 模板用 `bisect` + 列表演示同样的“插入、查前驱/后继、查排名”接口，并在 `pitfalls` 里写明列表插入是 O(n)、大数据要用 C++ 的 `std::set` / `multiset` 或第三方库；C++ 模板用 `std::set`。
- **后缀数组、AC 自动机、可持久化线段树等长模板**：优先写**正确、清楚**的版本，复杂度可以不是最优（比如后缀数组用 O(n log² n) 的倍增 + sort），在 `complexity` 和 `pitfalls` 里写清楚，不要为了优化把模板写得难懂。
- **概率期望 / 数论类**：样例的答案必须是整数或固定小数位输出（比如“对 998244353 取模”或“保留 6 位小数”），不要依赖浮点误差。
- **类别（`category`）只能用测试允许的那些**（`tests/test_patterns_data.py` 的 `ALLOWED_CATEGORIES`）。下面清单里给的类别都在允许范围内。
- **`related` 只能引用**现有 80 个套路的 id 或同批次的 id（自查脚本会拦）。需要引用其他批次的，合并后我加。
- **每批 7–12 个**，可以一批一批交，不用等全部做完。

## 批次清单（id — 标题 — 分类 — 难度）

### 批次 05（7 个）：字符串与设计题
- `rolling-hash` — 字符串哈希（滚动哈希） — 字符串 — 进阶
- `z-function` — Z 函数 — 字符串 — 进阶
- `suffix-array` — 后缀数组（倍增） — 字符串 — 竞赛
- `aho-corasick` — AC 自动机 — 字符串 — 竞赛
- `trie-xor` — 01 字典树（最大异或对） — 数据结构 — 进阶
- `design-min-stack` — 设计题：最小栈 — 设计题 — 入门
- `design-lru` — 设计题：LRU 缓存 — 设计题 — 进阶

### 批次 06（8 个）：数据结构进阶
- `segment-tree-lazy` — 线段树懒标记（区间加 + 区间和） — 数据结构 — 竞赛
- `sqrt-decomposition` — 分块 — 数据结构 — 进阶
- `mo-algorithm` — 莫队 — 数据结构 — 竞赛
- `persistent-seg` — 可持久化线段树（主席树，区间第 K 小） — 数据结构 — 竞赛
- `hld` — 树链剖分 — 数据结构 — 竞赛
- `dsu-weighted` — 带权并查集 — 数据结构 — 进阶
- `ordered-set` — 有序集合/平衡树用法 — 数据结构 — 进阶
- `fenwick-2d` — 二维树状数组 — 数据结构 — 进阶

### 批次 07（10 个）：树与图
- `tree-serialize` — 二叉树序列化与反序列化 — 树 — 进阶
- `tree-rerooting` — 换根 DP — 树 — 竞赛
- `tree-centroid` — 树的重心与点分治入门 — 树 — 竞赛
- `euler-tour-dfs-order` — DFS 序与子树区间 — 树 — 进阶
- `euler-path` — 欧拉路径/回路 — 图论 — 进阶
- `scc-tarjan` — 强连通分量（Tarjan） — 图论 — 竞赛
- `bridges-cutpoints` — 割点与桥 — 图论 — 竞赛
- `bipartite-matching` — 二分图匹配（匈牙利算法） — 图论 — 进阶
- `spfa-difference-constraints` — 差分约束与 SPFA — 最短路 — 进阶
- `graph-2sat` — 2-SAT — 图论 — 竞赛

### 批次 08（9 个）：网络流、搜索进阶、倍增
- `max-flow-dinic` — 最大流 Dinic — 图论 — 竞赛
- `min-cost-flow` — 最小费用最大流 — 图论 — 竞赛
- `dijkstra-layered` — 分层图最短路 — 最短路 — 进阶
- `bfs-state-space` — 状态空间 BFS — 搜索 — 进阶
- `bidirectional-bfs` — 双向 BFS — 搜索 — 进阶
- `a-star` — A* 搜索 — 搜索 — 竞赛
- `meet-in-the-middle` — 折半搜索 — 搜索 — 竞赛
- `iterative-deepening` — 迭代加深搜索 — 搜索 — 竞赛
- `binary-lifting` — 倍增 — 分治与倍增 — 进阶

### 批次 09（9 个）：动态规划进阶
- `dp-expectation` — 概率/期望 DP — 动态规划 — 竞赛
- `dp-monoqueue-opt` — 单调队列优化 DP — 动态规划 — 竞赛
- `dp-convex-hull-opt` — 斜率优化 DP — 动态规划 — 竞赛
- `dp-tree-knapsack` — 树上背包 — 动态规划 — 竞赛
- `dp-broken-profile` — 轮廓线 DP（插头 DP 入门） — 动态规划 — 竞赛
- `dp-game` — 博弈 DP — 动态规划 — 进阶
- `dp-rolling-array` — 滚动数组优化 — 动态规划 — 入门
- `dp-reconstruct` — DP 方案还原 — 动态规划 — 入门
- `bit-subset-enum` — 子集枚举与高维前缀和 — 位运算 — 竞赛

### 批次 10（7 个）：数论
- `mod-inverse` — 模逆元 — 数学 — 进阶
- `inclusion-exclusion` — 容斥原理 — 数学 — 进阶
- `euler-phi` — 欧拉函数 — 数学 — 进阶
- `matrix-pow` — 矩阵快速幂 — 数学 — 进阶
- `exgcd` — 扩展欧几里得 — 数学 — 进阶
- `crt` — 中国剩余定理 — 数学 — 竞赛
- `number-theory-blocks` — 整除分块 — 数学 — 竞赛

### 批次 11（8 个）：数学进阶与几何
- `probability-expectation` — 概率与期望 — 数学 — 进阶
- `game-nim` — Nim 游戏与 SG 函数 — 数学 — 进阶
- `mobius-intro` — 莫比乌斯函数入门 — 数学 — 竞赛
- `linear-basis-xor` — 线性基（异或） — 数学 — 竞赛
- `gauss-elimination` — 高斯消元 — 数学 — 竞赛
- `catalan-counting` — 卡特兰数与计数 — 数学 — 进阶
- `cross-product-basics` — 向量叉积 — 几何 — 入门
- `convex-hull` — 凸包 — 几何 — 进阶

### 批次 12（12 个）：技巧与实现陷阱
- `sweep-line-intervals` — 扫描线（区间与会议室） — 技巧 — 进阶
- `offline-queries` — 离线询问 — 技巧 — 进阶
- `greedy-exchange-argument` — 邻项交换贪心 — 贪心 — 进阶
- `greedy-huffman` — 哈夫曼与合并果子 — 贪心 — 入门
- `ternary-search` — 三分 — 二分 — 进阶
- `constructive-basic` — 构造题入门 — 技巧 — 进阶
- `interactive-basic` — 交互题入门 — 技巧 — 入门
- `bitset-opt` — bitset 优化 — 技巧 — 竞赛
- `cdq-divide` — CDQ 分治 — 分治 — 竞赛
- `pitfalls-io-speed` — 输入输出提速 — 实现陷阱 — 入门
- `pitfalls-recursion-depth` — 递归深度与栈溢出 — 实现陷阱 — 入门
- `pitfalls-modular-negative` — 取模与负数 — 实现陷阱 — 入门

> 合计 7+8+10+9+9+7+8+12 = **70** 个。完成后套路库一共 150 个。

## 例题的写法（再强调一次）

- 洛谷常见模板题有固定题号（例如“最大流”“最小费用最大流”“缩点”“割点”“二分图最大匹配”“线段树”等），**只有你确定题号和题名对应时才写**；写不准就不写。
- 每套路至少 3 道，洛谷和力扣可以混合；力扣只写题号数字和中文题名。
- 报告里每道例题标“确定 / 凭记忆”，“凭记忆”的我会逐个核对。

## 交付与验收

- 交付：`docs/pattern-batches/batch-05.json` … `batch-12.json` 和 `REPORT-batch-05.md` … `REPORT-batch-12.md`，可以分批交。
- 自查：每批交前跑 `python tools/check_pattern_batch.py docs/pattern-batches/batch-XX.json`，贴最后一行输出。
- 我这边：逐批合并、跑全量测试（Python 模板真实运行；GitHub 上 C++ 真实编译运行）、抽查每批 3 个套路的内容准确性和全部“凭记忆”的例题，通过后推送；不合格的会写明原因退回。
