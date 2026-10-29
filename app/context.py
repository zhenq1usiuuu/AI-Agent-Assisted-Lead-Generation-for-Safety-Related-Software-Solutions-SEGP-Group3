import hashlib
import json
from .models import Plan, ChunkExtraction, SemanticReview
from .sources import ROOT, SOURCES

SCHEMAS = {'receive_prompt': Plan.model_json_schema(), 'extract_signals': ChunkExtraction.model_json_schema(), 'review_signals': SemanticReview.model_json_schema()}
PREFIX = ((ROOT / 'prompts/system.md').read_text(encoding='utf-8-sig')
          + '\nSOURCE_REGISTRY\n' + json.dumps(SOURCES, ensure_ascii=False, sort_keys=True)
          + '\nOUTPUT_SCHEMAS\n' + json.dumps(SCHEMAS, ensure_ascii=False, sort_keys=True))
PREFIX_HASH = hashlib.sha256(PREFIX.encode()).hexdigest()
