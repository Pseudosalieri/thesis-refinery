# thesis-refinery · 论文精修厂

**把已经写成的 Word 学位论文变成可复现的构建产物,并用断言而不是肉眼证明它没有走样。**

[English](README.md) · [更新日志](CHANGELOG.md) · [参与贡献](CONTRIBUTING.md) · 协议:AGPL-3.0-only

---

> **状态:0.1.0,预发布。** 本引擎正从一条真实产线中抽取——那条产线支撑了一篇经过约 20 轮
> 修订并最终送审通过的硕士学位论文。接口仍可能变动。已落地与待办见 [CHANGELOG.md](CHANGELOG.md)。

### 现在就能用的部分

```bash
thesis-refinery init my-thesis                # 脚手架出一个空白项目
thesis-refinery build examples/demo-thesis    # 渲染 + 施加版式 + 过闸门
thesis-refinery verify -C examples/demo-thesis
```

示例论文可以完整构建,并通过全部十七项检查(其中两项因示例未配置封面身份而跳过)。
样式施加阶段、闸门、台账、`init`、`diff` 与构建编排均已实现并有测试;`extract-prefix`
已声明但尚未实现。

| 组件 | 状态 |
|---|---|
| `docxlib` —— OOXML 原语、包级写时复制 | 完成 |
| `style.py` / `config.py` —— 唯一规格、严格配置 | 完成 |
| `verify/` —— 七项检查,每项都有负向测试 | 完成 |
| `ledger/` —— 哈希基线、清点、逐段比对 | 完成 |
| `stages/format.py` —— 施加规格 | 完成 |
| `examples/demo-thesis` —— 合成、可构建、**21 项检查 0 跳过** | 完成 |
| `stages/abbreviations.py` —— 从 markdown 源重建缩略语表 | 完成 |
| `stages/references.py` —— 著录条目按引用体例转换 | 完成 |
| `stages/frontmatter.py` —— 封面逐字节导入 | 完成 |
| `extract-prefix` —— 从既有文档中裁出封面 | 完成 |
| `profiles/chemistry` —— 题注 run 形态、软件报告项下标 | 可选扩展 |
| `profiles/chemistry/spectra.py` —— 谱图附录 | 已规划,未落地 |

这份清单对工具本身不构成意外:缺失的阶段会**按名字报出来**并中止构建;缺失的
profile 模块写在正文里说明,而不是挂在 `__all__` 上当作承诺。

## 要解决的问题

几乎所有学位论文都在 Word 里改,而几乎所有学位论文都要满足一套 Word 根本无法强制的格式规范。
于是就有了这套熟悉的失败模式:

- `定稿_v7_真的定稿了.docx` 成了唯一真源;
- 格式回退是被答辩委员发现的,不是被构建发现的;
- 没人能说清老师手里那份和仓库里那份是不是同一版;
- 每一次“修一下”都是一轮手工点击,无法验证,也无法重复。

模板解决不了这件事。模板帮你**开始**写论文;真正折磨人的是后半程——**你已经有一稿了,
现在要让它合规,而且不许悄悄压坏别的东西。**

## 这是什么,不是什么

| | |
|---|---|
| **是** | 面向“论文已经存在”这一前提的构建与校验工具链 |
| | 一份声明式格式规格,由脚本施加,再由**独立**断言复查 |
| | 文档一旦走样就非零退出的闸门 |
| **不是** | LaTeX / Quarto 论文**模板** |
| | 对任意文档都能“一键跑通”的 Word 转换器 |
| | 文献管理器 |

最接近的邻居是 Quarto 论文模板
([`quarto-thesis`](https://github.com/nmfs-opensci/quarto-thesis)、
[`quarto-monash/thesis`](https://github.com/quarto-monash/thesis)、
[`upfthesis`](https://github.com/gongcastro/upfthesis) 等)。从空白页开始时用它们;
盯着写完的初稿和一份四十页格式规范时,用这个。

## 流水线

正文以 markdown 为准;`.docx` 是**构建产物**,永远不是真源。

```
chapters/*.qmd ──► quarto render ──► index.docx
                                        │
                                        ├─ 前置页导入   (封面/声明/授权,逐字节保真)
                                        ├─ 可选 profile (谱图附录、能量项下标)
                                        ├─ 参考文献著录 (著录格式规范化)
                                        ├─ 版式施加     (页边距、分节、页眉页脚、三线表)
                                        └─ 终版式       (页眉下边线、页脚字号、摘要标题)
                                        │
                                        ▼
                                   index-final.docx ──► verify ──► PASS / 退出码 1
```

每一阶段都是**读一个文件、写一个新文件**。任何环节都不就地修改,所以一旦失败,
可疑输入只有一个。

## 快速开始

```bash
pip install -e ".[dev]"                  # 或者用 . ./tools/dev-env.sh

thesis-refinery init my-thesis           # 脚手架出一个空白项目
cd my-thesis
thesis-refinery build                    # 渲染 + 施加版式 + 过闸门
thesis-refinery audit                    # 同一套检查,人读报告
thesis-refinery verify                   # 同一套检查,非零退出
```

渲染步骤需要 Quarto。除此之外——版式施加、校验、台账——都只作用于你手上已有的 `.docx`,
所以你完全可以在一份已经定稿的文档上引入本工具,而不重新渲染任何东西。

## 三条承诺

### 1. markdown 是真源

内容改动进 `chapters/*.qmd`;版式改动进格式规格。作者本人用 Word 手改是**另一条正常通道**,
但它附带一项明确义务:把 Word 版本与仓库基线比对,并将改动回填 markdown。
未回填的 Word 改动就是一次静默分叉,本工具链按分叉处理。

### 2. 一份规格,两个独立读者

格式事实——页面几何、页边距、页眉文字、表格边框、题注样式、目录制表位——只存在于一处。
**施加**它们的代码与**检查**它们的代码读同一份规格。

而遍历代码**故意不共享**。施加器与校验器各自独立走一遍文档。若二者共用代码路径,
遍历里的 bug 会自我抵消,于是闸门会在一份坏文档上报绿。共享数据,绝不共享 `main()`。

### 3. 每一条断言都必须有能力失败

不会失败的校验器只是装饰。`verify/` 里的每一项检查都配一个**故意把文档改坏**的负向测试,
断言闸门确实变红。永远退出 0 的格式报告不是闸门,本项目不提供那种东西。

## 配置

每个项目一份 `thesis.yml`,描述目录布局、封面必须出现与不得出现的身份串、版式取值与期望计数。

```yaml
project:
  thesis_dir: manuscript
  chapters_dir: manuscript/chapters
  output_dir: manuscript/_outputs

identity:
  required: []      # 封面必须包含的字符串
  forbidden: []     # 封面不得出现的字符串

style:
  page:   { width_mm: 210, height_mm: 297 }
  margins: { top_mm: 25, bottom_mm: 25, body_side_mm: 20 }

checks:
  expect_captions: auto        # 从源推导,而不是写死
  expect_body_paragraphs: auto
```

期望计数支持 `auto`,即从输入文档推导。写死的计数是会悄悄腐烂的地雷;
确实必须钉死的数字,钉在一处,并在构建日志里报出来。

## 领域 profile

领域相关的步骤是可选的:

```bash
pip install -e ".[chemistry]"
```

```yaml
profiles: [chemistry]
```

`chemistry` profile 增加:谱图附录(化合物 × 谱型矩阵、题注模板化、横向扫描图旋转 180°),
以及 `ΔG_Bind` 这类软件报告项的下标格式化。它的行为完全由你填写的清单驱动——
工作表名、列名、文件名模板、题注模板——代码里不预置任何化合物命名体系。

## 目录结构

| 路径 | 内容 |
|---|---|
| `src/thesis_refinery/docxlib/` | 共享 OOXML 原语:命名空间、XML 查询、包手术、图形、表格、域、安全保存 |
| `src/thesis_refinery/style.py` | 唯一的格式规格 |
| `src/thesis_refinery/stages/` | 流水线各阶段,一阶段一文件 |
| `src/thesis_refinery/verify/` | 闸门(`gate.py`)、人读报告(`report.py`)、各项检查 |
| `src/thesis_refinery/ledger/` | 台账:清点、哈希清单、图表映射、版本比对 |
| `src/thesis_refinery/profiles/` | 可选领域 profile |
| `examples/demo-thesis/` | 一份完整合成的示例论文,覆盖全部阶段 |
| `tools/` | 示例素材生成脚本,以及脱敏闸门 |
| `docs/` | 设计说明、规则、第三方策略、发布检查清单 |

## 环境要求

- Python ≥ 3.10
- Quarto(仅渲染步骤需要;pandoc 随它自带)
- 不需要 Git LFS,不需要 Word 自动化,不需要 COM。支持 Windows,但不依赖 Windows。

## 文档

- [`docs/design-notes.md`](docs/design-notes.md) —— 流水线为何长成这样
- [`docs/rules/`](docs/rules/) —— 工作规则(markdown 真源、改写先拷贝、记号法)
- [`docs/third-party.md`](docs/third-party.md) —— 哪些东西**故意不打包**,以及原因
- [`docs/PUBLISH-CHECKLIST.md`](docs/PUBLISH-CHECKLIST.md) —— 维护者发布前必须完成的动作

## 协议

AGPL-3.0-only,全文见 [LICENSE](LICENSE)。

关于网络条款:如果你把修改后的本程序作为网络服务运行,必须向使用者提供对应源码。
在本机用它构建你自己的论文不会触发任何义务。

本项目不打包任何第三方资产。真实学校要求的格式规范是有版权的文件,由你自己提供——
见 [`docs/third-party.md`](docs/third-party.md)。

## 参与贡献

见 [CONTRIBUTING.md](CONTRIBUTING.md)。简短版:任何新检查都必须配负向测试;
任何新的格式事实都必须进 `style.py`,不许内联。
