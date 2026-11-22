"""命令行入口：python3 -m tender_clarify [--json] [--data DIR]"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from . import load, render_json, render_text, review


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="tender_clarify",
        description="把招标文件、双语问答和投标递交放在同一轮释疑里核对。",
    )
    parser.add_argument(
        "--data",
        default=None,
        help="资料目录（含 briefs.json 与 submissions.json），默认为当前目录下的 data",
    )
    parser.add_argument("--json", action="store_true", help="输出 JSON")
    args = parser.parse_args(argv)

    data_dir = Path(args.data) if args.data else Path(__file__).resolve().parents[1] / "data"
    briefs, submissions, clock = load(data_dir)
    result = review(briefs, submissions, clock)
    print(render_json(result) if args.json else render_text(result))
    return 0


if __name__ == "__main__":
    sys.exit(main())
