"""Evidence from one changed preview document, never from the currently selected app."""
from dataclasses import dataclass, field


@dataclass
class PageValidation:
    id: str
    project_id: str
    app_id: str
    turn_id: str
    code_generation: str
    code_kind: str
    stages: dict[str, str] = field(default_factory=lambda: {
        'code': 'passed', 'startup': 'unverified', 'page': 'unverified',
        'runtime': 'unverified',
    })
    generation: str = ''
    acknowledged: bool = False
    closed: bool = False
    error: dict | None = None
    data_expected: bool = False
    dataset_ids: tuple[str, ...] = ()
    data_reads: list[dict] = field(default_factory=list)
    reads_truncated: bool = False
    # Every query name the page asked for, past the cap on `data_reads` too (#765).
    queries_read: set[str] = field(default_factory=set)
    query_failures: dict[str, str] = field(default_factory=dict)
    # What a failed query was sent and what it answered, by name, for the repair only (#735): it
    # holds request values, so it never joins `data_reads` or the summary.
    query_requests: dict[str, dict] = field(default_factory=dict)
    queries_declared: bool = False
    reason: str = ''
    # What each screen the headless check opened showed (#750), for the plan review only.
    screens: list[dict] = field(default_factory=list)

    def event(self) -> dict:
        return {'type': 'preview-validation', 'validationId': self.id,
                'projectId': self.project_id, 'appId': self.app_id,
                'turnId': self.turn_id, 'generation': self.generation}

    def summary(self) -> dict:
        outcomes = [read["outcome"] for read in self.data_reads]
        # An app with queries whose loaded page asked for none of them has not shown its data
        # working, whichever way it reads it (#730).
        unread = (self.queries_declared and self.stages["page"] == "passed"
                  and not self.reads_truncated
                  and not any(read["kind"] == "query" for read in self.data_reads))
        self.stages["data"] = (
            "failed" if "failed" in outcomes or unread else
            "unverified" if self.reads_truncated or "pending" in outcomes else
            "passed" if outcomes else
            "unverified" if self.data_expected else "not_applicable")
        states = self.stages.values()
        overall = ('failed' if 'failed' in states else
                   'unverified' if 'unverified' in states else 'passed')
        return {'overall': overall, 'validationId': self.id, 'generation': self.generation,
                'codeGeneration': self.code_generation, 'codeKind': self.code_kind,
                'stages': dict(self.stages), 'dataReads': [dict(read) for read in self.data_reads],
                'readsTruncated': self.reads_truncated,
                **({'queriesUnread': True} if unread else {}),
                **({'reason': self.reason} if overall == 'unverified' and self.reason else {})}
