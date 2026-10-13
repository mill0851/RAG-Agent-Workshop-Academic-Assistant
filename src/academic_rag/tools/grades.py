"""A tool that reports the student's grades.  TASK 2 (see tools/README.md).

The scraper already saved the exact numbers in each course's facts.json: the current
score and grade, and each assignment's score and points. This tool turns them into a
few lines the model can read. Three steps make it a tool every AI can call:

    1. write grades() below
    2. write GRADES.description: the model decides from it alone when to call the tool
    3. add GRADES to TOOLS in toolbox.py
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from .tool import Tool

if TYPE_CHECKING:
    from .toolbox import Toolbox


def grades(box: Toolbox, course: str = "") -> str:
    """The student's current grade and scored assignments, as short lines of text:

        ECE 5383 Machine Learning: current score 91.5 (A-)
        - HW1: 95 / 100
        - HW2: 88 / 100

    `course` is a course name or code ("ECE-5383"); when it is empty, use the course
    the student chose (box.course_id), or every course when none is chosen.
    """
    # TODO 1: choose the courses: the one `course` names (index.resolve_course with
    #         course_files.list_courses(box.settings)), else box.course_id, else all of them
    # TODO 2: for each, read facts.json with course_files.get_facts(box.settings, course_id):
    #         facts["grade"] has current_score and current_grade; each entry of
    #         facts["assignments"] has name, points_possible and submission["score"]
    # TODO 3: return the lines, or a short sentence when there is nothing to report
    raise NotImplementedError("Task 2 is not built yet: see src/academic_rag/tools/README.md")


GRADES = Tool(
    name="grades",
    description="TODO: what this tool returns, and when the model should call it.",
    input_schema={"type": "object", "properties": {
        "course": {"type": "string", "description": "A course name or code, e.g. ECE-5383. Leave it out "
                                                    "for the course the student chose."}}},
    run=grades,
)
