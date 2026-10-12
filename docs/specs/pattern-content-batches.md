# 任务单：套路库第一期新增内容（4 批，共 39 个套路）

给：Muse　　基线：GitHub main 最新提交（先拉最新）。
背景见 `docs/specs/roadmap-and-patterns-complete.md` 的“B 线：内容补全”（本文件是它的执行版）。学习路线功能已经上线，现有 41 个套路已补好样例并通过自动测试；这一轮要新增 39 个，**内容的准确性比数量更重要**——用户会照着模板去写题、去应付面试和考试。

## 你要交付什么

4 个批次文件 + 4 份报告，放在（不要改 `static/data/patterns.json`，由我合并）：

```
docs/pattern-batches/batch-01.json   docs/pattern-batches/REPORT-batch-01.md
docs/pattern-batches/batch-02.json   docs/pattern-batches/REPORT-batch-02.md
docs/pattern-batches/batch-03.json   docs/pattern-batches/REPORT-batch-03.md
docs/pattern-batches/batch-04.json   docs/pattern-batches/REPORT-batch-04.md
```

可以一批一批交，不用等全部做完。每批都是一个 JSON 数组，每个元素是一个套路。

## 单个套路的格式（必须全部满足）

字段和 `static/data/patterns.json` 里现有条目完全一致，请打开 `sliding-window`、`kmp`、`union-find` 三条当范本，照它们的深度和语气写。字段：

| 字段 | 要求 |
| --- | --- |
| `id` | 小写字母/数字/短横线，全局唯一（用下面清单里给的 id，不要改） |
| `title` | 中文标题，用清单里给的 |
| `category` / `level` | 用清单里给的。`category` 只能是测试里允许的那些（`tests/test_patterns_data.py` 的 `ALLOWED_CATEGORIES`），`level` 只能是 入门/进阶/竞赛 |
| `signals` | ≥3 条，写“看到什么特征该想到它”，要具体，不要空话 |
| `idea` | 1–3 句，讲清**为什么这样做**，不是只说做什么 |
| `template_python` | 完整可运行的程序：**从标准输入读、向标准输出写**，有 `if __name__ == "__main__":`，不依赖第三方库 |
| `template_cpp` | 完整可编译的 C++17 程序，同样读写标准输入输出，`#include <bits/stdc++.h>` 可以用 |
| `complexity` | 时间 + 空间，一句话 |
| `pitfalls` | ≥2 条**真实常见的错误**（边界、溢出、写反方向等），不要写“注意细节”这种废话 |
| `confusions` | 和哪些套路容易混，怎么区分，1–3 句 |
| `examples` | ≥3 道经典例题：`{"source": "luogu", "id": "P1234", "title": "…"}` 或 `{"source": "leetcode-cn", "id": "704", "title": "…"}`。**题号和题名必须准确，不确定的宁可不写**（写不满 3 道就在报告里说明，我来补）。洛谷题号形如 `P1234`/`B2001`，力扣只写数字 |
| `related` | 只能引用**现有 41 个套路的 id** 或**同一批次里的 id** |
| `sample_input` / `sample_output` | 一组小样例（字符串，内容用 `\n` 换行）。**两份模板（Python 和 C++）对 `sample_input` 的输出都必须等于 `sample_output`**（忽略行尾空白）。样例要有意义（能体现这个套路，不是 `1 1`），而且要手工验算过结果 |

## 怎么自查（必须做）

每交一个批次之前跑：

```
python tools/check_pattern_batch.py docs/pattern-batches/batch-01.json
```

它会把你的批次合并进当前清单，然后检查：必填字段、id 唯一、category/level 合法、related 引用存在、例题格式、**真的运行每个 Python 模板并比对样例输出**，机器上有 g++ 时还会编译运行 C++ 模板。不会修改仓库文件。全部通过再交。

**C++ 注意：** 如果你的电脑没有 g++，脚本会跳过 C++，但 GitHub 的自动检查会真的编译，写错会让检查变红。请在没有编译器时更仔细地检查语法，或者用任意在线 C++ 编译器（C++17）把每个模板跑一遍样例再交。

**正确性比对要求：** 样例答案要靠你自己独立算出来（或用暴力解法验证），**不要**把模板的输出直接抄成样例输出再说“通过”。这是这套自动检查的意义所在。

## 批次清单（id — 标题 — 分类 — 难度）

### 批次 01（9 个）：数组、双指针、前缀和、二分、数据结构基础、字符串
- `sort-custom` — 排序与自定义比较 — 数组 — 入门
- `matrix-spiral` — 矩阵模拟（螺旋/旋转） — 数组 — 入门
- `dutch-flag` — 荷兰国旗（三路划分） — 双指针 — 入门
- `remove-inplace` — 原地删除/去重（快慢指针） — 双指针 — 入门
- `prefix-sum-2d` — 二维前缀和 — 前缀和与差分 — 入门
- `binary-search-rotated` — 旋转数组二分 — 二分 — 入门
- `binary-search-real` — 实数二分 — 二分 — 进阶
- `discretization` — 离散化 — 数据结构 — 入门
- `palindrome-expand` — 回文中心扩展 — 字符串 — 入门

> 注意：`category` 里“数组”要在 `ALLOWED_CATEGORIES` 里（已有）。

### 批次 02（10 个）：堆、栈、链表、树
- `heap-topk` — 堆与 Top K — 堆 — 入门
- `k-way-merge` — 合并 K 个有序序列 — 堆 — 进阶
- `median-two-heaps` — 双堆维护中位数 — 堆 — 进阶
- `expression-stack` — 表达式求值（逆波兰/双栈） — 栈 — 入门
- `linked-list-merge` — 链表合并与排序 — 链表 — 入门
- `tree-level-order` — 层序遍历 — 树 — 入门
- `bst-basics` — 二叉搜索树性质 — 树 — 入门
- `lca-tree` — 最近公共祖先（倍增） — 树 — 进阶
- `tree-diameter` — 树的直径 — 树 — 进阶
- `tree-construct` — 前序+中序建树 — 树 — 进阶

> 链表、树的题在标准输入输出下要自己约定输入格式（例如用数组表示节点），请在 `sample_input` 的写法上保持简单清楚，并在 `idea` 或模板注释里说明输入格式。

### 批次 03（8 个）：回溯、搜索、图
- `backtrack-nqueens` — N 皇后 — 回溯 — 进阶
- `backtrack-partition` — 分割与括号生成 — 回溯 — 入门
- `backtrack-pruning` — 回溯剪枝与去重 — 回溯 — 进阶
- `memo-dfs` — 记忆化搜索 — 搜索 — 入门
- `bfs-multi-source` — 多源 BFS — 图遍历 — 入门
- `bfs-01` — 0-1 BFS — 图遍历 — 进阶
- `dfs-graph-cycle` — 有向图判环（三色标记） — 图遍历 — 入门
- `mst-prim` — Prim 最小生成树 — 图论 — 进阶

### 批次 04（12 个）：动态规划、分治、数据结构
- `dp-fibonacci` — 爬楼梯/斐波那契类 — 动态规划 — 入门
- `dp-house-robber` — 打家劫舍（相邻不选） — 动态规划 — 入门
- `kadane` — 最大子数组和（Kadane） — 动态规划 — 入门
- `dp-grid` — 网格路径 DP — 动态规划 — 入门
- `dp-lcs` — 最长公共子序列 — 动态规划 — 入门
- `dp-edit-distance` — 编辑距离 — 动态规划 — 进阶
- `dp-stock-state` — 股票买卖（状态机 DP） — 动态规划 — 进阶
- `knapsack-multi` — 多重/分组背包 — 动态规划 — 进阶
- `dp-counting` — 计数 DP（路径数/方案数取模） — 动态规划 — 入门
- `merge-sort-inversions` — 归并排序与逆序对 — 分治 — 进阶
- `segment-tree` — 线段树（单点修改+区间查询） — 数据结构 — 进阶
- `sparse-table` — 稀疏表 ST — 数据结构 — 进阶

## 报告 `REPORT-batch-XX.md` 必须包含

1. 本批每个套路的例题清单，每道标注把握程度：**确定**（你清楚记得题号和题名对应）/ **凭记忆**（不太确定，我会抽查）。**凭记忆的宁可删掉**，写不满 3 道请说明。
2. 本批每个套路的 `sample_input`、你**独立验算**的方法（手算/暴力解/等价公式）一句话说明。
3. 自查命令的最后一行输出（贴出来）。
4. 没做到的和原因。

## 不要做的事

- 不要改 `static/data/patterns.json`、不要改测试、不要改别的套路的内容（发现现有条目有错，写进报告，我来处理）。
- 不要照抄网上现成的长篇题解或别人的版权文字；自己用自己的话写，模板代码自己写。
- 不要在项目目录里创建临时目录/缓存（见仓库根 `AGENTS.md`）；本机没有 g++ 时用在线编译器验证，不要往仓库里下载编译器。
- 不要联网抓取题面；例题只放题号和题名。

## 验收（我这边会做的）

合并批次后跑全量测试（含每个模板的真实运行）、抽查每批 3 个套路的内容准确性和 10 道例题的题号题名，通过后推送。有不合格的会退回并写明原因。
