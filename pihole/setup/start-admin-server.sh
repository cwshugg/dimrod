#!/bin/bash
# Simple script that starts/restarts the pihole service.

if [ -z "$(pgrep lighttpd 2> /dev/null)" ]; then
    sudo service lighttpd start
else
    sudo service lighttpd restart
fi

