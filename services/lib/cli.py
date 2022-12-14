# Module that defines a simple command-line interface for services.

# Imports
import os
import sys
import argparse
import signal

# Enable import from the parent directory
pdir = os.path.dirname(os.path.dirname(os.path.realpath(__file__)))
if pdir not in sys.path:
    sys.path.append(pdir)

# Local imports
import lib.service
import lib.oracle

# Globals
config = None
service = None
oracle = None
exec_name = os.path.basename(sys.argv[0]).replace(".py", "")

# Pretty-printing globals
C_NONE = "\033[0m"
C_RED = "\033[31m"
C_GREEN = "\033[32m"

# ========================== Command-Line Interface ========================== #
class ServiceCLI:
    # Constructor. Takes in the service this CLI will manage.
    def __init__(self,
                 config=lib.service.ServiceConfig,
                 service=lib.service.Service,
                 oracle=lib.oracle.Oracle):
        self.config_class = config
        self.service_class = service
        self.oracle_class = oracle
        
        # ---------------------- Argument Parser Setup ----------------------- #
        # create an argument parser with a fitting description
        desc = "Interact with %s." % exec_name
        p = argparse.ArgumentParser(description=desc,
                                    formatter_class=argparse.RawDescriptionHelpFormatter)

        # add a number of arguments that all services will use
        p.add_argument("--config", metavar="CONFIG_JSON", required=True,
                       help="Takes in the path to your config file for %s." % exec_name,
                       default=None, nargs=1, type=str)
        p.add_argument("--oracle",
                       help="Enables the creation of an HTTP oracle to communicate with %s." % exec_name,
                       default=False, action="store_true")

        # save the parser to a class field
        self.parser = p
    
    # Runs the command-line interface. It parses all arguments then invokes the
    # service (and oracle, if applicable).
    def run(self):
        args = vars(self.parser.parse_args())

        # first, attempt to initialize the config object
        global config
        config_path = args["config"][0]
        try:
            config = self.config_class()
            config.parse_file(config_path)
        except Exception as e:
            self.panic("Failed to initialize %s" % self.config_class.__name__,
                       exception=e)
        
        # next, attempt to initialize the service
        global service
        try:
            service = self.service_class(config_path)
        except Exception as e:
            self.panic("Failed to initialize %s" % self.config_class.__name__,
                       exception=e)

        # finally, if specified, attempt to initialize the oracle
        if "oracle" in args and args["oracle"]:
            global oracle
            try:
                oracle = self.oracle_class(config_path, service)
            except Exception as e:
                self.panic("Failed to initialize %s" % self.config_class.__name__,
                           exception=e)

        # establish the SIGINT handler
        signal.signal(signal.SIGINT, self.sigint_handler)

        # now, run the service (and the oracle, if applicable)
        self.success("Initialized successfully. Starting %s%s." %
                     (config.service_name, " (and oracle)" if oracle else ""))
        service.start()
        if oracle:
            oracle.start()
            oracle.join()
        service.join()

    # ------------------------------- Helpers -------------------------------- #
    # Pretty-prints an error message and exits.
    def panic(self, msg, exception=None):
        prefix = "%s:" % exec_name if not config else "%s:" % config.service_name
        sys.stderr.write("%s%s%s %s\n" % (C_RED, prefix, C_NONE, msg))
        # raise the given exception or just exit
        if exception is not None:
            raise exception
        sys.exit(1)
    
    # Pretty-prints a success message.
    def success(self, msg):
        prefix = "%s:" % exec_name if not config else "%s:" % config.service_name
        sys.stderr.write("%s%s%s %s\n" % (C_GREEN, prefix, C_NONE, msg))

    # SIGINT handler.
    def sigint_handler(self, sig, frame):
        self.success("caught SIGINT. Exiting.")
        sys.exit(0)

