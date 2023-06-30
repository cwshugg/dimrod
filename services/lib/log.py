# A simple class that defines a logging mechanism, for use by services and their
# oracles.
#
#   Connor Shugg

# Imports
import os
import sys
from datetime import datetime

# Enable import from the parent directory
pdir = os.path.dirname(os.path.dirname(os.path.realpath(__file__)))
if pdir not in sys.path:
    sys.path.append(pdir)

# Log class
class Log:
    # Constructor. Requires a name for the log.
    #   stream      can be either sys.stdout, sys.stderr, or a file path
    def __init__(self, name, stream=sys.stdout):
        self.name = name

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
        # rent a file descriptor, write the object, then return it
        stream = self.rent_fd()
        dtstr = datetime.now().strftime("%Y-%m-%d %I:%M:%S %p")
        stream.write("%s[%s - %s] %s%s" % (begin, dtstr, self.name, msg, end))
        self.return_fd(stream)
    
    # Retrieves a file descriptor that's "rented" by the caller for a brief
    # period of time. Once the caller is done writing to the file, it must
    # then call return_fd() to close it properly.
    def rent_fd(self):
        is_file = type(self.stream) == str
        stream = self.stream
        if is_file:
            stream = open(self.stream, "a")
        return stream
    
    # Takes in the FD returned by rent_fd() and closes it properly.
    def return_fd(self, fd):
        is_file = type(self.stream) == str
        if is_file:
            fd.close()

