# Evaluating Bread

Does the assistant answer questions about papers correctly, cite what it
claims, land its citations on the right page, and say so when a paper does not
contain the answer? This folder holds the questions that measure it, and every
run's results.

Results: [RESULTS.md](RESULTS.md).

## The set

[`questions.json`](questions.json) has 42 questions about nine open-access
papers on local and biologically plausible learning rules:

| papers | |
|---|---|
| Forward-Forward (Hinton 2022) | Equilibrium Propagation (Scellier & Bengio 2016) |
| Feedback alignment (Lillicrap et al. 2016) | Competing hidden units (Krotov & Hopfield 2018) |
| SoftHebb (Moraitis et al. 2021) | Hebbian Deep Learning Without Feedback (Journé et al. 2022) |
| Sparse Distributed Memory is a Continual Learner (Bricken et al. 2023) | Shallow networks (Illing et al. 2019) |
| Overcoming catastrophic forgetting (Kirkpatrick et al. 2016) | |

| kind | n | asks | a correct response |
|---|---|---|---|
| lookup | 14 | a number or fact from the body | states it |
| mechanism | 13 | how the paper defines or does something | explains it as the paper does |
| unanswerable | 9 | something plausible the paper does not contain | says the paper does not contain it |
| find-paper | 6 | which paper in the library does X, without naming it | names it |

Each answerable item carries:

- `answer`: the gold answer, written from the paper.
- `must_mention`: the facts a correct answer has to state. Each is a group of
  acceptable spellings.
- `evidence`: one or two quotes with their page, checked word for word against
  the PDF.
- `paraphrase`: the same question in plain words, for the retrieval comparison.
- `notes`: the traps a grader should know. For example, 1.37% is a *different*
  Forward-Forward result than the 1.36% asked about, and the EWC λ of 400 is for
  Atari only.

Papers are identified by DOI and arXiv id, not by library id, so the set works
in any library holding the same papers. The PDF's SHA-256 is recorded too, so a
different version of the file, with different page numbers, is reported rather
than silently scored against.

### How it was built, and what that means

The questions were written by Claude subagents, each reading three of the
papers' full text. Every evidence quote was then located mechanically in the
PDF, on the page claimed, by the same checker the app uses for citations.
`./breadcrumbs eval check` re-runs that validation. Each unanswerable question
was confirmed absent by searching the paper's text for its key terms and their
synonyms, and the terms searched are listed in the item.

Two consequences are worth stating plainly:

- **The question writer and some subjects share a model family.** A Claude
  model answering questions written by Claude may find their phrasing familiar.
  Every item states its facts and evidence explicitly, so anyone can audit it,
  but the set has not been reviewed by a second person.
- **The questions borrow the papers' vocabulary.** They were composed while
  reading, so "the anti-Hebbian parameter Δ" is asked in the paper's own words.
  That favours keyword search. Every question with evidence therefore also has
  a paraphrase in plain words, and retrieval is scored on both.

## Running it

```bash
./breadcrumbs eval check                     # is the set sound in this library? free
./breadcrumbs eval retrieval --save          # keyword vs semantic vs hybrid. free
./breadcrumbs eval answers --provider deepseek --model deepseek-v4-pro --thinking medium \
    --judge deepseek:deepseek-v4-pro         # the whole assistant; costs tokens
./breadcrumbs eval answers --provider openrouter --model google/gemini-3.8-flash \
    --thinking medium --only ff-1 ff-4       # any provider; a couple of items first
./breadcrumbs eval judge --judge deepseek:deepseek-v4-pro   # re-grade saved answers
./breadcrumbs eval report                    # re-score with current rules, rebuild RESULTS.md
```

A full run is 42 questions at roughly 25,000–35,000 tokens each, most of it the
page and tool results the model reads. Run two or three items with `--only`
before a full run on a paid model.

`answers` runs the real assistant: the same loop, prompt, tools and citation
check as the app.

- **It uses a copy of your library.** The database is copied into a temporary
  folder and the PDFs are linked, so nothing the assistant writes during a run
  (a note, a highlight) reaches the real library.
- **It asks each question from the view where you would ask it.** A paper
  question is asked from the reader, open at page 1 of that paper. A library
  question is asked from the grid with nothing selected.

Each run saves its full record to `runs/`: every answer, its citations and
their verdicts, the tools called, tokens, and the turn's record. The record
names the exact model the provider served, the app's commit and the SHA-256 of
each PDF read.

## What is scored

The headline numbers are mechanical. Anyone can re-apply the rule to the saved
answers and get the same figure.

| metric | rule |
|---|---|
| **correct** | every `must_mention` group appears in the answer's prose. Quotes inside citations do not count: a fact has to be stated to the reader, not hidden in a tooltip |
| **grounded** | correct, *and* at least one citation to the right paper was found in it |
| **right page** | a verified citation lands on a page the gold evidence is on (paper questions) |
| **valid quotes** | citations found in the paper, over all citations made |
| **abstained** | for unanswerable questions, the answer says the paper does not contain it. This is a phrase rule, so every such answer is printed in the run's report for a reader to check |
| **repairs** | answers that needed the correction round. The cause is either quotes that were not found, or claims about a paper with no citation |

`--judge provider:model` adds what no rule can decide: whether each cited
quote actually supports the sentence it is attached to, and whether an answer
contains claims about the paper that neither the gold answer nor its quotes
cover. The second is not a hallucination rate. The judge sees only the gold
answer and the quotes, so a true detail from elsewhere in the paper (a table's
values, an architecture) counts as uncovered. Read it as "how much the answer
says beyond what it cited". The examples in the saved runs are mostly true. A judge is a model's opinion. It is reported in its own table, its
prompt and raw output are saved with the run, and it never feeds the mechanical
scores. When the judge and the answering model are the same model, that is
self-grading, and the results table shows it.

## Known limits

- **42 questions is small.** A difference of one or two answers is 3–5
  percentage points, which is noise. The set is for catching regressions and
  seeing how models differ in kind, not for ranking close results.
- **`must_mention` is strict about substance and lenient about form.** It
  cannot tell a correct number stated in a wrong context from a right one; the
  `notes` list the traps, and the judge reads them.
- **The abstention rule recognises phrasings, not meaning.** "The authors do
  not report a CIFAR-100 result" passes. An evasive answer that never says so
  fails, which is the intended direction, but a rule can be gamed.
- **A verified quote proves presence, not support.** Only the judge, or a
  reader, can say whether the quote backs the claim.

## Findings that changed the code

Building and running the evaluation found problems in the product and in the
evaluation itself. Each is fixed, and each is left on record here because
finding them is what the evaluation is for.

**In retrieval**

1. **The chunker hid sections after the bibliography.** In the arXiv version of
   the EWC paper, Methods comes *after* the reference list, and the chunker
   marked everything after "References" as bibliography and left it out of
   search. Both EWC methods questions scored zero on every retriever. Chunker
   v2 ends the bibliography at a numbered section heading, or as soon as text
   stops reading like references.
2. **The questions flattered keyword search.** See *How it was built*. The
   paraphrases were added before any tuning, and none was done afterwards: with
   27 questions per scope, tuning would fit the set rather than the problem.

**In the assistant**

3. **"Cite what you claim" in the prompt was not enough.** In the first smoke
   test, a correct answer said "per Appendix B" and cited nothing. Answers that
   attribute statements to a paper the turn has read, but cite nothing, now get
   the same single correction round as quotes that were not found. Negative
   statements ("the paper does not report X") are exempt, since no quote can
   show an absence, and asking for one invites citing something nearby.
4. **A correct quote failed the check.** Tool results reach the model as JSON,
   so a line break arrives as the two characters `\n`, and DeepSeek copied them
   into its quote. The checker now also tries a quote with copied escapes read
   as the spaces they were, and `read_pdf` returns joined lines so there is
   nothing to copy.
5. **The answer was not always in the last message.** Models often state the
   finding beside a tool call (`show_in_pdf`) and close with one line. The live
   panel showed only the closing line, and the check and the evaluation looked
   only at it. Text written beside tool calls is now shown as it arrives, and
   its citations count toward the turn. A first Claude Sonnet 5.5 run was
   scored before this fix and discarded.
6. **One model put the facts inside the quotes.** Gemini 3.8 Flash wrote
   "Backprop: [p. 4](cite:…)" with the number only in the quote, which the
   reader sees as a page chip. The mechanical score counted those facts as not
   stated, which is what the reader experiences; the judge, reading the quotes,
   counted them correct. That gap is the whole difference between the two
   "correct" columns for that model. The prompt now says the quote is not shown
   inline, and since a prompt is not a guarantee (DeepSeek later did the same
   in a screenshot run), the answer now shows any quote whose words are not
   already in the sentence, in quotation marks before its chip. The Gemini run
   predates both changes and was not repeated.
7. **One model searched without end.** On questions the paper cannot answer,
   Gemini searched the PDF and the web for thirty rounds and never replied. After
   four searches in a row that find nothing, each further miss now says plainly
   that the answer is probably not in the paper, and that saying so is a
   complete answer.

**In the evaluation**

8. **The abstention rule missed real abstentions.** "does **not report**" (the
   Markdown broke the phrase), "doesn't name", "isn't specified". Emphasis is
   now removed before matching and the rule covers those forms. This was
   changed after reading answers, so the judge's abstention figure is reported
   beside it as an independent check. `./breadcrumbs eval report` re-applies
   the current rules to every saved answer.
9. **The judge contradicted itself.** Asked whether an answer "abstained", it
   often explained that the answer correctly said the paper lacks the
   information, then answered false. It took "abstained" to mean "gave no
   information". The judge prompt now defines its terms, and every saved run
   was re-judged (`./breadcrumbs eval judge`).

**Found while making the README's screenshots**

10. **A suggested paper had someone else's identifier.** Asked for a paper
    that builds on feedback alignment, Bread wrote a link titled for one
    paper, with the arXiv id of an unrelated paper on Chern-Simons theory. The
    preview showed the real record, so nothing wrong could be saved, but the
    suggestion itself was false, and nothing checked it. Suggestions are now
    checked like quotes: the identifier is looked up in OpenAlex and its real
    title compared with the one written. A mismatch loses its button and joins
    the correction round.
11. **A correct quote failed on a symbol font.** Bread quoted "n = 20" from
    the feedback-alignment paper, and the check said not found. The quote was
    right: that Nature PDF sets its maths in a font whose codes land on
    Latin-1 fractions, so every "=" is stored as "¼" and "[" as "½", and the
    checker expanded those into the digits "14" and "12". They are now dropped
    like any other symbol. Three papers in this library carry the encoding.
12. **The corrected answer appeared under its draft.** The correction round
    replaced only the final message, so a draft written beside a tool call
    stayed on screen with its failed quote, above the corrected answer, and its
    failed citation still counted in the corrected answer's tally. Draft text is
    now marked superseded when the correction round starts: the panel shows the
    correction alone, the draft's steps stay in the trail, and the turn's
    record keeps the draft. The evaluation likewise scores what survives the
    correction; saved runs predate this and were scored with the drafts in.

**A limit the evaluation shows rather than fixes**

13. **A verified quote can sit under an invented claim.** Asked for the
    Forward-Forward goodness threshold, which the paper never states, GPT-5.6
    Luna answered "θ = 2" and cited, verbatim, the sentence that *defines* θ.
    The chip correctly says the quote is in the paper; it cannot say the quote
    does not contain a 2. The judge flagged it, and so would a reader who
    clicked the chip. This is why "quote supports claim" is a separate measure,
    and why the answer footer never claims more than that the words were found.
