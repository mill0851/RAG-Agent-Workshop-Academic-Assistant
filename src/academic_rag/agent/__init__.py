"""The answering agent behind the Ask page. Start reading at agent.ask(): it gathers the
seed (context.py), puts together everything the model is told (prompts.py, with its
rules in system_prompt.md), and lets the model answer with tools. See README.md here."""

from .agent import ask
from .context import Context, gather, small_to_big
from .prompts import SYSTEM_FILE, build_prompt, system_prompt
