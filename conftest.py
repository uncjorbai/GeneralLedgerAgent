# Empty on purpose. Its mere presence at the repo root tells pytest "this is the
# project root" and puts this directory on sys.path, so tests can do
# `from agent.registry import ...`. Without it, pytest can't find the `agent`
# package and imports fail. (A classic first-time pytest gotcha.)
