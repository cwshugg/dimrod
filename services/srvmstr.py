#!/usr/bin/env python3
# This module services as the master of all services. It accepts a config file
# and launches all specified services as child processes. This program is what
# should be registered with systemd to launch all services automatically.
#
#   Connor Shugg

# Imports
import os
import sys
import subprocess

# Ensure imports from the file's directory are possible
fdir = os.path.dirname(os.path.realpath(__file__))
if fdir not in sys.path:
    sys.path.append(fdir)

# Local imports
import lib.config
import lib.service
import lib.oracle


# ================================= Configs ================================== #
# Represents what should be present in the configuration file passed into this
# program via command-line arguments.
class MasterConfig(lib.config.Config):
    def __init__(self):
        super().__init__()
        self.fields = [
            lib.config.ConfigField("services",      [list],     required=True)
        ]

# Represents what each entry within the "services" field should contain.
class MasterServiceConfig(lib.config.Config):
    def __init__(self):
        super().__init__()
        self.fields = [
            lib.config.ConfigField("executable",    [str],      required=True),
            lib.config.ConfigField("config",        [str],      required=True),
            lib.config.ConfigField("use_oracle",    [bool],     required=False,     default=False)
        ]


# =============================== Main Routine =============================== #
def main():
    # if no argument was given, assume the file name and location
    cpath = os.path.join(fdir, "srvmstr_config.json") 
    if len(sys.argv) > 1:
        cpath = sys.argv[1]

    # make sure the file given exists
    assert os.path.isfile(cpath), "the config file %s could not be found" % cpath

    # parse the main config
    mconfig = MasterConfig()
    mconfig.parse_file(cpath)

    # for each service within the main config, parse it as a MasterServiceConfig
    services = []
    for s in mconfig.services:
        msconfig = MasterServiceConfig()
        msconfig.parse_json(s)
        services.append(msconfig)

    # finally, for each service, launch it as a separate child process
    children = []
    for service in services:
        # build a list of arguments for each service, based on the entries read
        # from the config file
        args = [service.executable, "--config", service.config]
        if service.use_oracle:
            args.append("--oracle")
        
        # spawn a child process to run the service
        child = subprocess.Popen(args)
        children.append(child)

    # once all children are spawned, we'll wait for them to complete
    for child in children:
        child.wait()

# RUNNER CODE
if __name__ == "__main__":
    main()

