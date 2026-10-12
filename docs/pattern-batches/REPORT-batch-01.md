# 批次 01 交付报告（REPORT-batch-01）

批次文件：`docs/pattern-batches/batch-01.json`（9 个套路，JSON 数组）
基线：`origin/main @ 068e73f`，工作目录 `~/workspace/ouye/wt-patterns-batches`
范本：照 `sliding-window`、`kmp`、`union-find` 的深度和语气撰写；未改 `patterns.json`、未改测试。

## 1. 本批套路与例题把握程度

### sort-custom — 排序与自定义比较（数组 / 入门）
- luogu P1177「快速排序」——确定
- luogu P1923「求第 k 小的数」——确定
- leetcode-cn 56「合并区间」——确定
- leetcode-cn 215「数组中的第K个最大元素」——确定

### matrix-spiral — 矩阵模拟（螺旋/旋转）（数组 / 入门）
- leetcode-cn 54「螺旋矩阵」——确定
- leetcode-cn 48「旋转图像」——确定
- leetcode-cn 59「螺旋矩阵 II」——确定

### dutch-flag — 荷兰国旗（三路划分）（双指针 / 入门）
- leetcode-cn 75「颜色分类」——确定
- luogu P1923「求第 k 小的数」——凭记忆（快速选择的三路划分变体，与荷兰国旗同源，但不是纯三色分类题）
- leetcode-cn 215「数组中的第K个最大元素」——凭记忆（快速选择的稳健实现常用三路划分）
- 说明：除 75 外，我没有完全把握的纯荷兰国旗题。按“凭记忆宁可删掉”的原则，这两道请抽查，不合适就删掉换你补的。

### remove-inplace — 原地删除/去重（快慢指针）（双指针 / 入门）
- leetcode-cn 26「删除有序数组中的重复项」——确定
- leetcode-cn 27「移除元素」——确定
- leetcode-cn 80「删除有序数组中的重复项 II」——确定

### prefix-sum-2d — 二维前缀和（前缀和与差分 / 入门）
- luogu P1719「最大加权矩形」——确定
- luogu P2280「激光炸弹」——确定
- leetcode-cn 304「二维区域和检索 - 矩阵不可变」——确定

### binary-search-rotated — 旋转数组二分（二分 / 入门）
- leetcode-cn 33「搜索旋转排序数组」——确定
- leetcode-cn 81「搜索旋转排序数组 II」——确定
- leetcode-cn 153「寻找旋转排序数组中的最小值」——确定

### binary-search-real — 实数二分（二分 / 进阶）
- luogu P1024「一元三次方程求解」——确定
- luogu P1163「银行贷款」——确定
- luogu P1577「切绳子」——凭记忆（记得是实数二分答案的切绳子题，题名若有出入请纠正）

### discretization — 离散化（数据结构 / 入门）
- luogu P1496「火烧赤壁」——确定
- luogu P1955「程序自动分析」——确定
- luogu P1908「逆序对」——凭记忆（题号题名有把握；离散化+树状数组是解逆序对的标准做法之一，但它更常被归到树状数组套路下）

### palindrome-expand — 回文中心扩展（字符串 / 入门）
- leetcode-cn 5「最长回文子串」——确定
- leetcode-cn 647「回文子串」——确定
- luogu P1210「回文质数」——凭记忆（用中心扩展生成回文数是自然思路，但不确定是否为该题的主流解法）

## 2. 每个样例的独立验算方法（一句话）

- sort-custom：手算——4 个二元组按 (b,a) 逐项比较，得出 (2,1),(4,1),(3,2),(1,3)。
- matrix-spiral：手算——3×3 矩阵按上→右→下→左→中心走一圈，得 1 2 3 6 9 8 7 4 5。
- dutch-flag：手算——逐步模拟 lt/i/gt 三指针的交换过程（i=0 处换 2 到末尾等），得 0 0 0 1 1 1 2 2 2。
- remove-inplace：手算——快慢指针扫描有序数组，去重结果显然为 0 1 2 3 4。
- prefix-sum-2d：手算——两个矩形分别累加得 12 和 28，再与容斥公式的四项对照。
- binary-search-rotated：手算——逐步模拟 lo/hi/mid（mid=3 处判左半有序），target=0 落在下标 4。
- binary-search-real：等价公式/已知常数——sqrt(2)=1.41421356…，保留 4 位小数为 1.4142。
- discretization：手算——排序去重得 [10,20,30,40]，逐个查排名得 4 1 4 2 1 3。
- palindrome-expand：手算——枚举全部中心，"babad" 的最长回文为 "bab"（与 "aba" 等长，模板取第一个出现的）。

样例答案均为先手算得出、再让模板运行比对，没有把模板输出抄成答案。

## 3. 自查命令最后一行输出

命令：`python3 tools/check_pattern_batch.py docs/pattern-batches/batch-01.json`
（用项目 venv 的 python3 跑；脚本把本批合并进 41 条正式清单后，用同一套
`tests/test_patterns_data.py` 全量校验：结构/例题格式/related 引用 +
每个 Python 模板真实运行 + g++ -O2 -std=c++17 编译运行每个 C++ 模板）

```
........................................................................ [ 69%]
...............................                                          [100%]
103 passed in 256.60s (0:04:16)
```

103 passed，0 failed。

## 4. 没做到的和原因

- dutch-flag、binary-search-real、discretization、palindrome-expand 各有 1–2 道“凭记忆”例题：测试要求每套路 ≥3 道例题，不敢直接删，请抽查后决定保留/替换/删除（dutch-flag 的两道若删掉，该条只剩 1 道，需你补）。
- related 引用限制：自查脚本只允许引用“现有 41 个或本批 id”。prefix-sum-2d 原本 related 有 kadane、discretization 原本 related 有 segment-tree（语义上都相关），因它们属于其他批次被迫移除；你合并全部批次后可加回。
- 未联网抓取任何题面；例题只放题号和题名。
- 仓库内未建临时目录/缓存；生成脚本放在 /tmp（`/tmp/gen_batch01.py`），自查脚本的临时文件走系统临时目录。
