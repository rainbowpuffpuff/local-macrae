#!/bin/sh
# The backend container has no Docker: agent steps run on Modal (`environment: modal`). This stand-in makes any
# leftover `docker image inspect` / `docker build` call fail cleanly (exit 1) instead of crashing with
# "No such file or directory".
echo "docker is not available in the macrae backend container (agent steps run on Modal)" >&2
exit 1
