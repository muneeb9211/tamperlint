# Security policy

## Reporting a vulnerability

Please report vulnerabilities privately through GitHub's
[private vulnerability reporting](https://docs.github.com/en/code-security/security-advisories/guidance-on-reporting-and-writing-information-about-vulnerabilities/privately-reporting-a-security-vulnerability)
("Report a vulnerability" on the repository's Security tab). Do not open a public issue.

We aim to acknowledge reports within 5 working days and to publish a fix or mitigation within
30 days for confirmed issues.

## Scope

tamperlint parses untrusted PDFs and images. In scope:

- crashes, hangs or excessive memory use triggered by a crafted file (denial of service);
- code execution or file access through a crafted file;
- ways to make the HTTP API keep, log or leak uploaded documents;
- HTML-report injection (the report escapes all document-derived text and contains no scripts).

Evasion techniques (a forgery tamperlint does not detect) are not vulnerabilities; please open a
regular issue, with a synthetic example, so a check can be added.

## Hardening built in

- File size, page, pixel, timeout and concurrency limits (see the README for the API settings).
  A timed-out analysis keeps its concurrency slot until its thread finishes, so slow uploads
  cannot pile up work; limits apply per worker process.
- Uploads are never stored by tamperlint. The web framework spools bodies over 1 MB to a
  temporary file during the request; deploy behind a proxy that limits request size.
- The Gradio demo deletes each upload from Gradio's cache as soon as it has been read, and
  sweeps the cache every five minutes for anything else (such as rejected file types).
- No PDF JavaScript is executed; no network access during analysis; signature validation uses no
  online revocation fetching.
- The Docker image runs as a non-root user.

## Responsible use

tamperlint produces evidence for human review. Do not take adverse action against a person on the
basis of an automated verdict alone.
