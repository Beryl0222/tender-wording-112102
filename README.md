# 建造招标释疑中的等效性与递交处置

构件招标的双语澄清来文、投标组合及证明目录。材料中的人、机构及经营记录是合成的。

每份资料为 UTF-8 JSON 数组。id 在同一份资料内唯一，原始文字按收到的形式保留。

- `data/briefs.json`。采购文件及释疑流。lot 标记发件人认为相关的标段，可为空。decision=issued 且有 issued_at 表示已签发。pair 从可追认的版本列对齐语言。
- `data/submissions.json`。投标人做的技术选择和证据目录。option 是不可拆成多个投标选择的组合；usable_for 是原证据提供方声明覆盖的对象。原截止和再确认期限由 tender_clock 给出。

使用 Python 3.11 标准库，无需安装软件包或连接外部服务。

以下命令分别读取一份原始资料、检查资料中的字段与关联、跑完整一轮释疑核对。

```sh
python3 -m json.tool data/briefs.json
python3 -m unittest discover -s tests -v
python3 -m tender_clarify            # 中文报告
python3 -m tender_clarify --json     # 同一结果的 JSON
```

## 核对做的事

`review(briefs, submissions, clock)` 返回一个 `ReviewResult`，把下列内容放在同一轮里：

- **提问归类**：区分「某材料/证明能否替换」与「送到现场的整套构件能否换检验方式」，
  按对象（联合装配组合 / 单块拼件）与检验方式把答复落到正确条款。
- **澄清效力**：只有 `decision=issued` 且有 `issued_at` 的澄清生效；效力不超出原文
  明确指向的标段，`某区无变化` 会排除该区；普通回复、旧稿不生效；同一问题的更正版
  只在其指向的标段和条款上取代旧文。
- **译文核对**：原文与译文在数值、量纲或要求强弱上冲突的条款留在待签
  （`pending_clauses`），能独立解释的条款照常发布（`publishable_clauses`）；
  译文不能借标段或措辞自行扩大效力。
- **公开释义与私有信息**：释义保留量纲与约束（成对、联合装配、原件、4.2 等），
  不引用原提问、不泄露供应商名或尺寸；只有不公开追问才补得齐的提问暂不公开发布。
- **递交复核**：按已公开释义与原先声明的构件组合逐项核对证明的 `usable_for`
  是否足够；同一证明用在两处时只有覆盖足够的那处继续有效；共用不可拆证明的
  候选构件按整组重新确认；只在公开变更确实影响时发补证/重新确认/先询问，
  没受影响的递交不整份退回。
- **通知与期限**：每则通知带共同的答疑依据（`answers`、`basis`）与再确认期限；
  按标段汇总可继续评审的递交。
- **截止后的译反**：截止后收到且把两种检验方式译反的文本会被标出，受影响的
  方案暂缓评审至统一补正，无关标段仍按原截止记录。

## 代码结构

| 文件 | 职责 |
| --- | --- |
| `tender_clarify/loader.py` | 读取并校验两份 JSON，构造 `Clock` |
| `tender_clarify/text.py` | 条款号、对象、检验方式、更正/不变量的微句解析 |
| `tender_clarify/rules.py` | 澄清效力、标段范围、译文冲突与待签条款 |
| `tender_clarify/public.py` | 提问归类、公开释义、私有信息隔离 |
| `tender_clarify/engine.py` | 递交复核、通知、期限、截止后译反 |
| `tender_clarify/render.py` | 中文报告与 JSON |
