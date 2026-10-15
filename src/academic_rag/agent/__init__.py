"""The answering agent behind the Ask page: agent.py runs a turn, context.py gathers the
seed, prompts.py puts together everything the model is told (its rules are in
system_prompt.md). See README.md in this folder."""

from .agent import ask
from .context import Context, gather, small_to_big
from .prompts import SYSTEM_FILE, build_prompt, system_prompt
