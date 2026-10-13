"""Task 3: boost passages that contain the question's exact codes and numbers.

Run:  pytest tests/tasks/test_task3_exact_match.py
Guide: src/academic_rag/index/README.md
"""

from academic_rag.index import Passage, boost_exact_matches


def passage(title, text, score):
    return Passage(n=0, title=title, course="ML", course_id="101", type="page", location="",
                   module="", url="", path="", score=score, text=text)


def titles(passages):
    return [p.title for p in passages]


def test_the_passage_with_the_exact_code_moves_to_the_top():
    vague = passage("Homework policy", "Homework is due on Fridays and late work loses points.", 0.82)
    exact = passage("Schedule", "HW 2 is due October 20.", 0.61)
    other = passage("Notes", "Gradient descent follows the negative gradient.", 0.55)
    result = titles(boost_exact_matches("when is HW 2 due", [vague, exact, other]))
    assert result == ["Schedule", "Homework policy", "Notes"], (
        f"got {result}: the only passage that says 'HW 2' should come first, the rest in their old order")


def test_section_numbers_count_as_exact_terms():
    general = passage("Help Center hours", "Tutors can help with any homework question.", 0.36)
    exact = passage("Homework 11.3: The Integral Test", "Complete the online problems for Section 11.3.", 0.22)
    result = titles(boost_exact_matches("when is homework 11.3 due", [general, exact]))
    assert result[0] == "Homework 11.3: The Integral Test", (
        f"got {result}: '11.3' is a section number, so the passage containing it should win")


def test_course_codes_match_regardless_of_case():
    general = passage("Office hours", "Office hours are on Tuesdays.", 0.9)
    coded = passage("CHEM syllabus", "CHEM 2053 meets in Room 305.", 0.5)
    result = titles(boost_exact_matches("chem 2053 room", [general, coded]))
    assert result[0] == "CHEM syllabus", f"got {result}: compare in lowercase, so 'chem 2053' matches 'CHEM 2053'"


def test_numbers_must_match_whole_not_as_part_of_a_bigger_number():
    hw22 = passage("Homework 22", "HW 22 is due in December.", 0.9)
    hw2 = passage("Homework 2", "HW 2 is due in October.", 0.5)
    result = titles(boost_exact_matches("HW 2", [hw22, hw2]))
    assert result == ["Homework 2", "Homework 22"], (
        f"got {result}: 'HW 2' must not match inside 'HW 22' (use \\b word boundaries)")


def test_without_exact_terms_the_vector_order_is_kept():
    ps = [passage("A", "overfitting memorizes noise", 0.9), passage("B", "regularization helps", 0.7)]
    result = titles(boost_exact_matches("what is overfitting", ps))
    assert result == ["A", "B"], f"got {result}: a question with no codes or numbers should change nothing"


def test_ties_keep_the_vector_order_and_nothing_is_lost():
    ps = [passage(t, f"Quiz 1 covers topic {t}.", s) for t, s in (("A", 0.9), ("B", 0.8), ("C", 0.7))]
    ps.append(passage("D", "No quiz here.", 0.95))
    result = titles(boost_exact_matches("quiz 1", ps))
    assert len(result) == 4, f"got {result}: never drop a passage, only reorder"
    assert result == ["A", "B", "C", "D"], f"got {result}: equal matches keep their vector order (sorted() is stable)"
