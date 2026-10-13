# Eval questions

Copy this file to `questions.md` in this folder (it is git-ignored: it is about your courses), then
replace the examples with about ten questions whose answers you know. `acad eval` asks each one
through the running server, exactly as the Ask page does, and scores the answer.

Each question is a `##` heading, followed by any of these lines:

- `course:` the course id, or part of its name (leave it out for all courses)
- `source:` the title of a document the agent should find, in its first search or with a tool;
  repeat the line if several documents count
- `must mention:` a fact the answer must contain; separate accepted spellings with `|`, and repeat
  the line for each fact
- `must not mention:` something the answer must not contain (a wrong date, a guessed room)

Good questions to include: a date, a policy, a broad question that needs several documents, an exact
assignment number, a deadline, a subject question your course barely covers, something the course
does not say at all, and a behavior you asked for in the system prompt (Task 1).

## When is Homework 3 due?
course: Physics I
source: Problem Set 3
must mention: October 9 | Oct 9 | 10/9

## What is the late policy for homework?
course: Physics I
source: Syllabus
must mention: 10%
must mention: per day

## Explain the work-energy theorem, with an example from our course.
course: Physics I
source: Lecture 09 - Work and Energy.pdf
must mention: kinetic energy
must mention: Check yourself
