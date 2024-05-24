# Implements the mode base class and an "empty" mode.

# Imports
import time

# Base class for all modes.
class Mode:
    def __init__(self, service, priority=None):
        self.service = service
        self.config = self.service.config
        self.name = "base"
        self.tick_rate = 60
        self.priority_override = priority
    
    def __str__(self):
        return self.name
    
    # Helper function for writing mode-specific log messages. This piggybacks
    # off of 'self.service.log.write()'.
    def log(self, msg: str):
        prefix = "[mode: %s]" % self.name
        self.service.log.write("%s %s" % (prefix, msg))
    
    # Checks any desired conditions and returns a number, either 0, or a
    # positive, non-zero integer. This number represents the mode's priority
    # in terms of how badly it wants to run.
    #
    #   - 0 means the mode doesn't want to run.
    #   - 1 means the mode wants to run at the lowest priority
    #   - 2 means the mode wants to run at a slightly higher priority
    #   - ...
    #   - And so on.
    #
    # When the Moder service iterates over all possible modes, it will run this
    # to determine which mode has the highest priority. The mode reporting the
    # highest priority will be run. When there is a tie, the first mode found
    # with the highest/tieing priority value will be run. If the currently-
    # -running mode has the same priority as the higher mode that wants to run,
    # the current mode will continue running.
    def priority(self):
        if self.priority_override is not None:
            return self.priority_override
        return 0
    
    # Sleeps for the mode's tick rate.
    def sleep(self):
        time.sleep(self.tick_rate)

    # Returns whether or not the mode is finished.
    def is_complete(self):
        return False
    
    # Runs the mode's step routine. This is where all main logic should be
    # implemented.
    def step(self):
        pass
    
    # Runs just before the mode is disabled. Useful for implementing cleanup
    # routines.
    def cleanup(self):
        pass

# Normal mode. Nothing goes on here, and this is the default.
class Mode_Empty(Mode):
    def __init__(self, service, priority=None):
        super().__init__(service, priority=priority)
        self.name = "empty"

    def priority(self):
        if self.priority_override is not None:
            return self.priority_override
        # the empty mode always wants to run, but at the lowest priority
        return 1

    def step(self):
        # DO NOTHING
        pass

