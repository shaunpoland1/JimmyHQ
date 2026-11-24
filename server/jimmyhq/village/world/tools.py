"""The tool names the body treats specially.

The world does not run tools. It only needs to know which names make something, which
act on the browser, and which always leave the same result. The runtime that gives
agents their tools must use these names, or change them here.
"""

from __future__ import annotations

# A successful one of these is a real change to a file: a small win.
MAKING_TOOLS = {"write_file", "add_to_file", "edit_file", "files.write", "files.append", "type"}

# Tools that act on the browser. A run of fruitless ones (failed, refused, or nothing
# new) means the page is a wall, and the body then refuses to keep pushing on it. Without
# this an agent can spend dozens of beats clicking at one page that will never respond.
# Leaving, going back, switching tabs or scrolling stays allowed.
# The two web tools count here too. Reading is what can hit a wall (a site that refuses,
# a page with nothing new, an address that is not allowed); searching is the way round
# it, as opening another page is, so a search that finds something clears the wall.
DESKTOP_TOOLS = {"open_page", "click", "type_text", "press_key", "scroll", "go_back",
                 "switch_tab", "close_tab", "web_read", "web_search"}
WALL_EXEMPT = {"open_page", "go_back", "switch_tab", "close_tab", "scroll", "web_search"}

# Tools that change the state and always leave the same result. Closing the last tab
# always ends on a blank screen, so a result guard would read every close as "same
# action, nothing new" and charge frustration for ordinary tidying up. The before-state
# guard still refuses closing an already-blank screen twice.
SAME_RESULT_TOOLS = {"close_tab"}
