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
    msg = "<b>Machine Status</b>\n\n"

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
# Gets and returns the names of currently-active DImROD services.
def get_services():
    lines = run_command(["systemctl", "list-units", "--type=service", "--all"]).split("\n")
    names = []
    for sdata in lines:
        pieces = sdata.split()
        # skip any malformed lines
        if len(pieces) < 3:
            continue
       
        # if the first piece contains some sort of character to indicate the
        # service has failed or isn't active, remove it
        if len(re.findall("[a-zA-Z]+", pieces[0])) == 0:
            pieces.pop(0)

        # skip any services that don't include "dimrod" in the name
        name = pieces[0].strip().lower()
        if "dimrod" not in name:
            continue
        names.append(name)
    return names

# Attempts to find an active DImROD service with the given name.
def find_service(name: str):
    n = name.strip().lower()
    # search for any service name that contains the given name
    for s in get_services():
        if n in s:
            return s
    return None

# Sends a report of DImROD's Python services.
def report_services(service, message, args):
    msg = "<b>DImROD Service Status</b>\n\n"
    
    # get a list of all active services
    names = get_services()
    names_len = len(names)
        
    # report the number of discovered services and list them
    msg += "There are %d service%s running.\n" % \
           (names_len, "" if names_len == 1 else "s")
    for name in names:
        n = name.replace("dimrod_", "").replace(".service", "")
        msg += "• %s\n" % n

    # send the message
    service.send_message(message.chat.id, msg, parse_mode="HTML")

# Reports information on a specific DImROD service.
def report_service(service, message, args, name):
    name_short = name.replace("dimrod_", "").replace(".service", "")
    msg = "<b>%s Status</b>\n\n" % name_short.title()
    
    # get the service's status
    status = run_command(["systemctl", "status", name]).split("\n")
    i = 0
    st = ""
    for i in range(len(status)):
        line = status[i].strip()
        pieces = line.split(":")

        metric = pieces[0]
        value = " ".join(pieces[1:]).strip()

        # get current status
        if metric == "Active":
            msg += "• <b>Status:</b> %s\n" % value
            st = value.split()[0].strip().lower()

        # get tasks
        if metric == "Tasks":
            msg += "• <b>Tasks:</b> %s\n" % value

        # get memory usage
        if metric == "Memory":
            msg += "• <b>Memory Usage</b>: %s\n" % value

        # break when we reach 'CGroup' or an empty line
        if metric == "CGroup" or len(line) == 0:
            break

    # now, read through the active processes, if the process is alive
    if st == "active":
        i += 1
        procs = ""
        for i in range(i, len(status)):
            line = status[i].strip()
            pieces = line.split(" ")
            
            # break when we see an empty line
            if len(line) == 0:
                break
        
            # find a few key pieces and add them to the messsage
            prog = os.path.basename(pieces[1]).lower()
            file = os.path.basename(pieces[2]).lower()
            procs += "• <code>%s %s</code>\n" % (prog, file)
        if len(procs) > 0:
            msg += "\n<b>Active Processes</b>\n"
            msg += procs


    # at this point we've reached the lines containing the latest log output
    # from the service. Print each one to give insight into any potential
    # failures
    i += 1
    output = ""
    for i in range(i, len(status)):
        # replace any < or > characters with other brackets so we don't
        # confuse telegram's HTML parser
        line = status[i].strip().replace("<", "[").replace(">", "]")
        output += "%s\n" % line
    if len(output) > 0:
        msg += "\n<b>Latest Output</b>\n"
        msg += "<code>%s</code>" % output

    # send the message
    service.send_message(message.chat.id, msg, parse_mode="HTML")

# Restarts the dimrod service daemon.
def restart_service(service, message, args, name):
    name_short = name.replace("dimrod_", "").replace(".service", "")
    service.send_message(message.chat.id,
                         "I'll try to restart %s. "
                         "Try checking the status in a minute." % name_short)
    run_command(["systemctl", "restart", name])

# Main handler for the 'services' sub-command.
def subcmd_services(service, message, args):
    # if not subcommand was given, send a summary
    if len(args) == 2:
        return report_services(service, message, args)

    # look for a service name and attempt to report on it if a third
    # argument is given
    if len(args) >= 3:
        s = find_service(args[2])
        if s is None:
            service.send_message(message.chat.id,
                                 "I couldn't find a service named \"%s\" as a service name. "
                                 "Perhaps it was never installed, or you entered the wrong name." %
                                 args[2])
            return

    # if no other arguments are given, show the service's status
    if len(args) == 3:
        return report_service(service, message, args, s)

    # otherwise, look for subcommands for the service
    subcmd = args[3].strip().lower()
    if subcmd in ["restart", "reboot"]:
        return restart_service(service, message, args, s)


# =================================== Main =================================== #
# Main function.
def command_system(service, message, args: list):
    # if no arguments were given, show a summary
    if len(args) == 1:
        return summarize(service, message, args)

    # look for the sub-command
    subcmd = args[1].strip().lower()
    if subcmd in ["services", "service", "serv", "srv", "svc", "python"]:
        return subcmd_services(service, message, args)

    # otherwise, complain and return
    service.send_message(message.chat.id, "Sorry, I'm not sure what you meant.")
    return

