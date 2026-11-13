#!/usr/bin/env bash
# Regenerate the DOCX and PDF versions of the handover reports from their Markdown sources.
# Requires: pandoc, LibreOffice (soffice), python3 with python-docx.
set -euo pipefail
cd "$(dirname "$0")/reports"
for md in executive/*.md technical/*.md; do
  dir=$(dirname "$md"); base=$(basename "$md" .md)
  kind=$([ "$dir" = executive ] && echo "Executive report" || echo "Technical report")
  (cd "$dir" && pandoc "$base.md" -o "$base.docx" --shift-heading-level-by=-1 \
      -M subtitle="$kind" -M date="13 November 2026" --resource-path=.)
  python3 ../style_docx.py "$dir/$base.docx"
  soffice --headless --convert-to pdf --outdir "$dir" "$dir/$base.docx" >/dev/null 2>&1
  echo "built $dir/$base.{docx,pdf}"
done
