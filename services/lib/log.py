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
    #   stream      can be either sys.stdout, sys.stderr, or a file path
    #   limit       defines the maximum number of lines that can be written to a
    #               file before its contents are steadily replaced (to save
    #               space)
    def __init__(self, name, stream=sys.stdout, limit=2048):
        self.name = name
        self.line_limit = limit
        self.line_count = 0

        # check the log stream. If it's a string, we'll parse it and open a file
        self.stream = stream
        if type(stream) == str:
            # special case 1: stdout
            if stream == "1" or stream.lower() == "stdout":
                self.stream = sys.stdout
            # special case 2: stderr
            elif stream == "2" or stream.lower() == "stderr":
                self.stream = sys.stderr
            # default case: treat it as a file path
            else:
                self.stream = stream
                # if the file doesn't exist, create it
                if not os.path.isfile(stream) and \
                   stream != sys.stdout and \
                   stream != sys.stderr:
                    fp = open(stream, "w")
                    fp.close()

    # Writes a new line to the log with the given message.
    def write(self, msg, begin="", end="\n"):
        # if the stream is a string, we'll interpret it as a file name
        is_file = type(self.stream) == str
        stream = self.stream
        if is_file:
            # if we've reached the line limit, reset the file's contents
            if self.line_count == self.line_limit:
                stream = open(self.stream, "w")
                self.line_count = 0
            else:
                stream = open(self.stream, "a")


        # write the message
        stream.write("%s[%s] %s%s" % (begin, self.name, msg, end))
        if is_file:
            stream.close()
        self.line_count += 1

