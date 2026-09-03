"""The domain the framework's own tests are written against.

Owned by the tests, and by nothing else. It exists to exercise the framework - every shape
worth exercising, including a few no application would want - so it is free to grow ugly in
the service of coverage.

The examples are a separate thing with a separate job: showing someone what good code with
this framework looks like. If the tests imported them, every change made for the sake of
coverage would land in the documentation, and every change made for the sake of clarity would
break the suite. They do not import each other, and `test_structure.py` keeps it that way.
"""
