---
title: tamperlint
emoji: 🔍
colorFrom: gray
colorTo: red
sdk: gradio
app_file: app.py
pinned: false
license: apache-2.0
short_description: Explainable tamper detection for PDFs and scans
---

# tamperlint demo

Upload a PDF, JPEG or PNG, or pick a synthetic SPECIMEN sample, and tamperlint shows its verdict
with the suspicious regions highlighted on the page.

Uploads are deleted as soon as they have been read; a cache sweep every few minutes removes
anything else Gradio kept, such as rejected file types. Findings are evidence for a human
reviewer, not proof of fraud. Source code, documentation and limitations: see the
[tamperlint repository](https://github.com/muneeb9211/tamperlint) on GitHub.
