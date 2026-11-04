# Brain reader checks

Run backend metadata, title-index migration and ingress asset checks with temporary notes:

```
PYTHONDONTWRITEBYTECODE=1 python3 -W error::ResourceWarning -m unittest discover -s ih_brain/tests -v
```

Run browser fixtures with Node 22 and an existing Chromium DevTools endpoint:

```
IH_BROWSER_CDP=http://127.0.0.1:9222 node ih_brain/tests/reader_browser.js
```

The browser test uses two synthetic notes, no Home Assistant session or production data, and rejects
write requests. It checks four width/theme combinations, common Markdown structures, complete table
cells, exact source disclosure, inert HTML/images/unsafe URLs, note links, history, pending feedback,
retry and cancelled responses. Screenshots go to `/tmp/ih-reader-previews` unless
`IH_READER_OUTPUT_DIR` is set. Installed real-data verification is a separate release step.

This is a dependency-free readable subset, not a full CommonMark implementation. Unsupported content
remains accessible through the exact source view. Attachment preview parity stays a separate task.
