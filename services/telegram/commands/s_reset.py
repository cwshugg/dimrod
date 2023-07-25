# Implements the /_reset secret bot command.

# Imports
import os
import sys

# Enable import from the parent directory
pdir = os.path.dirname(os.path.dirname(os.path.dirname(__file__)))
if pdir not in sys.path:
    sys.path.append(pdir)

# Main function.
def command_s_reset(service, message, args: list):
    # look up the chat ID in the service's dictionary and delete the entry if
    # it exists
    chat_id = str(message.chat.id)
    if chat_id in service.chat_conversations:
        service.chat_conversations.pop(chat_id)
    
    # indicate the conversation has been reset for this chat
    service.send_message(message.chat.id, "Conversation reset.\n")

