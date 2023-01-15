# This module implements an interface for sending email. Useful for notifying
# users of occurrences and events the server has carried out.
#
#   Connor Shugg

# Imports
import os
import sys
import threading

# Enable import from the parent directory
pdir = os.path.dirname(os.path.dirname(os.path.realpath(__file__)))
if pdir not in sys.path:
    sys.path.append(pdir)

# Local imports
from lib.config import Config, ConfigField
from lib.ifttt import WebhookConfig, Webhook


# =============================== Config Class =============================== #
# Configuration for the messenger.
class MessengerConfig(Config):
    def __init__(self):
        super().__init__()
        self.fields = [
            ConfigField("messenger_webhook_event",    [str],      required=True)
        ]


# ============================= Messenger Class ============================== #
# Main messenger class.
class Messenger:
    # Constructor.
    def __init__(self, config):
        self.config = config
        # set up a IFTTT webhook object
        wbconf = WebhookConfig()
        wbconf.parse_json(config.to_json())
        self.webhooker = Webhook(wbconf)
    
    # Takes in an email address, a subject string, and a content string, and
    # sends an email.
    def send(self, email: str, subject: str, content: str):
        email_data = {
            "to": email,
            "subject": subject,
            "content": content
        }
        self.webhooker.send(self.config.messenger_webhook_event,
                            email_data)

