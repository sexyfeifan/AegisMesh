import argparse
import json
import tempfile
import unittest
from pathlib import Path

import yaml

import vpn_obfuscator as core


class RegressionTests(unittest.TestCase):
    def setUp(self) -> None:
        self.repo_root = Path(__file__).resolve().parents[1]
        self.tempdir = tempfile.TemporaryDirectory(prefix="aegismesh_tests_")
        self.work = Path(self.tempdir.name)
        self.mapping_dir = self.work / "mapping"

    def tearDown(self) -> None:
        self.tempdir.cleanup()

    def _encode(self, input_file: Path, profile: str, inject_nid: bool = False) -> Path:
        out = self.work / f"{profile}.encoded"
        args = argparse.Namespace(
            input_url=None,
            input_file=input_file,
            input_text=None,
            output=out,
            profile=profile,
            mapping_dir=self.mapping_dir,
            fake_suffix="mask.invalid",
            strict=True,
            insecure=False,
            ca_file=None,
            inject_nid=inject_nid,
        )
        rc = core.encode_action(args)
        self.assertEqual(rc, 0)
        return out

    def _decode(self, converted_file: Path, profile: str) -> Path:
        out = self.work / f"{profile}.restored"
        args = argparse.Namespace(
            input_file=converted_file,
            output=out,
            profile=profile,
            mapping_dir=self.mapping_dir,
            strict=True,
        )
        rc = core.decode_action(args)
        self.assertEqual(rc, 0)
        return out

    def test_uri_round_trip(self) -> None:
        src = self.repo_root / "sample_uri.txt"
        encoded = self._encode(src, "uri")
        restored = self._decode(encoded, "uri")
        self.assertEqual(src.read_text(encoding="utf-8"), restored.read_text(encoding="utf-8"))

    def test_base64_uri_round_trip(self) -> None:
        src = self.repo_root / "sample_uri_base64.txt"
        encoded = self._encode(src, "b64")
        restored = self._decode(encoded, "b64")
        self.assertEqual(src.read_text(encoding="utf-8").strip(), restored.read_text(encoding="utf-8").strip())

    def test_clash_yaml_round_trip_semantic(self) -> None:
        src = self.repo_root / "sample_clash.yaml"
        encoded = self._encode(src, "yaml")
        restored = self._decode(encoded, "yaml")
        self.assertEqual(
            yaml.safe_load(src.read_text(encoding="utf-8")),
            yaml.safe_load(restored.read_text(encoding="utf-8")),
        )

    def test_decode_accepts_real_endpoint_fallback(self) -> None:
        src = self.repo_root / "sample_clash.yaml"
        profile = "real_fallback"
        encoded = self._encode(src, profile)

        mapping_path = self.mapping_dir / f"{profile}.mapping.json"
        mapping = json.loads(mapping_path.read_text(encoding="utf-8"))
        records = mapping["records"]

        converted_obj = yaml.safe_load(encoded.read_text(encoding="utf-8"))
        proxies = converted_obj["proxies"]
        for idx, proxy in enumerate(proxies):
            rec = records[idx]
            proxy["server"] = rec["real_host"]
            proxy["port"] = int(rec["real_port"])

        converted_file = self.work / "converted_real_endpoint.yaml"
        converted_file.write_text(yaml.safe_dump(converted_obj, allow_unicode=True, sort_keys=False), encoding="utf-8")
        restored = self._decode(converted_file, profile)

        self.assertEqual(
            yaml.safe_load(src.read_text(encoding="utf-8")),
            yaml.safe_load(restored.read_text(encoding="utf-8")),
        )


if __name__ == "__main__":
    unittest.main()
