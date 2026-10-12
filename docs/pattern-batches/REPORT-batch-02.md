# 套路库批次 02 报告（共 10 个：堆×3、栈×1、链表×1、树×5）

## 例题清单与把握程度

### heap-topk — 堆与 Top K — 堆 — 入门
- leetcode-cn 215 数组中的第K个最大元素 — 确定
- leetcode-cn 347 前 K 个高频元素 — 确定
- leetcode-cn 973 最接近原点的 K 个点 — 确定

### k-way-merge — 合并 K 个有序序列 — 堆 — 进阶
- leetcode-cn 23 合并 K 个升序链表 — 确定
- leetcode-cn 378 有序矩阵中第 K 小的元素 — 确定
- leetcode-cn 264 丑数 II — 确定
- luogu P1631 序列合并 — 凭记忆（清楚记得是“序列合并”，但为保险起见标出，请抽查）

### median-two-heaps — 双堆维护中位数 — 堆 — 进阶
- leetcode-cn 295 数据流的中位数 — 确定
- leetcode-cn 480 滑动窗口中位数 — 确定
- luogu P1168 中位数 — 确定
- luogu P1801 黑匣子 — 凭记忆（清楚记得是 Black Box 动态第 K 小，但标出请抽查）

### expression-stack — 表达式求值（逆波兰/双栈） — 栈 — 入门
- leetcode-cn 224 基本计算器 — 确定
- leetcode-cn 227 基本计算器 II — 确定
- leetcode-cn 150 逆波兰表达式求值 — 确定
- luogu P1981 表达式求值 — 凭记忆（记得是表达式求值题，标出请抽查）

### linked-list-merge — 链表合并与排序 — 链表 — 入门
- leetcode-cn 21 合并两个有序链表 — 确定
- leetcode-cn 148 排序链表 — 确定
- leetcode-cn 23 合并 K 个升序链表 — 确定

### tree-level-order — 层序遍历 — 树 — 入门
- leetcode-cn 102 二叉树的层序遍历 — 确定
- leetcode-cn 107 二叉树的层序遍历 II — 确定
- leetcode-cn 199 二叉树的右视图 — 确定

### bst-basics — 二叉搜索树性质 — 树 — 入门
- leetcode-cn 98 验证二叉搜索树 — 确定
- leetcode-cn 108 将有序数组转换为二叉搜索树 — 确定
- leetcode-cn 701 二叉搜索树中的插入操作 — 确定

### lca-tree — 最近公共祖先（倍增） — 树 — 进阶
- leetcode-cn 236 二叉树的最近公共祖先 — 确定
- leetcode-cn 1650 二叉树的最近公共祖先 III — 确定
- luogu P3379 最近公共祖先（LCA) — 确定

### tree-diameter — 树的直径 — 树 — 进阶
- leetcode-cn 543 二叉树的直径 — 确定
- leetcode-cn 1245 树的直径 — 确定
- leetcode-cn 1522 N 叉树的直径 — 确定

### tree-construct — 前序+中序建树 — 树 — 进阶
- leetcode-cn 105 从前序与中序遍历序列构造二叉树 — 确定
- leetcode-cn 106 从中序与后序遍历序列构造二叉树 — 确定
- leetcode-cn 889 根据前序和后序遍历构造二叉树 — 确定

## 样例独立验算方法（每个套路一句话）

- heap-topk：手算——8 个数降序排是 9,8,7,5,4,3,2,1，前 3 个是 9 8 7。
- k-way-merge：手算——三个有序序列 [1,4,7]、[2,5,8]、[3,6,9] 合并后是 1..9 升序。
- median-two-heaps：手算——插入流 5,1,9,3 后各前缀的下中位数依次为 5、1、5、3（前缀排序：[5]、[1,5]、[1,5,9]、[1,3,5,9]）。
- expression-stack：手算——3+(2*4-1)/3 = 3+(8-1)/3 = 3+7/3 = 3+2 = 5（向零取整）。
- linked-list-merge：手算——合并 [1,3,5] 与 [2,4] 得 1 2 3 4 5。
- tree-level-order：手算——层序数组 [3,9,20,-1,-1,15,7] 的 BFS 顺序是 3,9,20,15,7。
- bst-basics：手算——层序 [5,3,8,2,4,6,9] 逐节点验左<根<右成立，输出 YES。
- lca-tree：手算——树 1-2、1-3、2-4、2-5 中，LCA(4,5)=2（同父）、LCA(4,3)=1（跨子树）。
- tree-diameter：手算——链 1-2-3-4-5 的最长路是 1 到 5，共 4 条边。
- tree-construct：手算——前序 [3,9,20,15,7]、中序 [9,3,15,20,7]，根 3 左子树 [9] 右子树 [20,15,7]（根 20，左右 15/7），后序为 9,15,7,20,3。

另外所有样例都经 `tools/check_pattern_batch.py` 真实运行双模板比对通过（见下）。

## 自查命令输出

```
........................................................................ [ 68%]
.................................                                        [100%]
105 passed in 122.52s (0:02:02)
```

注：第一次跑时 9 个 Python 模板因缩进混用（1 空格/4 空格）报 IndentationError，
已用生成脚本重写全部 10 个 template_python（真实换行+4 空格缩进）并复跑，
第二次 105 passed 全绿。C++ 模板本机 g++ -O2 -std=c++17 真实编译运行通过。

## 没做到的和原因

- 本批 10 个套路例题全部满足 ≥3 道；其中 3 道标为“凭记忆”（P1631 序列合并、P1801 黑匣子、P1981 表达式求值），请抽查，如不准请删掉或替换。
- linked-list-merge 的 Python 模板是真实链表实现（ListNode + 哨兵合并），C++ 模板同样用 Node 结构体；输入约定为“数组表示链表”（N + N 个数，M + M 个数），已在 idea 和 pitfalls 里说明。
- expression-stack 不支持一元负号（如 -3+5），已在 pitfalls 里明确说明并给出替代写法（转成 (0-3)）。
- median-two-heaps 偶数个时取下中位数（P1168 的约定），已在 idea 里说明；如需平均值版本可改 median。
- tree-diameter 模板为无权树（边长为 1），输出边数；带权树需累加边权，已在 pitfalls 里说明。
- 未改 static/data/patterns.json、未改测试、未碰其他条目；未在项目目录建临时目录（校验脚本自带系统临时目录）。
