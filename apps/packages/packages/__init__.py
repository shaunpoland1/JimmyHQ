"""Packages: parcels in one place, each with a status and a last event, checked on a
schedule, with a card on the Bench when one arrives or runs into trouble.

The app holds the packages; a tracking service (``trackers``) says where each one is; the
code keeps the guarantees (a number is registered once, a failed check changes nothing
and is tried again soon, a change is told once); the owner decides. The app is marked
private: it writes nothing on the owner's record, and keeps tracking numbers only in its
own store. See ``program.build``.
"""
