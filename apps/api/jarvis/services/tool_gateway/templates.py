"""Predefined command templates.

v1 passed model output to ``subprocess(shell=True)``. That is arbitrary chat-to-shell —
the exact R4 the policy engine exists to prevent — so it is gone (PLAN.md §16).

What replaces it: a fixed set of commands with typed parameters. The model may choose a
template and fill its slots; it can never compose a command string. The rendered command
is shown verbatim in the approval, because "run a predefined template" is not informed
consent and "run ``rm -rf ~/project``" is.
"""

from __future__ import annotations

import shlex
from dataclasses import dataclass


@dataclass(frozen=True)
class CommandTemplate:
    key: str
    description: str
    argv: tuple[str, ...]
    params: tuple[str, ...] = ()

    def render(self, params: dict[str, str]) -> list[str]:
        """Fill the slots. Values are substituted as **whole argv entries**, never
        concatenated into a string, so shell metacharacters have nothing to act on."""
        missing = [p for p in self.params if p not in params]
        if missing:
            raise ValueError(f"template {self.key} is missing parameter(s): {missing}")

        rendered: list[str] = []
        for part in self.argv:
            if part.startswith("{") and part.endswith("}"):
                name = part[1:-1]
                if name not in self.params:
                    raise ValueError(f"template {self.key} references unknown slot {name}")
                rendered.append(str(params[name]))
            else:
                rendered.append(part)
        return rendered

    def preview(self, params: dict[str, str]) -> str:
        """The exact command, quoted for display in the approval card."""
        return shlex.join(self.render(params))


COMMAND_TEMPLATES: dict[str, CommandTemplate] = {
    t.key: t
    for t in (
        CommandTemplate(
            "git.status", "Show working tree status of a project",
            argv=("git", "-C", "{path}", "status", "--short"), params=("path",),
        ),
        CommandTemplate(
            "git.pull", "Pull the current branch of a project",
            argv=("git", "-C", "{path}", "pull", "--ff-only"), params=("path",),
        ),
        CommandTemplate(
            "project.open_in_editor", "Open a project folder in VS Code",
            argv=("code", "{path}"), params=("path",),
        ),
        CommandTemplate(
            "tests.run", "Run the test suite of a project",
            argv=("make", "-C", "{path}", "test"), params=("path",),
        ),
    )
}
