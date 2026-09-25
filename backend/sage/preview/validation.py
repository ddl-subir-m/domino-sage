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
    query_failures: dict[str, str] = field(default_factory=dict)

    def event(self) -> dict:
        return {'type': 'preview-validation', 'validationId': self.id,
                'projectId': self.project_id, 'appId': self.app_id,
                'turnId': self.turn_id, 'generation': self.generation}

    def summary(self) -> dict:
        outcomes = [read["outcome"] for read in self.data_reads]
        self.stages["data"] = (
            "failed" if "failed" in outcomes else
            "unverified" if self.reads_truncated or "pending" in outcomes else
            "passed" if outcomes else
            "unverified" if self.data_expected else "not_applicable")
        states = self.stages.values()
        overall = ('failed' if 'failed' in states else
                   'unverified' if 'unverified' in states else 'passed')
        return {'overall': overall, 'validationId': self.id, 'generation': self.generation,
                'codeGeneration': self.code_generation, 'codeKind': self.code_kind,
                'stages': dict(self.stages), 'dataReads': [dict(read) for read in self.data_reads],
                'readsTruncated': self.reads_truncated}
