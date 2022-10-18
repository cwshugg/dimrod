// Defines a URL class, used to easily represent URLs.

class URL
{
    // Constructor. Takes in the address, port, and endpoint to form the full
    // URL.
    constructor(address, port, endpoint)
    {
        this.address = address;
        this.port = port;
        this.endpoint = endpoint;

        // other internal settings
        this.no_cors = false;
    }

    // Creates a full URL string and returns it.
    get_string()
    {
        // make sure only one slash is included
        let slash = "/";
        if (this.endpoint.startsWith("/"))
        { slash = ""; }

        return "http://" +
               this.address + ":" +
               this.port + slash +
               this.endpoint;
    }
    
    // Sends a HTTP request to the URL, given the HTTP method and any JSON data
    // to be sent to the remote server.
    async send_request(method, jdata)
    {
        // build a JSON object of settings to pass to fetch()
        const fetch_data = { method: method };
        if (jdata)
        { fetch_data.body = JSON.stringify(jdata); }
        if (this.no_cors)
        { fetch_data.mode = "no-cors"; }

        // send a request to the URL
        const urlstr = this.get_string();
        return await fetch(urlstr, fetch_data);
    }
}

