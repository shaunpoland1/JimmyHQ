"""No pattern in the SDK or a first-party app that reads text from outside (a web page, a
model's answer, a paste, a file) takes time in proportion to the square of a long run of one
character. Each function is given the texts that make such a pattern start again at every
character, in a process of its own that is stopped if it does not finish, so a pattern that
never ends fails this test instead of hanging it."""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
APPS = sorted(p.parent for p in (ROOT / "apps").glob("*/jimmyhq.app.json"))
PATHS = [ROOT / "sdk" / "python", *APPS]
# An app that is not covered below says what to try in `tests/regex_safe.py`: a function
# `calls(call)` that makes `call(label, function)` for each function that reads outside text.
EXTRA = [p / "tests" / "regex_safe.py" for p in APPS if (p / "tests" / "regex_safe.py").is_file()]
N = 100_000
EACH = 1.5  # seconds one function may take on one text
WHOLE = 120  # seconds for all of them: a pattern that never ends is stopped here

CODE = '''
import sys, time
sys.path[:0] = %(paths)r
N = %(n)d
HOSTILE = {
    "open brackets": "[" * N, "images": "![" * (N // 2), "links": "[a](" * (N // 4),
    "emphasis": " *a" * (N // 3), "bold": "**a " * (N // 4), "underscores": "_a " * (N // 3),
    "blank lines": "\\n" * N, "blank lines of spaces": " \\n" * (N // 2), "spaces": " " * N,
    "no-break spaces": "\\u00a0" * N, "dots then a letter": "." * N + "x",
    "dots and spaces": ". " * (N // 2),
    "open quotes": "\\u201c" * N, "open single quotes": "\\u2018" * N, "commas": "," * N,
    "dashes": "- " * (N // 2), "bullets": "-" * N, "marks": "[S1: 1" * (N // 6),
    "marks with commas": "[S1: 1" + " , 1" * (N // 4), "angle brackets": "<" * N,
    "tags": "<p>" * (N // 3), "digits": "1" * N, "numbers": "1," * (N // 2) + "x",
    "at signs": "a@" * (N // 2), "hashes": "#a" * (N // 2), "backticks": "`a" * (N // 2),
    "photo marks": "[photo 1]" * (N // 9), "words": "word " * (N // 5),
    "ampersands": "&" * N, "entities": "&a" * (N // 2), "backslashes": "\\\\" * N,
    "lone underscores": "_" * N, "hours": "1" * 99 + "h" * (N // 2),
    "dates": "1/" * (N // 2) + "x", "years": "1" * N + "2000", "months": "may " * (N // 4),
    "day then month": "4 " * (N // 2) + "july 2019",
}
CALLS = []

def call(label, fn):
    CALLS.append((label, fn))

from jimmyhq_app import claims, text as sdk_text
call("sdk clean", sdk_text.clean)
call("sdk sentences", lambda s: sdk_text.split_sentences(sdk_text.clean(s)))
call("sdk quotes", claims.quoted)
call("sdk normalise", claims.normalise)
call("sdk numbers", claims.find_numbers)

from posts.checks import text as pt
from posts import voice, tools as ptools
call("posts strip_markdown", pt.strip_markdown)
call("posts remove_addresses", pt.remove_addresses)
call("posts sentence ends", lambda s: voice.SENTENCE_END.findall(s))
call("posts claim line", lambda s: ptools._CLAIM.match(s))

from articles import markup, replies, lessons, tools as atools
call("articles replies clean", lambda s: replies.clean(s, 4000))
call("articles replies split", replies.split)
call("articles remove_addresses", markup.remove_addresses)
call("articles strip_marks", markup.strip_marks)
call("articles parse_mark", lambda s: markup.parse_mark(s))
call("articles quoted", lambda s: lessons._QUOTED.findall(s))
call("articles claim tail", lambda s: atools._TAIL.match(s))

from heard import clean as hclean
call("heard line", lambda s: hclean.line(s, 5000))
call("heard block", lambda s: hclean.block(s, 5000))

import importlib.util

for extra in %(extra)s:
    spec = importlib.util.spec_from_file_location("regex_safe_" + extra.parent.parent.name, extra)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    module.calls(call)

slow = []
for label, fn in CALLS:
    for name, text in HOSTILE.items():
        start = time.perf_counter()
        try:
            fn(text)
        except Exception:
            pass  # a refusal of the text is fine: only the time is checked here
        took = time.perf_counter() - start
        if took > %(each)s:
            slow.append(f"{label} on {name}: {took:.1f}s")
print("\\n".join(slow))
sys.exit(1 if slow else 0)
'''


def test_no_function_that_reads_outside_text_takes_time_in_proportion_to_the_square_of_it():
    code = CODE % {"paths": [str(p) for p in PATHS], "n": N, "each": EACH,
                   "extra": "[" + ", ".join(f"__import__('pathlib').Path({str(p)!r})"
                                            for p in EXTRA) + "]"}
    try:
        done = subprocess.run([sys.executable, "-I", "-c", code], capture_output=True,
                              text=True, timeout=WHOLE, check=False)
    except subprocess.TimeoutExpired:
        raise AssertionError(
            f"a function took more than {WHOLE} seconds on hostile text") from None
    assert done.returncode == 0, done.stdout[-1500:] + done.stderr[-1500:]
