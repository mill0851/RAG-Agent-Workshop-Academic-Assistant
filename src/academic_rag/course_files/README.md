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

## Architecture diagram

[architecture.mmd](architecture.mmd) draws the course folder as the hand-off between the parts: what
writes it (the scraper and your uploads), the files in this folder, what a course folder holds, and
what reads it. It is a [Mermaid](https://mermaid.js.org) diagram: plain text that a Mermaid viewer
draws as a flowchart. To see it:

- **In VS Code:** install a Mermaid extension, such as *Mermaid Chart*, open `architecture.mmd`, and
  open its preview.
- **In a browser:** paste the file into the editor at https://mermaid.live.
- **As an image file:** run this in this folder. The first run downloads a browser to draw with.

  ```bash
  npx -p @mermaid-js/mermaid-cli mmdc -i architecture.mmd -o architecture.svg
  ```

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

## An item's life, step by step

Everything in a course folder is written through this folder and read back through it. The scraper
and the index never call each other: they agree on the folder.

```
Writing: the scraper (canvas/), or an upload
├── extract.py   html_to_text()     Canvas HTML to text: headings as #, lists as -, links kept
│                extract_file()     a file's bytes to Segments: text + "p. 3" or "slide 5"
├── items.py     render_item()      the header, the title, then each Segment after its marker
│                write_item()       saved only if the text changed
└── folders.py   write_bytes()      written to a .tmp file, then renamed into place

Reading: the index, the tools, the agent and the API
├── items.py     read_meta()        just the header: fast, for lists and checks
│                read_document()    the header and the labelled Segments back
│                iter_documents()   every item in a course folder
└── courses.py   list_courses()     the saved courses, with their index counts
                 get_facts()        a course's facts.json
                 list_documents()   a course's items, from their headers
```

**1. Into text.** Canvas sends rich text as HTML, which `html_to_text()` turns into readable text.
Files go through `extract_file()`, which picks a reader by file type and returns *Segments*: pieces
of text, each with an optional page or slide label. A scanned PDF has no text layer, so it gives
none.

**2. Into an item file.** `render_item()` writes the header as JSON between two `---` lines, then
the title as a heading, then each Segment, with a `<!-- segment: p. 3 -->` marker before each
labelled one. `write_item()` compares that with the file on disk and writes only when they differ,
which is how a second sync knows what is *unchanged*.

**3. Safely onto disk.** `write_bytes()` writes to `name.tmp` and then renames it over the real
file. A crash in the middle leaves the old file, never half of a new one.

**4. Back out.** `read_document()` parses an item file back into its header and Segments, cut at the
markers, so the page labels survive all the way into search results and citations. A file without
a header, like a note you dropped in the folder, is ignored.

### What an item's header holds

| Field | What it is | Example |
|---|---|---|
| `doc_id` | A stable id: course, type and Canvas id | `course:41001:file:9001` |
| `course_id`, `course_name` | The course it belongs to | `41001`, `Physics I` |
| `source_type` | Its type: home, syllabus, module, page, assignment, quiz, announcement, discussion, file or upload | `file` |
| `title` | Its title, as citations show it | `Lecture 09.pdf` |
| `url` | Its page in Canvas (empty for uploads) | `https://canvas.../files/9001` |
| `module` | The module it is in, if any | `Week 5: Work and Energy` |
| `updated_at` | When Canvas last changed it | `2026-09-10T10:00:00Z` |
| `canvas_id` | Its id in Canvas (not for uploads) | `9001` |
| `extractor`, `no_text`, `size` | Files only: which text extractor read it, whether it had no text, its size in bytes | `2`, `false`, `1000` |

### Your own documents

| Way in | What happens |
|---|---|
| **Add your own documents** on the Courses page | `save_upload()` saves the original in `uploads/`, extracts its text the same way as a Canvas file, writes its `.md` item, and the course is indexed straight away. Uploading the same name again replaces it |
| Copying a file into `uploads/` yourself | The next embed (Sync, or `acad embed`) calls `sync_uploads_folder()`, which extracts new and changed files and drops the item of any file you deleted |
| **Delete** next to an upload | `delete_upload()` removes the file and its item, and only inside `uploads/`: Canvas content cannot be deleted here |

The scraper only prunes the folders of Canvas tabs, so `uploads/` is never touched by a sync.

## Try it

- Open a `files/.../*.pdf.md` next to its PDF. What did extraction keep, and what did it lose:
  tables, equations, figures?
- Search for something your course material does not really explain, add a document that does,
  and search again. Press **Text** next to the upload to see what extraction produced: anything
  missing there can never be found.
