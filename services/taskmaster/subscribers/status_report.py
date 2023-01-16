#!/usr/bin/env python3
# This routine performs a status check of the home server and the machine, then
# summarizes it into an email and sends it.

# Imports
import os
import sys
import json
import subprocess
from datetime import datetime

# Enable import from the main directory
pdir = os.path.dirname(os.path.dirname(os.path.dirname(os.path.realpath(__file__))))
if pdir not in sys.path:
    sys.path.append(pdir)

# Local imports
from lib.mail import MessengerConfig, Messenger

# Globals
config_name = "cwshugg_mail_config.json"
config_path = os.path.join(os.path.dirname(os.path.realpath(__file__)), config_name)

# Runs a command and returns the stdout.
def run_command(args):
    result = subprocess.run(args, check=False, capture_output=True)
    return result.stdout.decode()

# Returns a string containing various machine status information.
def get_machine_status():
    msg = "<ul>"

    # get the system uptime
    msg += "<li><b>Uptime:</b> %s</li>" % run_command(["uptime", "-p"])

    # get system load average
    msg += "<li><b>Load Average:</b> %s</li>" % run_command(["cat", "/proc/loadavg"])

    # get the CPU temperature
    temp = run_command(["cat", "/sys/class/thermal/thermal_zone2/temp"])
    temp = float(((int(temp) / 1000.0) * 1.8) + 32.0)
    msg += "<li><b>CPU Temperature:</b> %.2f°F</li>" % temp

    # get memory usage
    msg += "<li><b>Memory Usage:</b>"
    memdata = run_command(["cat", "/proc/meminfo"]).split("\n")
    memdict = {}
    for line in memdata:
        pieces = line.strip().split(":")
        key = pieces[0].strip()
        value = "".join(pieces[1:]).strip()
        memdict[key] = value
    msg += "<ul>"
    for key in ["MemTotal", "MemAvailable"]:
        msg += "<li>%s: %s</li>" % (key, memdict[key])
    msg += "</ul>"
    msg += "</li>"

    # get process count
    procs = run_command(["ps", "-aux"]).split("\n")
    msg += "<li><b>Processes running:</b> %d</li>" % len(procs)

    msg += "</ul>"
    return msg

# Returns a string containing dimrod service status information.
def get_service_status():
    msg = "<p>"
    status = run_command(["systemctl", "status", "dimrod_sm.service"]).split("\n")
    for line in status:
        # stop once we reach the first empty line
        if len(line.strip()) == 0:
            break
        msg += "%s<br>" % line
    msg += "</p>"
    return msg

# Main function.
def main():
    # check command-line arguments and attempt to parse as JSON
    data = {}
    if len(sys.argv) > 1:
        data = json.loads(sys.argv[1])

    # look for a list of emails to send the status report to
    emails = data["emails"]
    if type(emails) == str:
        emails = [emails]

    print("Generating status report...")

    # gather system information
    msg = "<h1>DImROD Status Report</h1>"
    msg += "<h2>Machine Status</h2>"
    msg += get_machine_status()

    # gather information on the dimrod services
    msg += "<h2>Service Status</h2>"
    msg += get_service_status()

    # create a subject line
    subject_date = datetime.now().strftime("%Y-%m-%d - %I:%M %p")
    subject = "DImROD Status Report - %s" % subject_date
    
    # create a messenger object and send emails
    mconf = MessengerConfig()
    mconf.parse_file(config_path)
    m = Messenger(mconf)
    print("Sending status report to the following emails:")
    for addr in emails:
        m.send(addr, subject, msg)
        print(" - %s" % addr)

# Runner code
if __name__ == "__main__":
    sys.exit(main())

