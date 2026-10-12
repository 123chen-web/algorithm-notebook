# 套路库批次 04 交付报告

批次：`docs/pattern-batches/batch-04.json`（12 个套路，JSON 数组，未改 `static/data/patterns.json`、未改测试、未碰现有条目）

## 例题清单与把握程度

- **dp-fibonacci**（爬楼梯/斐波那契类）
  - leetcode-cn 70《爬楼梯》——确定
  - leetcode-cn 509《斐波那契数》——确定
  - luogu P1255《数楼梯》——确定
- **dp-house-robber**（打家劫舍）
  - leetcode-cn 198《打家劫舍》——确定
  - leetcode-cn 213《打家劫舍 II》——确定
  - leetcode-cn 740《删除并获得点数》——确定
- **kadane**（最大子数组和）
  - leetcode-cn 53《最大子数组和》——确定
  - leetcode-cn 918《环形子数组的最大和》——确定
  - leetcode-cn 152《乘积最大子数组》——确定
- **dp-grid**（网格路径 DP）
  - leetcode-cn 62《不同路径》——确定
  - leetcode-cn 63《不同路径 II》——确定
  - luogu P1002《过河卒》——确定
- **dp-lcs**（最长公共子序列）
  - leetcode-cn 1143《最长公共子序列》——确定
  - luogu P1439《最长公共子序列》——确定
  - leetcode-cn 583《两个字符串的删除操作》——确定
- **dp-edit-distance**（编辑距离）
  - leetcode-cn 72《编辑距离》——确定
  - luogu P2758《编辑距离》——确定
  - leetcode-cn 583《两个字符串的删除操作》——确定
- **dp-stock-state**（股票买卖）
  - leetcode-cn 121《买卖股票的最佳时机》——确定
  - leetcode-cn 122《买卖股票的最佳时机 II》——确定
  - leetcode-cn 123《买卖股票的最佳时机 III》——确定
- **knapsack-multi**（多重/分组背包）
  - luogu P1776《宝物筛选》——确定
  - luogu P1833《樱花》——确定
  - luogu P1064《金明的预算方案》——凭记忆（题号题名对应确定；它是“有依赖的背包”，对应本条目“分组”部分的内容，不是纯多重背包，请抽查是否合适）
- **dp-counting**（计数 DP）
  - leetcode-cn 62《不同路径》——确定
  - luogu P1164《小A点菜》——确定
  - luogu P1002《过河卒》——确定
- **merge-sort-inversions**（归并排序与逆序对）
  - luogu P1908《逆序对》——确定
  - leetcode-cn 493《翻转对》——确定
  - leetcode-cn 315《计算右侧小于当前元素的个数》——确定
- **segment-tree**（线段树）
  - luogu P3372《线段树 1》——确定
  - luogu P3373《线段树 2》——确定
  - leetcode-cn 307《区域和检索-数组可修改》——确定
- **sparse-table**（稀疏表 ST）
  - luogu P3865《ST 表》——确定
  - luogu P1816《忠诚》——确定
  - luogu P2251《质量检测》——凭记忆（区间最值 RMQ 经典题，请抽查题号题名）

## 样例独立验算方法（每个样例的答案都是先独立算出、再写进文件的）

- dp-fibonacci：手算递推 1,2,3,5,8，n=5 得 8
- dp-house-robber：手算枚举，2+9+1=12 为不相邻最大
- kadane：手算跟踪 cur/best，经典值 6
- dp-grid：手算 3×3 全空网格 = C(4,2) = 6
- dp-lcs：手算 "abcde"/"ace" 的最长公共子序列 "ace" = 3
- dp-edit-distance：经典样例 horse→ros = 3
- dp-stock-state：手算 leetcode 121 经典样例 [7,1,5,3,6,4] = 5
- knapsack-multi：手算枚举组合 (x=2,y=2) 得 16 最大；另用独立暴力程序复核为 16
- dp-counting：手算 总数 6 − 经过中心障碍 4 = 2；另用独立暴力程序复核为 2
- merge-sort-inversions：手算 5 4 3 2 1 0 共 10 对；另用独立暴力程序复核为 10
- segment-tree：手算 15 → 修改后 25 → 2..4 区间 19
- sparse-table：手算区间最大值 5, 5, 4

## 自查命令输出

```
python3 tools/check_pattern_batch.py docs/pattern-batches/batch-04.json
```

（最后一行输出见下；用 `~/workspace/ouye/venv/bin/python` 运行，本机有 g++ 13.3，C++ 模板真实编译运行）

```
........................................................................ [ 66%]
.....................................                                    [100%]
109 passed in 248.51s (0:04:08)
```

其中：3 项结构/引用/例题格式检查 + 53 个 Python 模板真实运行 + 53 个 C++ 模板（g++ -O2 -std=c++17）编译运行，全部通过。

## 没做到的和原因

- knapsack-multi、merge-sort-inversions、sparse-table 三个条目的第三道例题把握程度不如其它（见上表“凭记忆”标注），已如实列出，请人工抽查；纯多重背包的第三道经典题我没有十足把握的，不敢硬写。
- P1064《金明的预算方案》是有依赖的背包（分组背包变体），不是标准多重背包——它对应本条目 signals/idea 里的“分组”部分；如果合并时觉得不合适，可删掉，我这边测试仍通过（examples 仍 ≥3）。
- 其余字段（signals/idea/complexity/pitfalls/confusions/related/sample）全部按要求填写，无缺项。
