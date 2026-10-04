# 分享卡片用的中文字体

`share_card.py` 渲染成就分享卡片时需要中文字体。系统字体不可靠（Linux 容器里一般没有，Windows 的微软雅黑等又不能随仓库分发），
所以仓库自带一份**裁剪过的开源字体**，放在最前面优先使用。

| 文件 | 说明 |
|---|---|
| `NotoSansSC-Regular-subset.otf` | Noto Sans CJK SC Regular 的子集，约 1.6 MB |
| `OFL.txt` | 字体许可证：SIL Open Font License 1.1（允许自由使用、嵌入和再分发，修改版不得使用保留字体名） |
| `make_subset.py` | 生成子集的脚本，方便以后重做 |
| `make_intro_glyphs.py` | 从子集里提取“欧叶OY”的字形轮廓，生成开场短片用的 `static/intro-glyphs.js`（只含路径数据，约 2 KB） |

## 来源与内容

- 原字体：Noto Sans CJK SC Regular（Google / Adobe，SIL OFL 1.1），下载自
  <https://github.com/notofonts/noto-cjk/tree/main/Sans/OTF/SimplifiedChinese>，许可证文本来自同仓库 `Sans/LICENSE`。
- 子集内容：ASCII、Latin-1 补充、常见标点和全角字符、**GB2312 全部 6763 个汉字及符号**。
  GB2312 之外的生僻字（例如少数用户名里的字）会显示为方框；去掉了字体提示（hinting）和竖排等排版特性以减小体积。
- 重新生成：下载上面的原始 OTF 放在 `make_subset.py` 旁边，`pip install fonttools`，然后
  `python make_subset.py NotoSansSC-Regular-subset.otf`。

## 开场短片的字形轮廓

开场短片最后的品牌幕会把“欧叶OY”一笔笔描出来。轮廓取自上面的子集字体：`pip install fonttools` 后运行
`python make_intro_glyphs.py`，会重写 `static/intro-glyphs.js`。网页只加载这几条路径数据，不加载字体文件；
轮廓仍属于 SIL OFL 1.1 授权的字体软件，许可证见 `OFL.txt`。
