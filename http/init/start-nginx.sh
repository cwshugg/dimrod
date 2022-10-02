#!/bin/bash
# Super simple script that restarts the nginx service.

# look for existing nginx processes. Start nginx if none exist. Otherwise,
# restart the existing service
pids="$(pgrep nginx)"
if [ -z "${pids}" ]; then
    sudo service nginx start
else
    sudo service nginx restart
fi

# retrieve and display the status
sudo service nginx status

