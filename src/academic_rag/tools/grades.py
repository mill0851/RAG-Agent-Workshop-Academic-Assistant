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

from .. import course_files, index
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

    SOLUTION: choose the courses, then one block of lines per course from facts.json.
    """
    # SOLUTION
    # 1. the courses: the one named, else the chosen one, else all
    courses = course_files.list_courses(box.settings)
    if course:
        course_id, problem = index.resolve_course(courses, course)
        if problem:
            return problem  # the model can try again with a name from this list
        ids = [course_id]
    else:
        ids = [box.course_id] if box.course_id else [c["id"] for c in courses]

    # 2. each course's facts.json: the current grade, then every assignment that has a score
    blocks = []
    for course_id in ids:
        facts = course_files.get_facts(box.settings, course_id) or {}
        name = facts.get("course_name") or course_id
        grade = facts.get("grade") or {}
        if grade.get("current_score") is not None:
            letter = f" ({grade['current_grade']})" if grade.get("current_grade") else ""
            lines = [f"{name}: current score {grade['current_score']:g}{letter}"]
        else:
            lines = [f"{name}: no grade yet"]
        for a in facts.get("assignments", []):
            score = (a.get("submission") or {}).get("score")
            if score is not None:
                points = a.get("points_possible")
                lines.append(f"- {a['name']}: {score:g}" + (f" / {points:g}" if points is not None else ""))
        blocks.append("\n".join(lines))

    # 3. one block per course
    return "\n\n".join(blocks) or "There are no saved courses yet."


GRADES = Tool(
    name="grades",
    # SOLUTION: what it returns, when to call it (in the student's own words), and why nothing else will do
    description=("The student's own current grade and their score on each graded assignment, exactly as "
                 "Canvas has them. The course material never contains the student's scores: only this tool "
                 "does. Use it whenever they ask how they are doing, what their grade or score is, or how "
                 "many points they have lost. Not for due dates: those are in the deadline list."),
    input_schema={"type": "object", "properties": {
        "course": {"type": "string", "description": "A course name or code, e.g. ECE-5383. Leave it out "
                                                    "for the course the student chose."}}},
    run=grades,
)
