"""Imagery: pictures made from a description, kept in a gallery you can search.

The app holds the pictures; an image service (``providers``) draws each one; the code keeps
the guarantees (a daily limit counted before anything is sent, storage limits that delete
only unstarred pictures and say so, a key that never appears in a sentence, a log or a
file); the owner decides which service and what it may cost. See ``program.build``.
"""
