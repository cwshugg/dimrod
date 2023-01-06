#!/usr/bin/env python3
# This represents a simple, example subscriber script that can be called by
# Herald. Herald will pass a JSON string of data to a subscriber as the second
# command-line argument.

# Imports
import sys
import json

# Main function.
def main():
    # check command-line arguments and attempt to parse as JSON
    data = {}
    if len(sys.argv) > 1:
        data = json.loads(sys.argv[1])

    print("Test subscriber executing with data: %s" % data)
    return 0

# Runner code
if __name__ == "__main__":
    sys.exit(main())

