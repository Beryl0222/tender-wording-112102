# 建造招标释疑中的等效性与递交处置

构件招标的双语澄清来文、投标组合及证明目录。材料中的人、机构及经营记录是合成的。

每份资料为 UTF-8 JSON 数组。id 在同一份资料内唯一，原始文字按收到的形式保留。

- `data/briefs.json`。采购文件及释疑流。lot 标记发件人认为相关的标段，可为空。decision=issued 且有 issued_at 表示已签发。pair 从可追认的版本列对齐语言。
- `data/submissions.json`。投标人做的技术选择和证据目录。option 是不可拆成多个投标选择的组合；usable_for 是原证据提供方声明覆盖的对象。原截止和再确认期限由 tender_clock 给出。

使用 Python 3.11 标准库，无需安装软件包或连接外部服务。

以下命令分别读取一份原始资料、检查资料中的字段与关联。

```sh
python3 -m json.tool data/briefs.json
python3 -m unittest discover -s tests -v
```
