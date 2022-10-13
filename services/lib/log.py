# A simple class that defines a logging mechanism, for use by services and their
# oracles.
#
#   Connor Shugg

# Imports
import os
import sys

# Enable import from the parent directory
pdir = os.path.dirname(os.path.dirname(os.path.realpath(__file__)))
if pdir not in sys.path:
    sys.path.append(pdir)

# Log class
class Log:
    # Constructor. Requires a name for the log.
    def __init__(self, name, stream=sys.stdout):
        self.name = name

        # check the log stream. If it's a string, we'll parse it and open a file
        self.stream = stream
        if type(stream) == str:
            # special case 1: stdout
            if stream == "1" or stream.lower() == "stdout":
                self.stream = sys.stdout
            # special case 2: stderr
            if stream == "2" or stream.lower() == "stderr":
                self.stream = sys.stderr
            # default case: treat it as a file path
            else:
                self.stream = stream
                # if the file doesn't exist, create it
                if not os.path.isfile(stream):
                    fp = open(stream, "w")
                    fp.close()

    # Writes a new line to the log with the given message.
    def write(self, msg):
        # if the stream is stored as a string, we'll interpret it as a file path
        stream = self.stream
        is_file = type(self.stream) == str
        if is_file:
            stream = open(stream, "a")
        # write the message
        stream.write("[%s] %s\n" % (self.name, msg))
        if is_file:
            stream.close()

