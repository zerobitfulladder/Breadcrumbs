# Demo video, GIF and screenshots

Everything the main README shows, recorded from the live app by scripts here.

A scripted tour of Breadcrumbs, recorded as a video. A Playwright-driven
browser works through the app with a visible cursor, captions and a zooming
camera:

- the grid and the citation trail
- a paper and one of its authors
- Bread on the library: an answer from its records, then one from its own
  knowledge, each labelled as what it is
- the reader: a highlight, then a question about a passage
- a question about what the paper says: the quotes checked against the PDF,
  a chip opening the passage, the record of what produced the answer, and a
  question the paper cannot answer
- Bread marking the page itself

The cursor is hidden on the opening and closing title cards.

```bash
cd demo
npm install
npm run record                      # -> out/breadcrumbs-demo.mp4, 2560x1440, 60 fps
npm run record -- --size 1920x1080  # any 16:9 size up to 2880x1620
npm run record -- --watch           # a visible browser, to rehearse
npm run record -- --no-video        # run the tour without recording, at normal speed
```

The tour runs in slow motion while it records, 4× by default (`--slow`), and
the video plays at normal speed. Chrome's screencast hands over 15 to 25
frames a second of a page this busy. Slowed fourfold, that becomes a smooth
60. DevTools slows every animation in the page by the same factor, and the
tour stretches its own motion to match. The run takes about four times as long
as the video. The last line it prints reports the frame rate Chrome supplied.
If that falls below 60, raise `--slow`.

Two more scripts make the README's pictures:

```bash
npm run gif      # ../docs/trail.gif, the grid lighting one paper at a time
npm run stills   # ../docs/grid.png, reader.png, grounding.png, three-kinds.png
```

The GIF drives nothing. Left alone, the grid tours itself, and the GIF records
that at normal speed, with no cursor in the way. `trail.mjs` lists the papers
it lands on. The tour picks at random, so the script feeds it numbers that
make those papers its picks. The stills ask Bread for real, four times: the
reader, a cited answer for `grounding.png`, and an answer of each kind for
`three-kinds.png`, which lays the three side by side under their headings.

It needs ffmpeg, and Chromium for Playwright 1.63
(`npx playwright-core install chromium` if it is not already in
`~/.cache/ms-playwright`).

## What it touches

Nothing of yours. Each run copies the library (`~/Breadcrumbs`, or
`--library`/`BREADCRUMBS_HOME`) to a temporary folder and serves the copy on
port 8765, with no earlier conversations and nothing marked yet in the paper
the tour reads. The copy is deleted when the run ends.

The copy keeps your settings, keys included, and Bread answers for real
through the configured provider. Six questions per video run, so a few cents
at most. Answers differ from take to take. The waits for them play fast, from
3× to 12×, with a badge on screen saying so.

## Changing it

`tour.mjs` is the video's storyboard: the paper, the passages, the questions, and the
scenes in order. Each line is one action: `d.click`, `d.type`, `d.caption`,
`d.focus` for the camera, and so on. The helpers are in `record.mjs`. The
overlay they drive is in `stage.js`: cursor, captions, title cards and the
camera.

The paper must be in your library with a PDF that has a text layer. The
passages must be quoted as they appear on its first page.
