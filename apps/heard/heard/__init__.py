"""Heard: what a wearable recorder transcribed, kept as searchable conversations.

The recorder (or its app) posts text to JimmyHQ's inbound door; ``intake`` reads the three
shapes it knows (``shapes``), stitches live pieces into conversations and keeps them in the
app's own private store (``store``). Audio is never taken: a message's words are kept and
every other field is ignored, so an audio address in a message is never even read. The app
is private: it writes nothing to the owner's record, and a model reads a conversation only
when the owner asks, on the owner's own machine (``ask``).
"""
