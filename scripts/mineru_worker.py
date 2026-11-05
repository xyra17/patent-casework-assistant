"""Run local MinerU in its own environment; the main app never imports its ML stack."""
from __future__ import annotations

import contextlib
import json
import sys
from pathlib import Path


def main():
    source, output, tier = sys.argv[1:4]
    # Model progress belongs on stderr, never in the structured result.
    with contextlib.redirect_stdout(sys.stderr):
        from mineru.parser import parse
        from mineru.config import get_config, VlmConfig
        config = get_config()
        config.llm_aided.features.title_leveling = False
        config.llm_aided.features.cross_page_table_cell_merge = False
        # A user's global MinerU config must not turn local parsing into uploads.
        result = parse(source, tier=tier, ocr_mode='auto', vlm_config=VlmConfig(server_url=''))
        payload = result.structured_content()
        # Structured rendering omits default fields and block indices. Restore
        # these directly from the typed result, never infer coverage from text.
        payload['is_full_document'] = result.middle_json.is_full_document
        for page, original in zip(payload['pages'], result.pages):
            for block, original_block in zip(page['blocks'], original.blocks):
                block['index'] = original_block.index
    Path(output).write_text(json.dumps(payload, ensure_ascii=False), encoding='utf-8')


if __name__ == '__main__':
    main()
