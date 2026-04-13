# Tests for the Config class's YAML auto-detection support.
#
# These tests verify that Config correctly auto-detects JSON and YAML config
# files based on their file extension, and that the parsed result is identical
# regardless of the source format.

import os
import sys
import json
import yaml
import tempfile
import unittest
from datetime import datetime
from enum import Enum

# Enable imports from the services directory
sdir = os.path.dirname(os.path.dirname(os.path.dirname(os.path.realpath(__file__))))
if sdir not in sys.path:
    sys.path.append(sdir)

from lib.config import Config, ConfigField
from lib.uniserdes import UniserdesField


# ========================== Test Helper Classes ========================== #

class TestConfig(Config):
    """A simple Config subclass for testing."""
    def __init__(self):
        super().__init__()
        self.fields = [
            ConfigField("host", [str], required=True),
            ConfigField("port", [int], required=True),
            ConfigField("debug", [bool], required=False, default=False),
            ConfigField("name", [str], required=False, default="default"),
        ]
        self.init_defaults()


# ============================== Test Cases =============================== #

class TestConfigJsonFile(unittest.TestCase):
    """Tests that JSON config files still work correctly."""

    def test_parse_json_file(self):
        """Config should correctly parse a .json config file."""
        data = {"host": "localhost", "port": 8080, "debug": True}
        with tempfile.NamedTemporaryFile(
            mode="w", suffix=".json", delete=False
        ) as f:
            json.dump(data, f)
            fpath = f.name

        try:
            cfg = TestConfig.from_file(fpath)
            self.assertEqual(cfg.host, "localhost")
            self.assertEqual(cfg.port, 8080)
            self.assertTrue(cfg.debug)
            self.assertEqual(cfg.name, "default")
        finally:
            os.unlink(fpath)

    def test_json_defaults_applied(self):
        """Missing optional fields in a JSON config should get defaults."""
        data = {"host": "0.0.0.0", "port": 3000}
        with tempfile.NamedTemporaryFile(
            mode="w", suffix=".json", delete=False
        ) as f:
            json.dump(data, f)
            fpath = f.name

        try:
            cfg = TestConfig.from_file(fpath)
            self.assertEqual(cfg.host, "0.0.0.0")
            self.assertEqual(cfg.port, 3000)
            self.assertFalse(cfg.debug)
            self.assertEqual(cfg.name, "default")
        finally:
            os.unlink(fpath)


class TestConfigYamlFile(unittest.TestCase):
    """Tests that YAML config files are correctly auto-detected and parsed."""

    def test_parse_yaml_file(self):
        """Config should correctly parse a .yaml config file."""
        yaml_content = "host: 192.168.1.1\nport: 9090\ndebug: true\nname: yaml_test\n"
        with tempfile.NamedTemporaryFile(
            mode="w", suffix=".yaml", delete=False
        ) as f:
            f.write(yaml_content)
            fpath = f.name

        try:
            cfg = TestConfig.from_file(fpath)
            self.assertEqual(cfg.host, "192.168.1.1")
            self.assertEqual(cfg.port, 9090)
            self.assertTrue(cfg.debug)
            self.assertEqual(cfg.name, "yaml_test")
        finally:
            os.unlink(fpath)

    def test_parse_yml_file(self):
        """Config should correctly parse a .yml config file."""
        yaml_content = "host: example.com\nport: 443\n"
        with tempfile.NamedTemporaryFile(
            mode="w", suffix=".yml", delete=False
        ) as f:
            f.write(yaml_content)
            fpath = f.name

        try:
            cfg = TestConfig.from_file(fpath)
            self.assertEqual(cfg.host, "example.com")
            self.assertEqual(cfg.port, 443)
            self.assertFalse(cfg.debug)
            self.assertEqual(cfg.name, "default")
        finally:
            os.unlink(fpath)

    def test_yaml_defaults_applied(self):
        """Missing optional fields in a YAML config should get defaults."""
        yaml_content = "host: localhost\nport: 5000\n"
        with tempfile.NamedTemporaryFile(
            mode="w", suffix=".yaml", delete=False
        ) as f:
            f.write(yaml_content)
            fpath = f.name

        try:
            cfg = TestConfig.from_file(fpath)
            self.assertEqual(cfg.host, "localhost")
            self.assertEqual(cfg.port, 5000)
            self.assertFalse(cfg.debug)
            self.assertEqual(cfg.name, "default")
        finally:
            os.unlink(fpath)


class TestConfigFormatEquivalence(unittest.TestCase):
    """Tests that JSON and YAML configs produce identical results."""

    def test_json_and_yaml_produce_same_result(self):
        """A JSON config and a YAML config with the same data should produce
        identical Config objects."""
        data = {
            "host": "10.0.0.1",
            "port": 7777,
            "debug": True,
            "name": "equivalence_test",
        }

        # write JSON config
        with tempfile.NamedTemporaryFile(
            mode="w", suffix=".json", delete=False
        ) as f:
            json.dump(data, f)
            json_path = f.name

        # write YAML config with the same data
        with tempfile.NamedTemporaryFile(
            mode="w", suffix=".yaml", delete=False
        ) as f:
            yaml.dump(data, f, default_flow_style=False)
            yaml_path = f.name

        try:
            json_cfg = TestConfig.from_file(json_path)
            yaml_cfg = TestConfig.from_file(yaml_path)

            self.assertEqual(json_cfg.host, yaml_cfg.host)
            self.assertEqual(json_cfg.port, yaml_cfg.port)
            self.assertEqual(json_cfg.debug, yaml_cfg.debug)
            self.assertEqual(json_cfg.name, yaml_cfg.name)

            # verify that to_json() output is identical
            self.assertEqual(json_cfg.to_json(), yaml_cfg.to_json())
        finally:
            os.unlink(json_path)
            os.unlink(yaml_path)

    def test_json_and_yml_produce_same_result(self):
        """A JSON config and a .yml config with the same data should produce
        identical Config objects."""
        data = {
            "host": "db.server.local",
            "port": 5432,
            "debug": False,
            "name": "yml_test",
        }

        # write JSON config
        with tempfile.NamedTemporaryFile(
            mode="w", suffix=".json", delete=False
        ) as f:
            json.dump(data, f)
            json_path = f.name

        # write .yml config
        with tempfile.NamedTemporaryFile(
            mode="w", suffix=".yml", delete=False
        ) as f:
            yaml.dump(data, f, default_flow_style=False)
            yml_path = f.name

        try:
            json_cfg = TestConfig.from_file(json_path)
            yml_cfg = TestConfig.from_file(yml_path)
            self.assertEqual(json_cfg.to_json(), yml_cfg.to_json())
        finally:
            os.unlink(json_path)
            os.unlink(yml_path)


class TestConfigFpathSet(unittest.TestCase):
    """Tests that fpath is correctly set after parsing."""

    def test_fpath_set_for_json(self):
        """fpath should be set after parsing a JSON config file."""
        data = {"host": "localhost", "port": 80}
        with tempfile.NamedTemporaryFile(
            mode="w", suffix=".json", delete=False
        ) as f:
            json.dump(data, f)
            fpath = f.name

        try:
            cfg = TestConfig.from_file(fpath)
            self.assertEqual(cfg.fpath, fpath)
        finally:
            os.unlink(fpath)

    def test_fpath_set_for_yaml(self):
        """fpath should be set after parsing a YAML config file."""
        yaml_content = "host: localhost\nport: 80\n"
        with tempfile.NamedTemporaryFile(
            mode="w", suffix=".yaml", delete=False
        ) as f:
            f.write(yaml_content)
            fpath = f.name

        try:
            cfg = TestConfig.from_file(fpath)
            self.assertEqual(cfg.fpath, fpath)
        finally:
            os.unlink(fpath)


if __name__ == "__main__":
    unittest.main()
