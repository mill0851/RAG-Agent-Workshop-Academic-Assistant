# index/: the core of RAG

RAG is *retrieve, then generate*: find the passages that matter, then let a model answer from them.
This folder is the **retrieve** half. The files follow the pipeline, in order:

| Step | File | What it does |
|---|---|---|
| 1 | [chunk.py](chunk.py) | Cut each saved item into chunks of about 1,000 characters, none longer than the model can read |
| 2 | [embed.py](embed.py) | Turn text into vectors with a local model (no API key) |
| 3 | [store.py](store.py) | Keep the vectors in Chroma, and find the nearest ones to a question |
| | [build.py](build.py) | Run steps 1-3 for every course, only for what changed since last time |
| 4 | [search.py](search.py) | Find and number the best passages for a question (and **Task 3**) |

The Ask page's agent uses this search twice: for the first passages every question gets, and as its
`search_course` tool when it wants to search again ([tools/](../tools/README.md)).

## Architecture diagram

[architecture.mmd](architecture.mmd) draws both halves of this folder: building the index from the
course folders, and searching it. Both use the same embedder and the same Chroma store. It is a
[Mermaid](https://mermaid.js.org) diagram: plain text that a Mermaid viewer draws as a flowchart. To
see it:

- **In VS Code:** install a Mermaid extension, such as *Mermaid Chart*, open `architecture.mmd`, and
  open its preview.
- **In a browser:** paste the file into the editor at https://mermaid.live.
- **As an image file:** run this in this folder. The first run downloads a browser to draw with.

  ```bash
  npx -p @mermaid-js/mermaid-cli mmdc -i architecture.mmd -o architecture.svg
  ```

## The idea

### Chunking

A whole syllabus is too long to compare with a one-line question: one vector for many topics ends up
close to none of them. So each item is cut into **chunks** of about 1,000 characters, at paragraph
breaks first, then sentences, then words. Consecutive chunks overlap by 150 characters, so a
sentence on a boundary is not lost. The embedding model reads at most 256 *tokens* (word pieces), and
math has more tokens per character than prose, so any chunk that would be cut off is split again until
it fits.

Each chunk is embedded with a one-line header that says where it came from, so "the physics lecture
on energy" can match a page that never says "physics":

```
Physics I: Mechanics > Week 5: Work and Energy > Lecture 09 - Work and Energy.pdf (p. 2)
Kinetic energy: K = (1/2) m v^2.
Work-energy theorem: the net work done on an object equals the change in its
```

### Embeddings

An embedding model turns text into a list of numbers, here 384 of them, so that texts with similar
*meaning* get similar numbers. It is a small neural network, trained on about a billion pairs of
related texts (a question and its answer, a title and its article) to put each pair close together.
Similarity is measured as **cosine similarity**: 1 means the same direction (same meaning), 0 means
unrelated. The model, all-MiniLM-L6-v2, runs on your computer: no key, and nothing leaves your
computer after the one-time download (`acad setup`).

Real numbers from the model:

| Text A | Text B | Cosine |
|---|---|---|
| When is the midterm? | The midterm exam is on October 22 in Science Hall 100. | **0.64** |
| When is the midterm? | Lab reports are due on Fridays at 5:00 pm. | 0.18 |
| Homework 11.3 | Homework 11.4 | **0.97** |
| HW 4 | HW 5 | 0.85 |

The first two rows are why search by meaning works. The last two are its blind spot: to the model,
"Homework 11.3" and "Homework 11.4" mean almost the same thing. That is Task 3.

### The vector store and the search

[Chroma](https://www.trychroma.com/) keeps every chunk's vector, text and details (course, type,
title, page) in `data/chroma/`, inside this Python process. Given a question's vector, it returns the
nearest chunks, optionally within one course. `search()` in [search.py](search.py) is four steps:

```python
hits = store.query(embedder.embed_query(query), k * CANDIDATES_PER_RESULT,   # 1. retrieve wide: 18 for k=6
                   course_id=course_id, source_types=types)
passages = boost_exact_matches(query, [_passage(h) for h in hits])[:k]     # 2. rerank, 3. keep the best k
for n, p in enumerate(passages, start=1):
    p.n = n                                                                   # 4. number them for citation
```

Fetching three times more than you show is a common pattern: *retrieve wide, then rerank*. A cheap
first stage finds candidates; a second, smarter stage orders them.

## Building and searching, step by step

### Building: embed_courses()

`acad embed`, and the second half of every Sync, run `embed_courses()` in [build.py](build.py):

```
embed_courses()
1. the course folders          data/courses/*/ with a course.json (and your uploads/ files)
2. what is indexed already     data/embed_manifest.json; --rebuild empties the index instead
3. for each item:
     read_document()             its header and its labelled Segments
     unchanged?                  the same content hash as in the manifest: skipped
     chunk_document()            about 1,000 characters, one page at a time, cut to fit the model
     embed_documents()           about 64 chunks at a time, on your CPU
     store.upsert()              into Chroma, data/chroma/; the manifest is saved after each batch
4. prune                       items whose file is gone are taken out of the index
```

**Only what changed.** The manifest records a fingerprint of every item it indexed. An item whose
fingerprint is the same as last time is skipped, so a second embed only redoes what changed. A
changed item has its old chunks deleted first, because it may now have a different number of chunks.
Because the manifest is saved after every batch, an interrupted embed keeps what it finished.

**One model per index.** The index records which embedder built it. Vectors from two different
models cannot be compared, so if `EMBEDDING_BACKEND` changes, every search and embed stops with a
message to run `acad embed --rebuild`.

### What each chunk is stored with

| Detail | What it is | Used by |
|---|---|---|
| The vector | 384 numbers, from the chunk's header line and text | finding the nearest chunks |
| The text | The chunk itself, without the header line | the passages shown and sent to the model |
| `course_id`, `source_type` | Its course and type | limiting a search to one course, or to some types |
| `title`, `module`, `location`, `section` | Its document, module, page or slide, and nearest heading | citations like *Lecture 09.pdf (p. 2)* |
| `path`, `url` | Its saved `.md` file, and its page in Canvas | `read_document`, and the links under each source |
| `doc_id`, `chunk_index` | Which item it came from, and where in it | updating and deleting an item's chunks |

### Searching: search()

```
search(question, k)
1. embed_query()             the question, with the same model: 384 numbers
2. store.query()             the k × 3 nearest chunks by cosine similarity, in one course if chosen
3. boost_exact_matches()     Task 3: exact codes like "HW 2" first (on main, it changes nothing)
4. the best k, numbered      Passages: title, page, path, score and text, numbered [1], [2], ...
```

| Who searches | How many passages |
|---|---|
| The Search page (`POST /search`) | 6, 10 or 20, from its menu |
| `acad search` | 6, or `-k` |
| The `search_course` tool | 10 |
| The first search of every question on the Ask page | 20, then widened to whole pages ([agent/](../agent/README.md)) |

`format_passages()` turns passages into the text block that `acad search` prints and the model
reads.

## Try it

```bash
acad search "when is the midterm"
acad search "office hours" --course physics --type syllabus
```

- Ask the same thing in different words ("test" instead of "midterm"). The results barely change,
  because embeddings match meaning.
- Search for a *code* in one of your courses, such as an assignment or section number:
  `acad search "when is homework 3 due" --course <your course>`. Is the right homework first?

## Task 3: improve search

**You'll learn:** what embeddings are good and bad at, and the *retrieve wide, then rerank* pattern
that real search engines use to combine meaning with exact matching.
**Time:** about 30 minutes, ~15 lines in [search.py](search.py). **Suits:** comfortable with Python
(regular expressions and sorting).

### The problem

A calculus course numbers its homework by textbook section, and every homework description is the
same apart from that number. With the real model, *"when is homework 11.3 due?"* finds:

```
[1] 0.36  Help Center hours                       announcement
[2] 0.31  Calculus II Syllabus                    syllabus
[3] 0.29  Homework 11.4: Comparison Tests         assignment
[4] 0.27  Exam 2 next week                        announcement
[5] 0.27  Homework 11.2: Series                   assignment
[6] 0.25  Homework 7.1: Integration by Parts      assignment
```

Homework 11.3 is not in the top six. To the model, "11.3" and "11.4" are nearly the same (0.97
above), but a student asking about 11.3 wants 11.3. Course codes, assignment numbers and room
numbers must match exactly.

`search()` fetches 18 candidates and passes them through `boost_exact_matches`, which changes
nothing yet. Homework 11.3 *is* among the 18 (it just ranks below 6th), so reordering is enough.

### Your goal

Return the same passages, reordered so that those containing the question's exact codes and numbers
come first. Among passages that match equally, keep the vector order. Never drop one.

| Question | Exact terms | Should match | Should not match |
|---|---|---|---|
| when is homework 11.3 due | `homework 11.3` | "Homework 11.3: The Integral Test" | "Homework 11.4" |
| chem 2053 room | `chem 2053` | "CHEM 2053 meets in Room 305" | |
| HW 2 | `hw 2` | "HW 2 is due October 20" | "HW 22 is due in December" |
| what is overfitting | (none) | (order unchanged) | |

**Done when** `pytest tests/tasks/test_task3_exact_match.py` passes. Then restart `acad serve` and
search your own courses for an assignment by its number on the Search page: it comes first. The Ask
page benefits too, because its first passages and its `search_course` tool use this search.

### Strategy

1. **Find the exact terms in the question.** A useful rule: *a number, plus the word right before
   it*. That catches "homework 11.3", "chem 2053", "quiz 1" and "hw 2". Lowercase everything first.
2. **Count matches per passage.** For each passage, count how many terms appear in its title or
   text. Match *whole* terms, so "hw 2" does not match inside "hw 22".
3. **Sort by that count**, highest first. If the question has no exact terms, return the passages
   unchanged.

<details>
<summary>Hint 1: finding the terms with a regular expression</summary>

```python
import re

EXACT_TERM = re.compile(r"(?:[a-z]+ )?\d+(?:\.\d+)*")   # optional word + space, then 2053 or 11.3

terms = {m.group(0) for m in EXACT_TERM.finditer(query.lower())}
# "when is homework 11.3 due" -> {"homework 11.3"}
# "chem 2053 room"            -> {"chem 2053"}
```

</details>

<details>
<summary>Hint 2: matching whole terms</summary>

`\b` in a regular expression means a word boundary, and `re.escape` makes the `.` in "11.3" match a
real dot instead of "any character":

```python
pattern = re.compile(rf"\b{re.escape(term)}\b")
pattern.search("hw 2 is due")    # match
pattern.search("hw 22 is due")   # None: the 2 is followed by another digit, so no boundary
```

Search in `" ".join(f"{p.title} {p.text}".lower().split())`: lowercase, with runs of spaces and
newlines collapsed to one space.

</details>

<details>
<summary>Hint 3: sorting without losing the vector order</summary>

Python's `sorted` is *stable*: items with equal keys keep their original order. So among passages
with the same number of matches, the vector order survives for free:

```python
return sorted(passages, key=count_matches, reverse=True)
```

</details>

### Going further

- **Blend instead of sort.** Sorting puts *any* exact match above *every* other passage. Try
  `score + 0.15 * matches` instead, and find a question where each approach wins.
- **Real hybrid search.** Build a keyword index over all chunks (BM25, for example with the
  `rank_bm25` package) and merge its ranking with the vector ranking. That also finds exact matches
  that were not among the 18 candidates.
- **Think about it:** which questions should never be boosted? What happens with "what is due on
  10/20"?
