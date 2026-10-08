#!/usr/bin/env python3
"""Serve one checkout's Sage for the #702 evaluation, with the fixture as its only Dataset.

Run from that checkout's `backend/`, with its own venv:

    EVAL_FIXTURE_MOUNT=/path/to/mount/eval-orders SAGE_WORKSPACE_DIR=... SAGE_CONTROL_PORT=... \
        .venv/bin/python /path/to/scripts/eval/serve.py

A laptop has no Domino Dataset to attach from, and every Build attach resolves one. So the asset
provider is swapped for a single mounted Dataset over a local directory -- the same seam
`spikes/native-reasoning/production_probe.py` uses on the resources provider. Nothing else moves:
the app, the gateway, the routes and the attach path are that checkout's own, and the bytes reach
the app through `attach_file`'s mounted-Dataset branch exactly as on Domino.
"""
from __future__ import annotations

import os
import pathlib
import sys
from dataclasses import dataclass

sys.path.insert(0, str(pathlib.Path.cwd()))

from sage.assets.provider import Asset, FakeAssetProvider
from sage.orchestrator import app as appmod

DATASET_ID = "ds_eval-orders"


@dataclass
class FixtureAssets(FakeAssetProvider):
    def __post_init__(self) -> None:
        mount = pathlib.Path(os.environ["EVAL_FIXTURE_MOUNT"])
        self.root = mount.parent
        self.assets = [Asset(DATASET_ID, mount.name, mount_path=str(mount))]


appmod.orchestrator._assets = FixtureAssets()
appmod.run()
