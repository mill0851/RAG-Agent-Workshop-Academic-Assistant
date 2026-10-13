# course_files/: documents become text

> **You don't need this folder for the tasks.** Read it to see how a PDF becomes something a model
> can search. (Task 2 calls two of its functions, `list_courses` and `get_facts`, which its hints
> show.)

A language model cannot read a PDF. Before anything can be searched, every page, slide, assignment
and announcement has to become plain text, and how well that goes limits how good every answer can
be. This folder does that, and defines the folder layout the rest of the system relies on.

| File | What it does |
|---|---|
| [extract.py](extract.py) | Turn HTML, PDF, Word and PowerPoint into text, remembering the page or slide |
| [items.py](items.py) | Read and write the saved items (one `.md` file each) |
| [folders.py](folders.py) | The folder layout, and writing files safely |
| [courses.py](courses.py) | Questions about the saved courses: which exist, their facts, their items |
| [uploads.py](uploads.py) | Add your own documents to a course |

## The idea

**Extraction, format by format.** Each format keeps the structure that helps a reader:

| Format | What is kept |
|---|---|
| Canvas pages (HTML) | headings as `#`, lists as `-`, and link targets |
| PDF | the text of each page, labelled `p. 3` |
| PowerPoint | the text of each slide and its speaker notes, labelled `slide 5` |
| Word | paragraphs, headings and tables |

The labels travel all the way into answers, so a citation can say *Lecture 09 - Work and
Energy.pdf, p. 2*. A scanned PDF holds pictures of text with no text layer: it is saved but marked
**no text**, and nothing in it can be found.

**The folder is the contract.** The scraper writes the course folders and the index reads them; the
two never call each other. Anything that writes this format becomes searchable, which is all an
upload is.

```
data/courses/Physics I Mechanics (41001)/
  course.json        real name, code, term, short summary, which tabs were read
  facts.json         exact values: due dates, points, scores, grade (used by tools/)
  home.md  syllabus.md
  modules/  pages/  assignments/  quizzes/  discussions/  announcements/
  files/Lectures/Lecture 09 - Work and Energy.pdf      the original download
  files/Lectures/Lecture 09 - Work and Energy.pdf.md   its extracted text (this is what gets embedded)
  uploads/...                                          documents you added (Sync never touches these)
```

**One item, one file.** Every saved item is a `.md` file: a small JSON header between `---` lines
(id, course, type, title, module, link), then the text, with a marker before each page or slide.
Open one next to its PDF to see exactly what the model can read.

**Your own documents.** A textbook chapter, your notes or a practice exam can change what the
assistant can answer. **Add your own documents** on the Courses page saves an upload the same way as
a Canvas file, in the course's `uploads/` folder, and indexes it straight away. Copying files into
that folder yourself works too: the next Sync picks them up. The file name becomes the title in
citations, so name it well.

## Try it

- Open a `files/.../*.pdf.md` next to its PDF. What did extraction keep, and what did it lose:
  tables, equations, figures?
- Search for something your course material does not really explain, add a document that does,
  and search again. Press **Text** next to the upload to see what extraction produced: anything
  missing there can never be found.
