# This module defines a class that represents a command for the DImROD bot.

class TelegramCommand:
    # Constructor. Takes in all necessary fields to define a command.
    def __init__(self, keywords: list, description: str, handler):
        self.keywords = keywords
        self.description = description
        self.handler = handler
    
    # Takes in a list of string arguments and runs the command's handler.
    # Returns the handler's return value.
    def run(self, message: dict, args: list):
        return self.handler(message, args)

