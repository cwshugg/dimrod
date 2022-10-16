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
        // build a request body string, if JSON data was given
        let request_body = null;
        if (jdata)
        { request_body = JSON.stringify(jdata); }
    
        // send a request to the correct server endpoint
        let response = null;
        const urlstr = this.get_string();
        if (jdata == null)
        {
            response = await fetch(urlstr, {
                mode: "no-cors",
                method: method
            });
        }
        else
        {
            response = await fetch(urlstr, {
                mode: "no-cors",
                method: method,
                body: request_body
            });
        }
    
        // retrieve the response body and attempt to parse it as JSON
        return response;
    }

}

