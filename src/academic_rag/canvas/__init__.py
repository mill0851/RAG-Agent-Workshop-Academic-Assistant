"""Getting data out of Canvas: client.py talks to the API, scraper.py saves each course
into data/courses/. Start reading at scraper.scrape(). See README.md in this folder."""

from .client import CanvasAuthError, CanvasClient, CanvasNotAvailable
from .scraper import ScrapeStats, make_canvas, scrape
