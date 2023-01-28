# Implements the /system bot command.

# Imports
import os
import sys
import subprocess
import re

# Enable import from the parent directory
pdir = os.path.dirname(os.path.dirname(os.path.dirname(__file__)))
if pdir not in sys.path:
    sys.path.append(pdir)


# ================================= Helpers ================================== #
# Runs a command and returns the stdout.
def run_command(args):
    result = subprocess.run(args, check=False, capture_output=True)
    return result.stdout.decode()

# Gets the system uptime.
def get_uptime():
    ut = run_command(["uptime", "-p"]).replace("\n", "")
    ut = ut.replace("up", "")
    ut = ut.replace("weeks", "w")
    ut = ut.replace("week", "w")
    ut = ut.replace("days", "d")
    ut = ut.replace("day", "d")
    ut = ut.replace("hours", "h")
    ut = ut.replace("hour", "h")
    ut = ut.replace("minutes", "m")
    ut = ut.replace("minute", "m")
    return ut.strip()

# Returns the CPU load average.
def get_loadavg():
    return run_command(["cat", "/proc/loadavg"]).replace("\n", "")

# Returns the memory info as a dictionary.
def get_mem_info():
    memdata = run_command(["cat", "/proc/meminfo"]).split("\n")
    memdict = {}
    for line in memdata:
        pieces = line.strip().split(":")
        key = pieces[0].strip()
        value = " ".join(pieces[1:]).strip()
        memdict[key] = value
    return memdict

# Returns a list of running processes.
def get_process_count():
    procs = run_command(["ps", "-aux"]).split("\n")
    return len(procs)


# ================================ Messaging ================================= #
# Sends a summary of the system.
def summarize(service, message, args):
    msg = "<b>System Summary</b>\n\n"

    # system uptime
    ut = get_uptime()
    msg += "• <b>Uptime:</b> %s\n" % ut

    # system loadavg
    la = get_loadavg()
    msg += "• <b>Load Average:</b> %s\n" % la

    # memory information (in kB)
    mem = get_mem_info()
    mem_total = int(re.sub("\D", "", mem["MemTotal"]).strip())
    mem_available = int(re.sub("\D", "", mem["MemAvailable"]).strip())
    mem_used = mem_total - mem_available
    mem_percent = float(mem_used) / float(mem_total)
    msg += "• <b>Memory Usage:</b> %.2f GB / %.2f GB (%.2f%%)\n" % \
           (float(mem_used) / 1000000.0,
            float(mem_total) / 1000000.0,
            mem_percent * 100.0)

    # process count
    pcount = get_process_count()
    msg += "• <b>Process Count:</b> %d\n" % pcount

    # send the message
    service.send_message(message.chat.id, msg, parse_mode="HTML")


# ================================= Services ================================= #
# Sends a report of DImROD's Python services.
def report_services(service, message, args):
    msg = "<b>DImROD Service Status</b>\n\n"
    status = run_command(["systemctl", "status", "dimrod_sm.service"]).split("\n")
    i = 0
    for i in range(len(status)):
        line = status[i].strip()
        pieces = line.split(":")

        metric = pieces[0]
        value = " ".join(pieces[1:]).strip()

        # get current status
        if metric == "Active":
            msg += "• <b>Status:</b> %s\n" % value

        # get tasks
        if metric == "Tasks":
            msg += "• <b>Tasks:</b> %s\n" % value

        # get memory usage
        if metric == "Memory":
            msg += "• <b>Memory Usage</b>: %s\n" % value

        # break when we reach 'CGroup'
        if metric == "CGroup":
            break

    # now, read through the active processes
    msg += "\n<b>Active Processes</b>\n"
    i += 1
    for i in range(i, len(status)):
        line = status[i].strip()
        pieces = line.split(" ")
        
        # break when we see an empty line
        if len(line) == 0:
            break
    
        # find a few key pieces and add them to the messsage
        prog = os.path.basename(pieces[1]).lower()
        file = os.path.basename(pieces[2]).lower()
        name = file.split(".")[0]
        if prog == "python3":
            msg += "• <code>%s</code> is running\n" % name

    # send the message
    service.send_message(message.chat.id, msg, parse_mode="HTML")

# Restarts the dimrod service daemon.
def restart_services(service, message, args):
    service.send_message(message.chat.id,
                         "I'll try to restart DImROD service daemon. "
                         "Try to message me in a minute.")
    run_command(["systemctl", "restart", "dimrod_sm.service"])

# Main handler for the 'services' sub-command.
def subcmd_services(service, message, args):
    # if not subcommand was given, send a summary
    if len(args) == 2:
        return report_services(service, message, args)

    # otherwise, look for subcommands
    subcmd = args[2].strip().lower()
    if subcmd in ["restart", "reboot"]:
        return restart_services(service, message, args)


# =================================== Main =================================== #
# Main function.
def command_system(service, message, args: list):
    # if no arguments were given, show a summary
    if len(args) == 1:
        return summarize(service, message, args)

    # look for the sub-command
    subcmd = args[1].strip().lower()
    if subcmd in ["services", "service", "python"]:
        return subcmd_services(service, message, args)

    # otherwise, complain and return
    service.send_message(message.chat.id, "Sorry, I'm not sure what you meant.")
    return

