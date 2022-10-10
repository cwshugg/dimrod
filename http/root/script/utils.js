// Utility module that defines helper classes/functions.

// URL class. Used to easily represent URLs.
class URL
{
    // Constructor. Takes in the address, port, and endpoint to form the full
    // URL.
    constructor(address, port, endpoint)
    {
        this.address = address;
        this.port = port;
        this.endpoint = endpoint;
    }

    // Creates a full URL string and returns it.
    get_string()
    {
        return "http://" +
               this.address + ":" +
               this.port + "/" +
               this.endpoint;
    }
}

