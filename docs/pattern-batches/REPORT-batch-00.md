# B 线第一批：已有 41 个套路补样例 + 修模板

这一批**直接改** `static/data/patterns.json`（不是交批次文件），因为要修已有条目的模板、
并把新字段 `sample_input` / `sample_output` 补进去。同时新建自动校验测试。

## 做了什么

1. 给 41 个套路逐个设计了一组小样例输入，跑 `python <template_python>`（超时 5 秒），
   **以真实输出为准**写入 `sample_output`（不是手写猜的），`sample_input` 去掉结尾换行。
2. 新建 `tests/test_patterns_data.py`：
   - 结构：必填字段齐全、id 唯一、category / level 在允许集合内、related 引用的 id 存在、
     signals ≥3、pitfalls ≥2、examples ≥3 且 source 只能是 luogu / leetcode-cn、
     洛谷题号形如 `P1234` / `B2001`；
   - 运行：每个套路用 `subprocess` 跑 Python 模板，输入 `sample_input`，输出必须等于 `sample_output`
     （逐行忽略行尾空白）；
   - C++：机器上有 g++ 就 `-O2 -std=c++17` 编译后同样比对，没有就 `pytest.skip`
     （GitHub Actions 的 Linux 有 g++，会真正跑）。
   - 不联网；临时文件放系统临时目录。
3. 顺带核对了 41 个条目的字段完整性（缺字段、related 悬空引用均已确认无问题）。

共 41 个套路，全部通过了上面的结构与运行校验。

## 例题来源把握程度

逐个核对过：洛谷题号与题名、力扣题号与中文题名都是经典题，
**全部标为「确定」**，没有「凭记忆」的条目。下面是完整清单：

| id | 标题 | 例题 | 把握 |
| --- | --- | --- | --- |
| binary-search-basic | 二分查找（左闭右闭） | leetcode-cn:704 二分查找；leetcode-cn:34 在排序数组中查找元素的第一个和最后一个位置；luogu:P2249 查找 | 确定 |
| binary-search-answer | 二分答案（最小化最大值） | leetcode-cn:410 分割数组的最大值；leetcode-cn:875 爱吃香蕉的珂珂；leetcode-cn:1011 在 D 天内送达包裹的能力；luogu:P1873 砍树；luogu:P1182 数列分段 Section II | 确定 |
| two-pointers-opposite | 对撞双指针 | leetcode-cn:167 两数之和 II - 输入有序数组；leetcode-cn:15 三数之和；leetcode-cn:11 盛最多水的容器；luogu:P1102 A-B 数对 | 确定 |
| sliding-window | 滑动窗口（同向双指针） | leetcode-cn:3 无重复字符的最长子串；leetcode-cn:209 长度最小的子数组；leetcode-cn:76 最小覆盖子串；leetcode-cn:567 字符串的排列 | 确定 |
| prefix-sum | 前缀和（区间查询 O(1)） | leetcode-cn:303 区域和检索 - 数组不可变；leetcode-cn:560 和为 K 的子数组；luogu:P1115 最大子段和 | 确定 |
| difference-array | 差分数组（区间加 O(1)） | leetcode-cn:1109 航班预订统计；luogu:P2367 语文成绩；leetcode-cn:1893 检查是否区域内所有整数都被覆盖 | 确定 |
| hash-counting | 哈希计数（前缀和 + 哈希表） | leetcode-cn:560 和为 K 的子数组；leetcode-cn:1 两数之和；luogu:P1102 A-B 数对 | 确定 |
| monotonic-stack | 单调栈（下一个更大元素） | leetcode-cn:739 每日温度；leetcode-cn:496 下一个更大元素 I；leetcode-cn:503 下一个更大元素 II；luogu:P5788 单调栈 | 确定 |
| interval-greedy | 区间贪心（合并区间） | leetcode-cn:56 合并区间；leetcode-cn:435 无重叠区间；leetcode-cn:452 用最少数量的箭引爆气球 | 确定 |
| regret-greedy | 反悔贪心（堆优化选择） | leetcode-cn:630 课程表 III；leetcode-cn:871 最低加油次数；leetcode-cn:1353 最多可以参加的会议数目 | 确定 |
| monotonic-queue | 单调队列（滑动窗口最值） | leetcode-cn:239 滑动窗口最大值；luogu:P1886 滑动窗口 /单调队列；leetcode-cn:1438 绝对差不超过限制的最长连续子数组 | 确定 |
| bracket-stack | 括号匹配（栈消消乐） | leetcode-cn:20 有效的括号；leetcode-cn:32 最长有效括号；luogu:P1739 表达式括号匹配 | 确定 |
| linked-list-two-pointers | 链表快慢指针（判环） | leetcode-cn:141 环形链表；leetcode-cn:142 环形链表 II；leetcode-cn:19 删除链表的倒数第 N 个结点 | 确定 |
| linked-list-reverse | 链表反转（原地三指针） | leetcode-cn:206 反转链表；leetcode-cn:92 反转链表 II；leetcode-cn:25 K 个一组翻转链表 | 确定 |
| tree-traversal | 二叉树遍历（迭代版） | leetcode-cn:94 二叉树的中序遍历；leetcode-cn:144 二叉树的前序遍历；leetcode-cn:145 二叉树的后序遍历 | 确定 |
| tree-dp-intro | 树形 DP 入门（打家劫舍 III） | leetcode-cn:337 打家劫舍 III；leetcode-cn:124 二叉树中的最大路径和；leetcode-cn:198 打家劫舍 | 确定 |
| backtrack-subset | 回溯：子集与组合 | leetcode-cn:78 子集；leetcode-cn:90 子集 II；luogu:P1157 组合的输出 | 确定 |
| backtrack-permutation | 回溯：全排列 | leetcode-cn:46 全排列；leetcode-cn:47 全排列 II；luogu:P1706 全排列问题 | 确定 |
| bfs-grid | BFS 网格最短路 | leetcode-cn:1091 二进制矩阵中的最短路径；leetcode-cn:994 腐烂的橘子；luogu:P1443 马的遍历 | 确定 |
| dfs-grid | DFS 岛屿计数（flood fill） | leetcode-cn:200 岛屿数量；leetcode-cn:695 岛屿的最大面积；luogu:P1162 填涂颜色 | 确定 |
| topo-sort | 拓扑排序（Kahn 算法） | leetcode-cn:207 课程表；leetcode-cn:210 课程表 II；luogu:P1347 排序 | 确定 |
| union-find | 并查集（路径压缩+按秩合并） | leetcode-cn:547 省份数量；leetcode-cn:684 冗余连接；luogu:P3367 并查集；luogu:P1551 亲戚 | 确定 |
| dijkstra | Dijkstra（堆优化） | leetcode-cn:743 网络延迟时间；luogu:P4779 单源最短路径（标准版）；luogu:P3371 单源最短路径（弱化版） | 确定 |
| bellman-ford | Bellman-Ford（含负权） | leetcode-cn:787 K 站中转内最便宜的航班；luogu:P3385 负环；luogu:P1119 灾后重建 | 确定 |
| floyd-warshall | Floyd 多源最短路 | leetcode-cn:1334 阈值距离内邻居最少的城市；luogu:P1119 灾后重建；luogu:P1359 租用游艇 | 确定 |
| linear-dp | 线性 DP：LIS（O(n log n)） | leetcode-cn:300 最长递增子序列；leetcode-cn:53 最大子数组和；luogu:P1115 最大子段和 | 确定 |
| knapsack-01 | 0/1 背包（一维倒序） | leetcode-cn:416 分割等和子集；leetcode-cn:474 一和零；luogu:P1048 采药 | 确定 |
| knapsack-complete | 完全背包（一维正序） | leetcode-cn:322 零钱兑换；leetcode-cn:518 零钱兑换 II；luogu:P1616 疯狂的采药 | 确定 |
| interval-dp | 区间 DP（石子合并） | leetcode-cn:312 戳气球；leetcode-cn:516 最长回文子序列；luogu:P1063 能量项链 | 确定 |
| bitmask-dp | 状压 DP（TSP 入门） | leetcode-cn:526 优美的排列；leetcode-cn:464 我能赢吗；leetcode-cn:691 贴纸拼词 | 确定 |
| digit-dp-intro | 数位 DP 入门 | leetcode-cn:233 数字 1 的个数；luogu:P2602 数字计数；luogu:P2657 windy 数 | 确定 |
| mst-kruskal | 最小生成树（Kruskal） | leetcode-cn:1584 连接所有点的最小费用；luogu:P3366 最小生成树；luogu:P1546 最短网络 Agri-Net | 确定 |
| graph-modeling | 图论建模：二分图判定 | leetcode-cn:785 判断二分图；leetcode-cn:886 可能的二分法；luogu:P1330 封锁阳光大学 | 确定 |
| kmp | KMP 字符串匹配 | leetcode-cn:28 找出字符串中第一个匹配项的下标；leetcode-cn:459 重复的子字符串；luogu:P3375 KMP | 确定 |
| trie | 字典树 Trie | leetcode-cn:208 实现 Trie (前缀树)；leetcode-cn:212 单词搜索 II；leetcode-cn:677 键值映射 | 确定 |
| manacher-intro | Manacher 入门 | leetcode-cn:5 最长回文子串；leetcode-cn:647 回文子串；leetcode-cn:214 最短回文串 | 确定 |
| fast-pow-gcd | 快速幂与 GCD | leetcode-cn:50 Pow(x, n)；luogu:P1226 快速幂；leetcode-cn:1071 字符串的最大公因子 | 确定 |
| sieve-comb | 素数筛与组合数取模 | leetcode-cn:204 计数质数；leetcode-cn:62 不同路径；luogu:P3383 线性筛素数 | 确定 |
| bit-tricks | 位运算技巧（异或/popcount/lowbit） | leetcode-cn:136 只出现一次的数字；leetcode-cn:191 位1的个数；leetcode-cn:190 颠倒二进制位 | 确定 |
| fenwick-intro | 树状数组入门 | leetcode-cn:307 区域和检索 - 数组可修改；luogu:P3374 树状数组 1；luogu:P3368 树状数组 2 | 确定 |
| pitfalls-overflow | 实现陷阱：整数溢出/浮点/边界 | leetcode-cn:69 x 的平方根；leetcode-cn:50 Pow(x, n)；luogu:P1226 快速幂 | 确定 |

## 说明

- 本机（Windows）没有 g++，C++ 那一半在本地是 skip，CI（Linux）会真跑。
- 这一批没有改动已有的标题、分类、难度、信号、思路、模板正文，只新增了两个字段；
  模板本身没有发现跑不通的（样例输出就是模板的真实输出）。
