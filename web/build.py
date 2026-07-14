#!/usr/bin/env python3
"""Inline the JS engine of record and the parity vectors into the single-file HTML.

This guarantees the browser file's engine is byte-identical to the source that passed
the Node parity test — there is one JS source, inlined for portability.
Run: python web/build.py
"""
import os
root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
html = open(os.path.join(root, "web", "dosedesk.template.html")).read()
engine = open(os.path.join(root, "engine", "dosedesk_engine.js")).read()
vectors = open(os.path.join(root, "provenance", "parity_vectors.json")).read().strip()

assert "/*__DOSEDESK_ENGINE__*/" in html, "engine placeholder missing (already built?)"
assert "/*__PARITY_VECTORS__*/" in html, "vectors placeholder missing (already built?)"

html = html.replace("/*__DOSEDESK_ENGINE__*/", engine)
html = html.replace("/*__PARITY_VECTORS__*/ null", vectors)

out = os.path.join(root, "web", "dosedesk.html")
open(out, "w").write(html)
print("built web/dosedesk.html — engine + %d bytes vectors inlined" % len(vectors))
