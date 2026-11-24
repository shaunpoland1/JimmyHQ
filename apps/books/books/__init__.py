"""Books: personal bookkeeping. Accounts, a register with running balances, transfers,
bills and income reminders, reconciling against a statement, and spending by category.

The app holds the books; the code keeps the guarantees (money is whole cents, a balance
is always worked out afresh, a transfer's two sides move together, a reminder is posted
once for each due date, a reconcile finishes only at a difference of zero, every change
that touches money is one transaction); the owner decides. The app is marked private: it
writes nothing on the owner's record, asks no model and uses no network. A Bench card for
a bill says its name and due date only. See ``program.build``.
"""
