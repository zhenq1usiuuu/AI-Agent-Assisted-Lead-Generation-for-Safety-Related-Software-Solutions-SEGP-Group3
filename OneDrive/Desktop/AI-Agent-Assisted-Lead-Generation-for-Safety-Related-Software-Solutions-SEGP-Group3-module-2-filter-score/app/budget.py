"""Persistent conservative reservations for serial Kimi calls."""
import json
from datetime import datetime, timezone
from pathlib import Path


class BudgetExceeded(ValueError):
    pass


class EvaluationBudget:
    """Serial ledger; uncertain failures keep reservations. Optional settling uses a conservative bound from returned usage.

    Assumes official K3 USD pricing. Reserve input at $6/M (including the
    one-hour cache-write rate), output at $15/M, and byte-based input headroom.
    This is an upper-bound planning guard, not a provider billing statement.
    Do not share one ledger between concurrent processes.
    """
    def __init__(self, path, limit_usd=10.0, settle=False):
        self.path = Path(path)
        self.limit = limit_usd
        self.settle = settle
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.data = json.loads(self.path.read_text(encoding='utf-8')) if self.path.exists() else {'calls': []}

    def save(self):
        self.data['limit_usd'] = self.limit
        self.data['reserved_usd'] = round(sum(c['reserved_usd'] for c in self.data['calls']), 6)
        self.data['committed_usd'] = round(self.committed(), 6)
        temporary = self.path.with_suffix('.tmp')
        temporary.write_text(json.dumps(self.data, indent=2), encoding='utf-8')
        temporary.replace(self.path)

    def committed(self):
        return sum(c.get('settled_bound_usd', c['reserved_usd']) if self.settle else c['reserved_usd'] for c in self.data['calls'])

    def reserve(self, body, stage):
        # UTF-8 bytes exceed ordinary token counts; include serialization overhead.
        input_bound = len(json.dumps(body, ensure_ascii=False).encode('utf-8')) + 8192
        reserved = input_bound * 6 / 1_000_000 + body['max_tokens'] * 15 / 1_000_000
        if self.committed() + reserved > self.limit:
            raise BudgetExceeded('Run budget guard stopped before sending another paid request')
        row = {'stage': stage, 'at': datetime.now(timezone.utc).isoformat(),
               'reserved_usd': reserved, 'status': 'reserved_before_request'}
        self.data['calls'].append(row)
        self.save()
        return len(self.data['calls']) - 1

    def record(self, index, usage):
        row = self.data['calls'][index]
        row.update(status='response_received', usage=usage)
        prompt = usage.get('prompt_tokens', 0)
        output = usage.get('completion_tokens', 0)
        cached = usage.get('prompt_tokens_details', {}).get('cached_tokens', 0)
        # Default 5-minute cache writes have the same rate as uncached input.
        row['estimated_usd_default_cache'] = ((prompt - cached) * 3 + cached * .3 + output * 15) / 1_000_000
        if self.settle and 'prompt_tokens' in usage and 'completion_tokens' in usage:
            row['settled_bound_usd'] = (prompt * 6 + output * 15) / 1_000_000
        self.save()
