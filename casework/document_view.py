"""Page a specific parsed revision and locate a citation without rewriting it."""
from __future__ import annotations

from casework import store


def document_page(item, *, offset=0, limit=10, focus_chunk_id=None, focus_location=None, focus_excerpt=None):
    chunks = item['payload']['chunks']
    index = None
    if focus_chunk_id:
        index = next((i for i in range(len(chunks)) if store.digest(
            [item['id'], item['revision'], item['parser_key'], i]) == focus_chunk_id), None)
    # An explicit chunk identity must never be silently replaced by another chunk.
    elif focus_location:
        locations = [p.strip() for p in focus_location.split('；') if p.strip()]
        index = next((i for i, c in enumerate(chunks) if c['location'] in locations), None)
        if index is None:
            # Spreadsheet field claims locate a column within a parsed row chunk.
            index = next((i for i, c in enumerate(chunks)
                          if any(p.startswith(c['location'] + ' · 第') for p in locations)), None)
    elif focus_excerpt:
        index = next((i for i, c in enumerate(chunks) if focus_excerpt in c['text']), None)
    requested = bool(focus_chunk_id or focus_location or focus_excerpt)
    if index is not None:
        offset = index // limit * limit
    annotated = [dict(c, ordinal=i, cited=i == index) for i, c in enumerate(chunks)]
    context = annotated[max(0, index - 1):index + 2] if index is not None else []
    page_chunks = annotated if index is not None else chunks
    return dict(chunks=page_chunks[offset:offset + limit], offset=offset, limit=limit,
                total_chunks=len(chunks), has_more=len(chunks) > offset + limit,
                focus_index=index, focus_context=context,
                focus_status='located' if index is not None else 'not_found' if requested else 'not_requested')
