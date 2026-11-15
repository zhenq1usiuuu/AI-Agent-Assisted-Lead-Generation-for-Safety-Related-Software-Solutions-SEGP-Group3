"""Persistent conservative reservations for serial Kimi calls."""
import json
from datetime import datetime, timezone
from pathlib import Path
from contextvars import ContextVar

COST_REPORTER = ContextVar("cost_reporter", default=None)


class BudgetExceeded(ValueError):
    pass


class EvaluationBudget:
    """Serial ledger with conservative preflight reservations and usage settlement.

    Successful Chat Completions calls settle cache read/write and uncached input
    separately. Unknown charges retain reservations. Amounts estimate provider
    billing and exclude search/tool fees. Do not share a ledger concurrently.
    """
    def __init__(self, path, limit_usd=10.0, settle=True):
        self.path = Path(path)
        self.limit = limit_usd
        self.settle = settle
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.data = json.loads(self.path.read_text(encoding='utf-8')) if self.path.exists() else {'calls': []}
        # Recompute legacy successful calls from retained usage, never discard uncertain attempts.
        for index, row in enumerate(self.data['calls']):
            if row.get('usage') and 'settled_usd' not in row:
                self.record(index, row['usage'], notify=False)

    def save(self):
        self.data['limit_usd'] = self.limit
        self.data['reserved_usd'] = round(sum(c['reserved_usd'] for c in self.data['calls']), 6)
        self.data['committed_usd'] = round(self.committed(), 6)
        self.data['estimated_used_usd'] = sum(c.get('settled_usd', 0) for c in self.data['calls'])
        self.data['unresolved_reserved_usd'] = sum(c['reserved_usd'] for c in self.data['calls'] if 'settled_usd' not in c)
        temporary = self.path.with_suffix('.tmp')
        temporary.write_text(json.dumps(self.data, indent=2), encoding='utf-8')
        temporary.replace(self.path)

    def committed(self):
        return sum(c.get('settled_usd', c['reserved_usd']) if self.settle else c['reserved_usd'] for c in self.data['calls'])

    def reserve(self, body, stage):
        # UTF-8 bytes exceed ordinary token counts; include serialization overhead.
        input_bound = len(json.dumps(body, ensure_ascii=False).encode('utf-8')) + 8192
        reserved = input_bound * 6 / 1_000_000 + body['max_tokens'] * 15 / 1_000_000
        self.data['next_reservation_usd'] = reserved
        self.save()
        self.report(reserved)
        if self.committed() + reserved > self.limit:
            raise BudgetExceeded('Run budget guard stopped before sending another paid request')
        row = {'stage': stage, 'at': datetime.now(timezone.utc).isoformat(),
               'reserved_usd': reserved, 'status': 'reserved_before_request',
               'cache_ttl':body.get('prompt_cache_options', {}).get('ttl', '5m')}
        self.data['calls'].append(row)
        self.save()
        return len(self.data['calls']) - 1

    def report(self, next_reservation=0):
        reporter = COST_REPORTER.get()
        if reporter:
            reporter(f"Estimated used: ${sum(c.get('settled_usd', 0) for c in self.data['calls']):.6f} | "
                     f"Next reservation: ${next_reservation:.6f} | "
                     f"Unresolved reservations: ${sum(c['reserved_usd'] for c in self.data['calls'] if 'settled_usd' not in c):.6f} | "
                     f"Model budget: ${self.limit:.2f}")

    def record(self, index, usage, headers=None, notify=True):
        row = self.data['calls'][index]
        row.update(status='response_received', usage=usage)
        details = usage.get('prompt_tokens_details') or {}
        prompt, output = usage.get('prompt_tokens'), usage.get('completion_tokens')
        cached, written = details.get('cached_tokens', 0), details.get('cache_write_tokens', 0)
        values = (prompt, output, cached, written)
        if (not all(type(v) is int and v >= 0 for v in values)
                or cached + written > prompt):
            row['settlement_note'] = 'Missing or invalid usage; reservation retained.'
            self.save()
            return
        write_1h = written if row.get('cache_ttl', '5m') == '1h' else 0
        if headers:
            try:
                five = int(headers['msh-usage-cache-write-tokens-5m'])
                hour = int(headers['msh-usage-cache-write-tokens-1h'])
                if five < 0 or hour < 0 or five + hour != written:
                    raise ValueError('Inconsistent cache write headers')
                write_1h = hour
                row['cache_write_headers'] = {'5m':five, '1h':hour}
            except KeyError:
                pass
            except (TypeError, ValueError):
                row['settlement_note'] = 'Invalid cache write headers; reservation retained.'
                self.save()
                return
        elif row.get('cache_write_headers'):
            write_1h = row['cache_write_headers']['1h']
        uncached = prompt - cached - written
        cost = (uncached * 3 + cached * .3 + (written-write_1h) * 3 + write_1h * 6 + output * 15) / 1_000_000
        row.update(settled_usd=cost, estimated_usd_default_cache=((prompt-cached)*3+cached*.3+output*15)/1_000_000,
                   released_usd=max(0, row['reserved_usd']-cost),
                   billing_tokens={'uncached':uncached, 'cache_read':cached,
                                   'cache_write_5m':written-write_1h, 'cache_write_1h':write_1h, 'output':output})
        row.pop('settlement_note', None)
        self.data['next_reservation_usd'] = 0
        self.save()
        if notify:
            self.report()
