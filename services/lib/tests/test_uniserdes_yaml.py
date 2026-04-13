# Tests for the YAML serialization/deserialization support added to the
# Uniserdes class.
#
# These tests verify that `to_yaml()`, `parse_yaml()`, and `from_yaml()` work
# correctly for simple fields, nested objects, enums, datetimes, lists, and
# optional/default fields. They also verify that the existing JSON methods
# remain unaffected.

import os
import sys
import json
import yaml
import unittest
from datetime import datetime
from enum import Enum

# Enable imports from the services directory
sdir = os.path.dirname(os.path.dirname(os.path.dirname(os.path.realpath(__file__))))
if sdir not in sys.path:
    sys.path.append(sdir)

from lib.uniserdes import Uniserdes, UniserdesField


# ========================== Test Helper Classes ========================== #

class Color(Enum):
    """A simple enum for testing enum serialization."""
    RED = 1
    GREEN = 2
    BLUE = 3


class SimpleObject(Uniserdes):
    """A simple Uniserdes subclass with basic fields."""
    def __init__(self):
        super().__init__()
        self.fields = [
            UniserdesField("name", [str], required=True),
            UniserdesField("count", [int], required=True),
            UniserdesField("ratio", [float], required=False, default=0.0),
            UniserdesField("active", [bool], required=False, default=True),
        ]
        self.init_defaults()


class NestedChild(Uniserdes):
    """A nested Uniserdes subclass (used as a child within another object)."""
    def __init__(self):
        super().__init__()
        self.fields = [
            UniserdesField("label", [str], required=True),
            UniserdesField("value", [int], required=False, default=0),
        ]
        self.init_defaults()


class ParentObject(Uniserdes):
    """A parent Uniserdes subclass that contains a nested NestedChild object and

    a list of NestedChild objects.
    """
    def __init__(self):
        super().__init__()
        self.fields = [
            UniserdesField("title", [str], required=True),
            UniserdesField("child", [NestedChild], required=True),
            UniserdesField("children", [NestedChild], required=False, default=[]),
        ]
        self.init_defaults()


class EnumObject(Uniserdes):
    """A Uniserdes subclass with an enum field."""
    def __init__(self):
        super().__init__()
        self.fields = [
            UniserdesField("name", [str], required=True),
            UniserdesField("color", [Color], required=True),
        ]
        self.init_defaults()


class DatetimeObject(Uniserdes):
    """A Uniserdes subclass with a datetime field."""
    def __init__(self):
        super().__init__()
        self.fields = [
            UniserdesField("event", [str], required=True),
            UniserdesField("timestamp", [datetime], required=True),
        ]
        self.init_defaults()


class OptionalObject(Uniserdes):
    """A Uniserdes subclass with only optional fields."""
    def __init__(self):
        super().__init__()
        self.fields = [
            UniserdesField("tag", [str], required=False, default="default_tag"),
            UniserdesField("priority", [int], required=False, default=5),
        ]
        self.init_defaults()


# ============================== Test Cases =============================== #

class TestUniserdesYamlSimple(unittest.TestCase):
    """Tests for to_yaml/from_yaml with simple field types."""

    def test_to_yaml_returns_string(self):
        """to_yaml() should return a string."""
        obj = SimpleObject.from_json({
            "name": "test",
            "count": 42,
        })
        result = obj.to_yaml()
        self.assertIsInstance(result, str)

    def test_to_yaml_valid_yaml(self):
        """to_yaml() output should be parseable by yaml.safe_load()."""
        obj = SimpleObject.from_json({
            "name": "test",
            "count": 42,
            "ratio": 3.14,
            "active": False,
        })
        yaml_str = obj.to_yaml()
        parsed = yaml.safe_load(yaml_str)
        self.assertIsInstance(parsed, dict)

    def test_to_yaml_content_matches_to_json(self):
        """to_yaml() should produce the same data structure as to_json()."""
        obj = SimpleObject.from_json({
            "name": "hello",
            "count": 100,
            "ratio": 2.71,
            "active": True,
        })
        json_dict = obj.to_json()
        yaml_dict = yaml.safe_load(obj.to_yaml())
        self.assertEqual(json_dict, yaml_dict)

    def test_from_yaml_simple(self):
        """from_yaml() should correctly parse a simple YAML string."""
        yaml_str = "name: test_item\ncount: 7\nratio: 1.5\nactive: true\n"
        obj = SimpleObject.from_yaml(yaml_str)
        self.assertEqual(obj.name, "test_item")
        self.assertEqual(obj.count, 7)
        self.assertEqual(obj.ratio, 1.5)
        self.assertTrue(obj.active)

    def test_round_trip(self):
        """An object should survive a to_yaml → from_yaml round trip."""
        original = SimpleObject.from_json({
            "name": "round_trip",
            "count": 99,
            "ratio": 0.5,
            "active": False,
        })
        yaml_str = original.to_yaml()
        restored = SimpleObject.from_yaml(yaml_str)
        self.assertEqual(original.name, restored.name)
        self.assertEqual(original.count, restored.count)
        self.assertEqual(original.ratio, restored.ratio)
        self.assertEqual(original.active, restored.active)

    def test_defaults_applied(self):
        """from_yaml() should apply defaults for missing optional fields."""
        yaml_str = "name: minimal\ncount: 1\n"
        obj = SimpleObject.from_yaml(yaml_str)
        self.assertEqual(obj.name, "minimal")
        self.assertEqual(obj.count, 1)
        self.assertEqual(obj.ratio, 0.0)
        self.assertTrue(obj.active)


class TestUniserdesYamlNested(unittest.TestCase):
    """Tests for to_yaml/from_yaml with nested Uniserdes objects."""

    def test_nested_single_object(self):
        """A nested Uniserdes object should survive a YAML round trip."""
        original = ParentObject.from_json({
            "title": "parent",
            "child": {"label": "child1", "value": 10},
        })
        yaml_str = original.to_yaml()
        restored = ParentObject.from_yaml(yaml_str)
        self.assertEqual(restored.title, "parent")
        self.assertIsInstance(restored.child, NestedChild)
        self.assertEqual(restored.child.label, "child1")
        self.assertEqual(restored.child.value, 10)

    def test_nested_list_of_objects(self):
        """A list of nested Uniserdes objects should survive a YAML round trip."""
        original = ParentObject.from_json({
            "title": "parent_with_list",
            "child": {"label": "main", "value": 1},
            "children": [
                {"label": "a", "value": 2},
                {"label": "b", "value": 3},
                {"label": "c", "value": 4},
            ],
        })
        yaml_str = original.to_yaml()
        restored = ParentObject.from_yaml(yaml_str)
        self.assertEqual(len(restored.children), 3)
        self.assertEqual(restored.children[0].label, "a")
        self.assertEqual(restored.children[1].value, 3)
        self.assertEqual(restored.children[2].label, "c")

    def test_nested_content_matches_json(self):
        """YAML serialization of nested objects should match JSON output."""
        obj = ParentObject.from_json({
            "title": "compare",
            "child": {"label": "x", "value": 5},
            "children": [{"label": "y", "value": 6}],
        })
        json_dict = obj.to_json()
        yaml_dict = yaml.safe_load(obj.to_yaml())
        self.assertEqual(json_dict, yaml_dict)


class TestUniserdesYamlEnum(unittest.TestCase):
    """Tests for to_yaml/from_yaml with enum fields."""

    def test_enum_round_trip_integer(self):
        """An enum serialized as an integer should survive a YAML round trip."""
        original = EnumObject.from_json({
            "name": "red_thing",
            "color": 1,
        })
        self.assertEqual(original.color, Color.RED)
        yaml_str = original.to_yaml()
        restored = EnumObject.from_yaml(yaml_str)
        self.assertEqual(restored.color, Color.RED)

    def test_enum_from_yaml_string(self):
        """An enum specified as a string in YAML should be parsed correctly."""
        yaml_str = "name: green_thing\ncolor: GREEN\n"
        obj = EnumObject.from_yaml(yaml_str)
        self.assertEqual(obj.color, Color.GREEN)

    def test_enum_to_yaml_as_integer(self):
        """to_yaml() should serialize enums as their integer value."""
        obj = EnumObject.from_json({"name": "blue_thing", "color": 3})
        yaml_dict = yaml.safe_load(obj.to_yaml())
        self.assertEqual(yaml_dict["color"], 3)


class TestUniserdesYamlDatetime(unittest.TestCase):
    """Tests for to_yaml/from_yaml with datetime fields."""

    def test_datetime_round_trip(self):
        """A datetime field should survive a YAML round trip."""
        dt = datetime(2025, 6, 15, 12, 30, 45)
        original = DatetimeObject.from_json({
            "event": "launch",
            "timestamp": dt.isoformat(),
        })
        self.assertEqual(original.timestamp, dt)

        yaml_str = original.to_yaml()
        restored = DatetimeObject.from_yaml(yaml_str)
        self.assertEqual(restored.timestamp, dt)
        self.assertEqual(restored.event, "launch")

    def test_datetime_to_yaml_as_iso_string(self):
        """to_yaml() should serialize datetimes as ISO strings."""
        dt = datetime(2024, 1, 1, 0, 0, 0)
        obj = DatetimeObject.from_json({
            "event": "new_year",
            "timestamp": dt.isoformat(),
        })
        yaml_dict = yaml.safe_load(obj.to_yaml())
        self.assertEqual(yaml_dict["timestamp"], dt.isoformat())


class TestUniserdesYamlOptionalAndExtra(unittest.TestCase):
    """Tests for optional/default fields and extra fields in YAML."""

    def test_optional_defaults(self):
        """Optional fields not present in YAML should get their defaults."""
        yaml_str = "{}\n"
        obj = OptionalObject.from_yaml(yaml_str)
        self.assertEqual(obj.tag, "default_tag")
        self.assertEqual(obj.priority, 5)

    def test_optional_overridden(self):
        """Optional fields present in YAML should override defaults."""
        yaml_str = "tag: custom\npriority: 1\n"
        obj = OptionalObject.from_yaml(yaml_str)
        self.assertEqual(obj.tag, "custom")
        self.assertEqual(obj.priority, 1)

    def test_extra_fields_preserved(self):
        """Extra fields in YAML (not defined in the schema) should be preserved."""
        yaml_str = "name: test\ncount: 5\nextra_key: extra_value\n"
        obj = SimpleObject.from_yaml(yaml_str)
        self.assertEqual(obj.name, "test")
        self.assertTrue(hasattr(obj, "extra_fields"))
        self.assertIn("extra_key", obj.extra_fields)
        self.assertEqual(obj.extra_key, "extra_value")


class TestUniserdesYamlParseMethod(unittest.TestCase):
    """Tests for the parse_yaml() instance method."""

    def test_parse_yaml_instance_method(self):
        """parse_yaml() should populate an existing object's fields."""
        obj = SimpleObject()
        obj.parse_yaml("name: parsed\ncount: 33\n")
        self.assertEqual(obj.name, "parsed")
        self.assertEqual(obj.count, 33)

    def test_parse_yaml_reparse(self):
        """parse_yaml() should allow re-parsing an object with new data."""
        obj = SimpleObject()
        obj.parse_yaml("name: first\ncount: 1\n")
        self.assertEqual(obj.name, "first")
        obj.parse_yaml("name: second\ncount: 2\n")
        self.assertEqual(obj.name, "second")
        self.assertEqual(obj.count, 2)


class TestExistingJsonUnchanged(unittest.TestCase):
    """Tests to verify that the existing JSON methods are still functional."""

    def test_to_json_still_works(self):
        """to_json() should still return a dictionary."""
        obj = SimpleObject.from_json({"name": "json_test", "count": 10})
        jdata = obj.to_json()
        self.assertIsInstance(jdata, dict)
        self.assertEqual(jdata["name"], "json_test")
        self.assertEqual(jdata["count"], 10)

    def test_from_json_still_works(self):
        """from_json() should still create objects correctly."""
        obj = SimpleObject.from_json({"name": "json_test2", "count": 20})
        self.assertEqual(obj.name, "json_test2")
        self.assertEqual(obj.count, 20)

    def test_json_round_trip_still_works(self):
        """JSON round trip (to_json → from_json) should still work."""
        original = SimpleObject.from_json({
            "name": "json_rt",
            "count": 77,
            "ratio": 1.23,
            "active": False,
        })
        restored = SimpleObject.from_json(original.to_json())
        self.assertEqual(original.name, restored.name)
        self.assertEqual(original.count, restored.count)
        self.assertEqual(original.ratio, restored.ratio)
        self.assertEqual(original.active, restored.active)

    def test_to_bytes_still_works(self):
        """to_bytes() should still encode the object as JSON bytes."""
        obj = SimpleObject.from_json({"name": "bytes_test", "count": 5})
        b = obj.to_bytes()
        self.assertIsInstance(b, bytes)
        decoded = json.loads(b.decode("utf-8"))
        self.assertEqual(decoded["name"], "bytes_test")

    def test_str_still_works(self):
        """__str__() should still return a formatted JSON string."""
        obj = SimpleObject.from_json({"name": "str_test", "count": 3})
        s = str(obj)
        parsed = json.loads(s)
        self.assertEqual(parsed["name"], "str_test")


if __name__ == "__main__":
    unittest.main()
