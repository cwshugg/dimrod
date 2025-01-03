# This module defines a class that represents a command for the DImROD bot.

class TelegramCommand:
    prefix = "/"

    # Constructor. Takes in all necessary fields to define a command.
    def __init__(self, keywords: list, description: str, handler, secret=False):
        self.keywords = keywords
        self.description = description
        self.handler = handler
        self.secret = secret
    
    # Takes in the first argument of a telegram message and determines if it
    # matches the command's keywords.
    def match(self, text: str):
        text = text.replace("/", "").strip().lower()
        return text in self.keywords
    
    # Takes in a list of string arguments and runs the command's handler.
    # Returns the handler's return value.
    def run(self, service, message, args: list):
        return self.handler(service, message, args)

