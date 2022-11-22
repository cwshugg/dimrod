// Defines a number of functions specific to the Lumen service.

// Lumen class
class Lumen
{
    // Constructor. Takes in the underlying Service object.
    constructor(service)
    {
        this.service = service;
        this.lights = [];
    }
    
    // Pings the Lumen service and returns a JSON array containing information
    // about all of the lights supported by Lumen.
    // Alternatively, if pinging Lumen fails, an error is thrown.
    async get_lights()
    {
        // build a URL and send a request to lumen
        const url = new URL(this.service.url.address,
                            this.service.url.port,
                            "/lights");
        let resp = await url.send_request("GET", null);
        if (resp.status != 200)
        { throw new Error("Lumen responded with status code " + resp.status + "."); }
        
        // parse the JSON out and determine if the request succeeded
        const jdata = await resp.json();
        if (!jdata.success)
        {
            // construct a message and throw an error on failure
            let message = "Failed to retrieve lights from Lumen";
            if (jdata.message)
            { message += ": " + jdata.message; }
            else
            { message += "."; }
            throw new Error(message);
        }

        // return the response payload
        this.lights = jdata.payload;
        return this.lights;
    }
    
    // Takes in a light ID and searches for a light within Lumen's internal
    // "light" array. Only works if 'get_lights()' was called prior.
    search_light(lid)
    {
        for (let i = 0; i < this.lights.length; i++)
        {
            if (this.lights[i].id == lid)
            { return this.lights[i]; }
        }
        return null;
    }

    // Takes in a light ID, color, and brightness value and attempts to turn
    // the light on by communicating with Lumen.
    async turn_on(lid, color, brightness)
    {
        // build a URL to ping
        const url = new URL(this.service.url.address,
                            this.service.url.port,
                            "/toggle");
        
        // construct a JSON payload to send
        const jdata = {"id": lid, "action": "on"}
        if (color)
        { jdata["color"] = color; }
        if (brightness !== null)
        { jdata["brightness"] = brightness; }

        // send the request and check for a non-200 response
        const resp = await url.send_request("POST", jdata);
        const rdata = await resp.json();
        if (resp.status != 200 || !rdata.success)
        {
            let message = "Failed to turn the light on (" + resp.status + ")";
            if (rdata.message)
            { message += ": " + rdata.message; }
            else
            { message += "."; }
            throw new Error(message);
        }
    }
    
    // Takes in a light ID and attempts to turn the light off by communicating
    // with Lumen.
    async turn_off(lid)
    {
        // build a URL to ping
        const url = new URL(this.service.url.address,
                            this.service.url.port,
                            "/toggle");
        
        // construct a JSON payload to send
        const jdata = {"id": lid, "action": "off"}

        // send the request and check for a non-200 response
        const resp = await url.send_request("POST", jdata);
        const rdata = await resp.json();
        if (resp.status != 200 || !rdata.success)
        {
            let message = "Failed to turn the light off (" + resp.status + ")";
            if (rdata.message)
            { message += ": " + rdata.message; }
            else
            { message += "."; }
            throw new Error(message);
        }
    }
}

