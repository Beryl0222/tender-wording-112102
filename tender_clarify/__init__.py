"""招标释疑核对。

把招标文件条款、双语问答和投标人已递交的技术选择放在同一轮释疑里核对：
澄清的签发范围、译文的效力、提问人私有信息的隔离、证明的适用范围、
受影响的递交以及通知与期限。

公开 API::

    from tender_clarify import load, review, render_text

    briefs, submissions, clock = load("data")
    result = review(briefs, submissions, clock)
    print(render_text(result))
"""

from .engine import ReviewResult, review
from .loader import Clock, DataError, load
from .render import render_json, render_text

__all__ = [
    "Clock",
    "DataError",
    "ReviewResult",
    "load",
    "render_json",
    "render_text",
    "review",
]
