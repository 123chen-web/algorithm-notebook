# 批次 03 报告：回溯 / 搜索 / 图（8 个套路）

- 批次文件：`docs/pattern-batches/batch-03.json`（JSON 数组，8 项）
- 范本参照：`sliding-window`、`kmp`、`union-find` 的深度与语气（简体中文、信号具体、模板带中文注释、坑写真实错误）
- `related` 只引用了现有 41 个清单的 id 或本批次内的 id，未引用其他批次

## 例题清单与把握程度

全部 24 道例题均为**确定**（题号和题名对应关系是经典题，有把握），没有"凭记忆"的条目。

| id | 标题 | 例题 | 把握 |
| --- | --- | --- | --- |
| backtrack-nqueens | N 皇后 | luogu:P1219 八皇后；leetcode-cn:51 N 皇后；leetcode-cn:52 N 皇后 II | 确定 |
| backtrack-partition | 分割与括号生成 | leetcode-cn:22 括号生成；leetcode-cn:131 分割回文串；leetcode-cn:93 复原 IP 地址 | 确定 |
| backtrack-pruning | 回溯剪枝与去重 | leetcode-cn:46 全排列；leetcode-cn:47 全排列 II；leetcode-cn:40 组合总和 II | 确定 |
| memo-dfs | 记忆化搜索 | leetcode-cn:329 矩阵中的最长递增路径；leetcode-cn:62 不同路径；luogu:P1434 滑雪 | 确定 |
| bfs-multi-source | 多源 BFS | leetcode-cn:542 01 矩阵；leetcode-cn:1162 地图分析；luogu:P1332 血色先锋队 | 确定 |
| bfs-01 | 0-1 BFS | luogu:P4554 小明的游戏；leetcode-cn:1368 使网格图至少有一条有效路径的最小代价；leetcode-cn:2290 到达角落需要移除障碍物的最小数目 | 确定 |
| dfs-graph-cycle | 有向图判环（三色标记） | leetcode-cn:207 课程表；leetcode-cn:210 课程表 II；luogu:P2661 信息传递 | 确定 |
| mst-prim | Prim 最小生成树 | luogu:P3366 最小生成树；leetcode-cn:1584 连接所有点的最小费用；luogu:P2330 繁忙的都市 | 确定 |

## 每个套路 sample_input 的独立验算方法

| id | sample | 独立验算方法 |
| --- | --- | --- |
| backtrack-nqueens | `4` → `2` | 已知结论：4 皇后恰有 2 组解（(2,4,1,3)、(3,1,4,2)），手算列出验证 |
| backtrack-partition | `3` → 5 行括号串 | 卡特兰数 C(3)=5；手工追踪 DFS 生成顺序，确认输出行数与顺序 |
| backtrack-pruning | `4\n1 1 2 2` → `6` | 组合公式 4!/(2!2!)=6 |
| memo-dfs | 3×3 空网格 → `6` | 组合公式 C(4,2)=6（右/下各走 2 步） |
| bfs-multi-source | `1 5\n1 0 1 0 1` → `1 0 1 0 1` | 手算：源在位置 1、3，其余格到最近源的曼哈顿距离 |
| bfs-01 | 4 点 4 边 → `1` | 手算两条路径：1→2→4 耗 1，1→3→4 耗 1，取最小 |
| dfs-graph-cycle | 三角形有向边 → `YES` | 手算：1→2→3→1 构成环 |
| mst-prim | 4 点 5 边 → `6` | 用 Kruskal 交叉验证：选边 (1,2,1)、(2,3,2)、(3,4,3)，总和 6 |

样例答案均为先独立算出、再由自查脚本运行模板比对确认一致；不是把模板输出抄成样例。

## 自查命令最后一行输出

```
101 passed in 82.26s (0:01:22)
```

命令：`python tools/check_pattern_batch.py docs/pattern-batches/batch-03.json`
（用仓库 venv 的 python；合并当前 41 条 + 本批 8 条后跑全套校验：
结构、例题格式、related 引用、每个 Python 模板真实运行、本机 g++ -O2 -std=c++17
编译运行每个 C++ 模板；临时文件走系统临时目录，未改仓库文件）

## 没做到的和原因

- 无。8 个套路全部写满 3 道及以上例题，related 无悬空引用，自查脚本全部通过。
