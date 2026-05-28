from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from bankofai.x402_gateway.cli.templates import write_provider_template


def test_template_writes_valid_yaml(tmp_path: Path) -> None:
    target = write_provider_template(
        output_dir=tmp_path,
        name="my-api",
        forward_url="https://upstream.example",
        network="tron-shasta",
    )
    parsed = yaml.safe_load(target.read_text())
    assert parsed["name"] == "my-api"
    assert parsed["forward_url"] == "https://upstream.example"
    assert parsed["operator"]["network"] == "tron-shasta"


def test_template_refuses_overwrite_by_default(tmp_path: Path) -> None:
    write_provider_template(
        output_dir=tmp_path, name="a", forward_url="https://u", network="tron-shasta"
    )
    with pytest.raises(FileExistsError):
        write_provider_template(
            output_dir=tmp_path,
            name="a",
            forward_url="https://u",
            network="tron-shasta",
        )
