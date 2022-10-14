# Module that defines classes used to represent a single home light that lumen
# can interact with.

# Imports
import json


# ================================== Lights ================================== #
# Class that represents a single light. The light has an identifier and a number
# of flags that 
class Light:
    # Constructor. Takes in the light's ID and a number of flags indicating if
    # special features are present.
    def __init__(self, lid, has_color, has_brightness):
        self.lid = lid
        self.has_color = has_color
        self.has_brightness = has_brightness
    
    # Converts the current Light object into a JSON/dictionary and returns it.
    def to_json(self):
        # TODO
        # NOTE - IDEA: could you make a "JSONSerializable" class that small
        #        classes like this could inherit that automatically take care
        #        of the 'to_json()' and 'from_json()' functions?
        #        That would be super handy.
        pass

    # Attempts to parse a dictionary/JSON object as a light. Returns a Light
    # object on success, and throws an exception on a failure.
    @staticmethod
    def from_json(jdata):
        # TODO
        pass
    
