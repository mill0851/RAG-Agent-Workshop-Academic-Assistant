"""What a tool is: a function, plus the words a model reads to decide when to call it.

    name          what the model writes to call it, e.g. "search_course"
    description   when to use it and what it returns: the model decides from this alone
    input_schema  its arguments, as JSON Schema (the format every AI provider accepts)
    run           run(box, **arguments) -> text for the model, where box is the Toolbox
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass


@dataclass
class Tool:
    """One tool the model can call. The model never sees `run`: only the other three."""

    name: str
    description: str
    input_schema: dict
    run: Callable[..., str]

    def spec(self) -> dict:
        """What is sent to the model: everything except the function itself."""
        return {"name": self.name, "description": self.description, "input_schema": self.input_schema}
