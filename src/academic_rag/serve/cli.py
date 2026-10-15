"""The `acad` command: check | setup | scrape | embed | sync | courses | search | eval | serve

Each command is one cmd_*() function, which calls the same functions the API does.
build_parser() lists the commands and their options; main() runs the one asked for.
"""

from __future__ import annotations

import argparse
import logging
import sys
import textwrap
import time
from pathlib import Path

from .. import canvas, course_files, index, llm
from ..canvas import CanvasAuthError
from ..config import Settings, load_settings
from ..index import IndexMismatch

# Text the size of a real chunk, for `acad setup` to time the embedding model with
_SAMPLE = ("Gradient descent updates parameters in the direction of the negative gradient. "
           "Regularization penalizes large weights and reduces overfitting on small datasets. "
           "The midterm covers chapters one through five, including worked examples. ") * 5


# ------------------------------------------------------------------ helpers
def _setup_logging(verbose: bool) -> None:
    """Our progress messages with -v (and always for scrape and sync), quiet libraries."""
    logging.basicConfig(level=logging.WARNING, format="%(message)s")
    logging.getLogger("academic_rag").setLevel(logging.INFO if verbose else logging.WARNING)
    for noisy in ("httpx", "httpcore"):  # httpx logs full URLs, and Canvas file links carry a verifier
        logging.getLogger(noisy).setLevel(logging.WARNING)
    logging.getLogger("pypdf").setLevel(logging.ERROR)  # warns about harmless quirks in many real PDFs


def _progress():
    """A progress callback that redraws one line on a terminal and stays quiet otherwise."""
    tty = sys.stderr.isatty()
    last = {"t": 0.0}

    def show(message: str, done: int, total: int) -> None:
        """Redraw the progress line."""
        now = time.time()
        if not tty or (now - last["t"] < 0.1 and done < total):
            return  # not a terminal, or redrawn less than 0.1 s ago
        last["t"] = now
        line = f"  [{done}/{total}] {message}"[:110]
        print("\r" + line.ljust(110), end="\n" if done >= total else "", file=sys.stderr, flush=True)

    return show


def _embedder_and_store(settings: Settings):
    """The embedder .env asks for, and the index built with it."""
    embedder = index.get_embedder(settings)
    return embedder, index.open_store(settings, embedder.name)


# ----------------------------------------------------------------- commands
def cmd_check(args, settings: Settings) -> int:
    """acad check: who the Canvas token belongs to, and the courses it can see."""
    client = canvas.make_canvas(settings)
    me = client.get_self()
    courses = client.list_courses(settings.enrollment_states)
    print(f"Token OK. Signed in as: {me.get('name')} (id {me.get('id')})")
    print(f"{len(courses)} course(s) visible ({', '.join(settings.enrollment_states)}):")
    for c in courses:
        print(f"  {c['id']:>10}  {c['name']}")
    return 0


def cmd_setup(args, settings: Settings) -> int:
    """acad setup: download the embedding model once, and time it on this computer."""
    if settings.embedding_backend == "local":
        print("Downloading the embedding model (one time, about 80 MB) ...")
    embedder = index.get_embedder(settings)
    embedder.embed_query("warm up")  # loads (and the first time downloads) the model

    sample = [_SAMPLE[: settings.chunk_chars]] * 32
    t = time.perf_counter()
    embedder.embed_documents(sample)
    rate = len(sample) / max(time.perf_counter() - t, 1e-6)
    print(f"Ready: {embedder.name} embeds about {rate:,.0f} chunks/sec on this computer, "
          f"so a course of ~1,000 chunks takes about {1000 / rate / 60:.1f} min.")
    if settings.embedding_backend == "local":
        print("The model is cached in ~/.cache/chroma/onnx_models/ "
              "(copy that folder to another computer to skip the download there).")
    return 0


def cmd_scrape(args, settings: Settings) -> int:
    """acad scrape: download from Canvas into data/courses/."""
    stats = canvas.scrape(settings, course_ids=args.course, include_files=not args.no_files,
                          prune=not args.no_prune, progress=_progress())
    _print_scrape(stats, settings)
    return 0


def _print_scrape(stats, settings: Settings) -> None:
    """Each course's folder and how many items each tab gave, then the totals."""
    for c in stats.courses:
        tabs = ", ".join(f"{t['label']}={t['items']}" for t in c.tabs if t["status"] in {"scraped", "empty"})
        print(f"  {c.name}\n      -> {settings.courses_dir / c.folder}\n      {tabs}")
    print(f"Done: {stats.summary()}.")


def cmd_embed(args, settings: Settings) -> int:
    """acad embed: index the saved courses (only what changed, unless --rebuild)."""
    embedder, store = _embedder_and_store(settings)
    stats = index.embed_courses(settings, store, embedder, course_ids=args.course, rebuild=args.rebuild,
                                progress=_progress())
    print(f"Done: {stats.summary()}. Index now holds {store.count()} chunks.")
    return 0


def cmd_sync(args, settings: Settings) -> int:
    """acad sync: scrape, then embed."""
    cmd_scrape(args, settings)
    return cmd_embed(args, settings)


def cmd_courses(args, settings: Settings) -> int:
    """acad courses: the saved courses, with their summaries and index counts."""
    courses = course_files.list_courses(settings)
    if not courses:
        print("Nothing scraped yet. Run `acad scrape`.")
        return 1
    for c in courses:
        label = f"{c['name']}  [{c['course_code']}]" if c.get("course_code") else c["name"]
        print(f"{c['id']:>10}  {label}  ({c['indexed_chunks']} chunks indexed)")
        if c["summary"]:
            print(textwrap.indent(textwrap.fill(c["summary"], 96), " " * 12))
    print(f"{len(courses)} course(s) in {settings.courses_dir}: {sum(c['indexed_docs'] for c in courses)} "
          f"documents indexed ({sum(c['indexed_chunks'] for c in courses)} chunks).")
    return 0


def cmd_search(args, settings: Settings) -> int:
    """acad search: what retrieval finds for a query, with no AI."""
    embedder, store = _embedder_and_store(settings)
    if store.count() == 0:
        raise SystemExit("The index is empty. Run `acad scrape` then `acad embed` (or `acad sync`).")
    course_id = None
    if args.course:
        course_id, problem = index.resolve_course(course_files.list_courses(settings), args.course)
        if problem:
            raise SystemExit(problem)
    passages = index.search(store, embedder, " ".join(args.query), args.k,
                            course_id=course_id, source_types=args.type)
    print(index.format_passages(passages, max_chars=600) if passages else "No matching passages.")
    return 0


def cmd_eval(args, settings: Settings) -> int:
    """acad eval: score the running server on questions with known answers (eval.py)."""
    from .eval import run

    url = args.url or f"http://{settings.api_host}:{settings.api_port}"
    results = run(Path(args.file), url, args.provider, args.model, settings.data_dir / "eval", args.effort)
    return 0 if all(r["passed"] for r in results) else 1


def cmd_serve(args, settings: Settings) -> int:
    """acad serve: the web pages and the API, until Ctrl+C."""
    import uvicorn

    from .api import create_app

    host, port = args.host or settings.api_host, args.port or settings.api_port
    print(f"Open http://{host}:{port} in your browser  (API docs at /docs)")
    uvicorn.run(create_app(settings), host=host, port=port, log_level="info")
    return 0


# --------------------------------------------------------------------- main
def build_parser() -> argparse.ArgumentParser:
    """Every command and its options, as `acad --help` lists them."""
    p = argparse.ArgumentParser(prog="acad",
                                description="Academic assistant over your Canvas course materials")
    p.add_argument("-v", "--verbose", action="store_true", help="show progress logging")
    p.add_argument("--env-file", default=".env", help="path to the .env file (default: .env)")
    sub = p.add_subparsers(dest="command", required=True)

    # Options that several commands share
    def course_arg(sp):
        """--course, for the commands that can do only some courses."""
        sp.add_argument("--course", action="append", help="course id (repeatable); default: all courses")

    def scrape_args(sp):
        """--no-files and --no-prune, for scrape and sync."""
        sp.add_argument("--no-files", action="store_true", help="skip downloading PDFs/slides/docs")
        sp.add_argument("--no-prune", action="store_true", help="keep saved items that were deleted in Canvas")

    def rebuild_arg(sp):
        """--rebuild, for embed and sync."""
        sp.add_argument("--rebuild", action="store_true",
                        help="wipe the index and re-embed everything (needed after changing EMBEDDING_BACKEND)")

    sub.add_parser("check", help="verify the Canvas token and list visible courses").set_defaults(fn=cmd_check)
    sub.add_parser("setup", help="download the local embedding model and measure its speed"
                   ).set_defaults(fn=cmd_setup)

    s = sub.add_parser("scrape", help="save every readable Canvas tab into data/courses/<course name>/")
    course_arg(s)
    scrape_args(s)
    s.set_defaults(fn=cmd_scrape)

    e = sub.add_parser("embed", help="embed the saved course folders into the local index")
    course_arg(e)
    rebuild_arg(e)
    e.set_defaults(fn=cmd_embed)

    y = sub.add_parser("sync", help="scrape then embed")
    course_arg(y)
    scrape_args(y)
    rebuild_arg(y)
    y.set_defaults(fn=cmd_sync)

    sub.add_parser("courses", help="list saved courses with their summaries").set_defaults(fn=cmd_courses)

    r = sub.add_parser("eval", help="ask the running server questions with known answers, and score them")
    r.add_argument("--file", default="tests/eval/questions.md", help="the questions (default: %(default)s)")
    r.add_argument("--provider", choices=llm.PROVIDERS, help="default: LLM_PROVIDER in .env")
    r.add_argument("--model", help="default: that provider's default model")
    r.add_argument("--effort", choices=llm.EFFORTS,
                   help="how hard the model thinks (default: REASONING_LEVEL in .env, or medium)")
    r.add_argument("--url", help="the running server (default: API_HOST and API_PORT from .env)")
    r.set_defaults(fn=cmd_eval)

    q = sub.add_parser("search", help="show the passages retrieval finds for a query (no LLM)")
    q.add_argument("query", nargs="+")
    q.add_argument("--course", help="course id or part of its name")
    q.add_argument("--type", action="append", help="source type, e.g. syllabus, file, upload (repeatable)")
    q.add_argument("-k", type=int, default=6)
    q.set_defaults(fn=cmd_search)

    v = sub.add_parser("serve", help="start the HTTP API for the web front end")
    v.add_argument("--host")
    v.add_argument("--port", type=int)
    v.set_defaults(fn=cmd_serve)
    return p


def main(argv: list[str] | None = None) -> int:
    """Run one command. Expected problems (a bad token, a mixed-up index) print one line."""
    args = build_parser().parse_args(argv)
    _setup_logging(args.verbose or args.command in {"scrape", "sync"})
    settings = load_settings(args.env_file)
    try:
        return args.fn(args, settings)
    except (CanvasAuthError, IndexMismatch) as exc:
        print(f"error: {exc}", file=sys.stderr)
    except KeyboardInterrupt:
        print("\ninterrupted (nothing is lost: re-run to continue)", file=sys.stderr)
    return 1


if __name__ == "__main__":
    sys.exit(main())
